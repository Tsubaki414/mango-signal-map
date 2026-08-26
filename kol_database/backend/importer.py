"""Import already-quoted creators from KOL_data/*.csv|xlsx into the DB.

Scope, per product spec: only rows that already carry a quote. Two source
files are supported today:

- KOL_data/建联名单总表 - Fiona-英文AI KOL copy.csv
    Outreach master list. Column `有报价` == "1" marks a quoted row.
    Every quoted row in this file links to x.com, so platform is fixed to X.
- KOL_data/KOL回复报价表.xlsx (sheet "回复报价")
    Replies that already contain a quote (every row has one), spanning
    several platforms (YouTube, Instagram, X, TikTok, ...).

Re-running the import is idempotent: creators are matched and merged by
(platform, handle) extracted from the profile URL, so a second run updates
rather than duplicates rows. Manual classification edits are never touched
here (see models.creator_class_locked).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import openpyxl
from sqlalchemy.orm import Session

from .fx import to_usd
from .models import ContactMethod, Creator, RateCard, SocialAccount
from .parsing import followers_annotation, normalize_text, parse_followers, parse_quote_text

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
# A handful of profile_url cells aren't a clean URL -- they're a label plus
# a URL plus a trailing note glued on with no space, e.g.
# "TikTok: https://www.tiktok.com/@niklas_volland（另有IG，邮件自述266K...)".
# Stop at whitespace or common trailing punctuation/parens (incl. full-width)
# so urlparse() never sees the label prefix or the glued-on note as part of
# the URL (which previously produced garbage like scheme="tiktok" or a
# handle of "https:").
URL_RE = re.compile(r"https?://[^\s　（）()，,；;、]+")


PLATFORM_DOMAINS = {
    "x.com": "X",
    "twitter.com": "X",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "tiktok.com": "TikTok",
    "linkedin.com": "LinkedIn",
    "threads.net": "Threads",
    "facebook.com": "Facebook",
}

# A schemeless cell like "x.com/heyDhavall" (no "https://") would otherwise
# make urlparse() treat the whole string as a bare path, extracting
# "x.com" itself as the handle instead of "heyDhavall". Only prepend a
# scheme for cells that start with one of our known social domains, to
# avoid misinterpreting unrelated text as a URL.
BARE_DOMAIN_RE = re.compile(
    r"^(?:" + "|".join(re.escape(d) for d in PLATFORM_DOMAINS) + r")(?:/\S*)?$"
)

# Free-text platform labels in the source sheets are inconsistent ("X",
# "X(Twitter)", "YouTube Shorts", "未在邮件中注明", long multi-platform
# descriptions). This maps every observed variant to one of a small set of
# clean, standardized platform names so filters/badges don't fragment.
PLATFORM_ALIASES = {
    "x": "X",
    "twitter": "X",
    "x(twitter)": "X",
    "x (twitter)": "X",
    "youtube": "YouTube",
    "youtube shorts": "YouTube",
    "instagram": "Instagram",
    "ig": "Instagram",
    "tiktok": "TikTok",
    "linkedin": "LinkedIn",
    "threads": "Threads",
    "facebook": "Facebook",
}

NOT_SPECIFIED_MARKERS = ("未在邮件中", "未注明", "未指定", "not specified", "tbd", "unspecified")
MULTI_PLATFORM_MARKERS = ("多平台", "multi-platform", "multi platform")


def _extract_url(text: str | None) -> str | None:
    if not text:
        return None
    match = URL_RE.search(text)
    if match:
        return match.group(0)
    stripped = text.strip()
    if BARE_DOMAIN_RE.match(stripped):
        return "https://" + stripped
    return None


def normalize_platform(raw: str | None) -> str:
    """Collapse messy free-text platform labels into a small canonical set.
    "未在邮件中注明" (not specified in the email) becomes "Unknown", not a
    platform of its own; any of the descriptive "多平台(IG/TikTok/...)"
    strings become "Multi-platform" (the deal genuinely spans several
    platforms as one package -- see importer module docstring)."""
    if not raw or not raw.strip():
        return "Unknown"
    text = raw.strip()
    key = text.lower()
    if key in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[key]
    if any(marker in text or marker in key for marker in NOT_SPECIFIED_MARKERS):
        return "Unknown"
    if any(marker in text or marker in key for marker in MULTI_PLATFORM_MARKERS):
        return "Multi-platform"
    if len(text) <= 20:
        return text
    return "Multi-platform"


@dataclass
class RawRecord:
    """One quoted creator pulled from a source sheet, pre-DB-write."""

    display_name: str
    platform: str
    platform_raw: str | None
    handle: str | None
    profile_url: str | None
    followers_raw: str | None
    quote_text: str
    contacts: dict[str, str] = field(default_factory=dict)  # method_type -> value
    internal_notes: str | None = None
    source_file: str = ""


# Path segments where the real identifier is the SEGMENT AFTER this word,
# not this word itself -- e.g. youtube.com/channel/UC.../ must extract the
# UC... id, not the literal word "channel" (a real bug found in production:
# a creator's handle was stored as the string "channel").
_CONTAINER_SEGMENTS = {"channel", "c", "user", "in", "company", "people"}


def _platform_and_handle(url: str | None, platform_hint: str | None = None) -> tuple[str, str | None]:
    """Prefer the profile URL's domain (reliable) over the free-text platform
    label (messy); fall back to normalizing the label only when there's no
    URL to read a domain from."""
    handle = None
    domain_platform = None
    if url and url.strip():
        parsed = urlparse(url.strip())
        domain = parsed.netloc.lower().removeprefix("www.")
        domain_platform = PLATFORM_DOMAINS.get(domain)
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            if path_parts[0].lower() in _CONTAINER_SEGMENTS and len(path_parts) >= 2:
                handle = path_parts[1]
            else:
                handle = path_parts[0].lstrip("@")
    platform = domain_platform or normalize_platform(platform_hint)
    return platform, handle


def load_csv_records(path: Path) -> list[RawRecord]:
    records: list[RawRecord] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("有报价", "").strip() != "1":
                continue
            quote_text = (row.get("报价") or "").strip()
            if not quote_text:
                continue

            url = _extract_url((row.get("主页链接") or "").strip()) or (row.get("主页链接") or "").strip()
            platform, handle = _platform_and_handle(url, platform_hint="X")
            name = (row.get("名称") or "").strip().lstrip("@") or handle or "Unknown"

            contacts: dict[str, str] = {}
            email = (row.get("邮箱") or "").strip()
            if email and EMAIL_RE.match(email):
                contacts["email"] = email
            tg = (row.get("TG") or "").strip()
            if tg and tg not in {"", "\n"}:
                contacts["telegram"] = tg.lstrip("@")
            website = (row.get("个人网页") or "").strip()
            if website and website not in {"", "\n"}:
                contacts["website"] = website
            other = (row.get("其他联系方式") or "").strip()
            if other and other not in {"", "\n", "0", "1"}:
                contacts["other"] = other

            note_bits = []
            for col in ("建联备注", "跟进备注"):
                val = (row.get(col) or "").strip()
                if val and val not in {"\n"}:
                    note_bits.append(f"{col}: {val}")
            if (row.get("需要跟进") or "").strip() == "1":
                note_bits.append("需要跟进: 是")
            followers_raw = (row.get("粉丝数") or "").strip() or None
            annotation = followers_annotation(followers_raw)
            if annotation:
                note_bits.append(f"粉丝数备注: {annotation}")

            records.append(
                RawRecord(
                    display_name=name,
                    platform=platform,
                    platform_raw=None,  # CSV has no separate platform column; every quoted row is x.com
                    handle=handle,
                    profile_url=url or None,
                    followers_raw=followers_raw,
                    quote_text=quote_text,
                    contacts=contacts,
                    internal_notes="; ".join(note_bits) or None,
                    source_file=path.name,
                )
            )
    return records


def load_xlsx_records(path: Path) -> list[RawRecord]:
    records: list[RawRecord] = []
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["回复报价"]
    rows = list(ws.iter_rows(values_only=True))
    for row in rows[1:]:
        if not row or len(row) < 6:
            continue
        real_name, display_name, platform_raw, profile_url, followers_raw, quote_raw = row[:6]
        contact_raw = row[6] if len(row) > 6 else None
        quote_text = str(quote_raw).strip() if quote_raw else ""
        if not quote_text:
            continue

        platform_hint = str(platform_raw).strip() if platform_raw else None
        clean_url = _extract_url(str(profile_url).strip()) if profile_url else None
        platform, handle = _platform_and_handle(clean_url, platform_hint)

        name = str(display_name).strip() if display_name else (str(real_name).strip() if real_name else handle or "Unknown")

        contacts: dict[str, str] = {}
        contact_text = str(contact_raw).strip() if contact_raw else ""
        if contact_text:
            email_match = EMAIL_RE.search(contact_text)
            if email_match:
                contacts["email"] = email_match.group(0)
            else:
                contacts["other"] = contact_text

        followers_raw_str = str(followers_raw).strip() if followers_raw else None
        note_bits = []
        if real_name and real_name != display_name:
            note_bits.append(f"真实姓名: {real_name}")
        annotation = followers_annotation(followers_raw_str)
        if annotation:
            note_bits.append(f"粉丝数备注: {annotation}")
        # normalize_platform() collapses long multi-platform descriptions down
        # to "Multi-platform" -- keep the original wording so nothing is lost.
        if platform == "Multi-platform" and platform_hint and platform_hint != platform:
            note_bits.append(f"平台备注: {platform_hint}")

        records.append(
            RawRecord(
                display_name=name,
                platform=platform,
                platform_raw=platform_hint,
                handle=handle,
                profile_url=clean_url or (str(profile_url).strip() if profile_url else None),
                followers_raw=followers_raw_str,
                quote_text=quote_text,
                contacts=contacts,
                internal_notes="; ".join(note_bits) or None,
                source_file=path.name,
            )
        )
    return records


def _find_existing_social_account(session: Session, platform: str, handle: str | None) -> SocialAccount | None:
    if not handle:
        return None
    return (
        session.query(SocialAccount)
        .filter(SocialAccount.platform == platform, SocialAccount.handle == handle)
        .one_or_none()
    )


def upsert_record(session: Session, record: RawRecord) -> Creator:
    account = _find_existing_social_account(session, record.platform, record.handle)
    if account is not None:
        creator = account.creator
    else:
        creator = Creator(display_name=record.display_name, creator_class="Unknown", creator_class_source="unset")
        session.add(creator)
        session.flush()
        account = SocialAccount(
            creator_id=creator.id,
            platform=record.platform,
            platform_raw=record.platform_raw,
            handle=record.handle,
            profile_url=record.profile_url,
        )
        session.add(account)

    followers = parse_followers(record.followers_raw)
    if followers is not None and account.followers_source != "rapidx":
        account.followers = followers
    if not account.profile_url and record.profile_url:
        account.profile_url = record.profile_url

    if creator.source_files:
        files = set(creator.source_files.split(",")) | {record.source_file}
    else:
        files = {record.source_file}
    creator.source_files = ",".join(sorted(files))

    existing_contact_values = {(c.method_type, c.value) for c in creator.contacts}
    for method_type, value in record.contacts.items():
        if (method_type, value) not in existing_contact_values:
            session.add(ContactMethod(creator_id=creator.id, method_type=method_type, value=value))

    if record.internal_notes:
        if creator.internal_notes and record.internal_notes not in creator.internal_notes:
            creator.internal_notes = f"{creator.internal_notes}\n{record.internal_notes}"
        elif not creator.internal_notes:
            creator.internal_notes = record.internal_notes

    # Rate cards: replace this platform+source's parsed rows so re-imports
    # don't pile up duplicates, but keep rows parsed from other files/platforms.
    session.query(RateCard).filter(
        RateCard.creator_id == creator.id,
        RateCard.platform == record.platform,
        RateCard.notes == f"source:{record.source_file}",
    ).delete()

    parsed = parse_quote_text(normalize_text(record.quote_text), default_currency="USD")
    for item in parsed:
        amount_usd, fx_rate = to_usd(item.amount, item.currency)
        session.add(
            RateCard(
                creator_id=creator.id,
                platform=record.platform,
                deliverable=item.deliverable,
                quote_amount=item.amount,
                quote_amount_min=item.amount_min,
                quote_amount_max=item.amount_max,
                quote_currency=item.currency,
                quote_amount_usd=amount_usd,
                fx_rate_used=fx_rate,
                is_confident=item.is_confident,
                raw_quote_text=record.quote_text,
                notes=f"source:{record.source_file}",
            )
        )

    return creator


def run_import(kol_data_dir: Path, session: Session) -> dict[str, int]:
    csv_path = kol_data_dir / "建联名单总表 - Fiona-英文AI KOL copy.csv"
    xlsx_path = kol_data_dir / "KOL回复报价表.xlsx"

    records: list[RawRecord] = []
    if csv_path.exists():
        records.extend(load_csv_records(csv_path))
    if xlsx_path.exists():
        records.extend(load_xlsx_records(xlsx_path))

    creators_before = session.query(Creator).count()
    seen_creator_ids: set[int] = set()
    for record in records:
        creator = upsert_record(session, record)
        session.flush()
        seen_creator_ids.add(creator.id)
    session.commit()

    return {
        "records_processed": len(records),
        "creators_before": creators_before,
        "creators_after": session.query(Creator).count(),
        "creators_touched": len(seen_creator_ids),
    }
