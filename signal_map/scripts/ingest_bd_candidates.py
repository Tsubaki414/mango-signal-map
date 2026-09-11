"""外部候选名单入库 —— 去重接到已有身份记录上，附带说法一律标未核验。

这个脚本**不产生候选池**。候选池是 ``bd_discovery.build_views`` 每次现算出来
的。这里做的是三件很有限的事：

1. 把一份外部名单里的账号，按 ``(platform, handle)`` 接到已有的 ``Creator``
   上。同一个人出现在三份名单里，仍然只有一条身份记录 —— 名单只是又一次
   「有人提到过他」。
2. 把名单自己的评分、分层、描述**原样**存进 ``source_*`` 列，并永久标记
   ``source_claims_status='unverified'``。筛选逻辑不读这些列。
3. 名单里字面写着的邮箱和合作入口，记成 ``CommercialSignal``，同样未核验，
   带上原文和出处。

为什么不为新账号建 Creator
--------------------------
一条 ``Creator`` 是「Mango 认识这个人、可以向他采购」的记录。一份外部名单
只说明有人推荐过他。真正建立身份记录发生在有人**开始建联**的那一刻，由
``/api/internal/bd/candidates/{platform}/{handle}/contacts`` 或 ``/quotes``
触发。这样 ``creators`` 表里就不会堆满从没联系过的名字，而
``creators_without_quote`` 这类查询也不会被外部名单稀释。

支持的来源::

    python -m signal_map.scripts.ingest_bd_candidates --source ilands_html \\
        --path ilands_client_review_send_to_engineering_2026-09-03.html
    python -m signal_map.scripts.ingest_bd_candidates --source outreach_workbook \\
        --path outputs/ai_kol_new_outreach_20260906_expanded/head_ai_kol_and_supplier_new_outreach_shortlist.xlsx
    python -m signal_map.scripts.ingest_bd_candidates --all --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.bd_models import BDCandidate, CommercialSignal  # noqa: E402
from signal_map.backend.models import Creator, SocialAccount  # noqa: E402
from signal_map.backend.observation_models import XAccount  # noqa: E402

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s；;，,()（）]+")


@dataclass
class IncomingCandidate:
    """一条外部名单记录，还没有和库里的任何东西对上。"""

    platform: str
    handle: str
    display_name: str | None = None
    profile_url: str | None = None
    object_kind: str = "unknown"
    object_kind_basis: str | None = None
    target_group: str | None = None
    source_ref: str | None = None
    score_raw: str | None = None
    tier_raw: str | None = None
    notes_raw: str | None = None
    #: ``(signal_type, value, evidence_quote)``，字面读出来的，不猜。
    signals: list[tuple[str, str | None, str]] = field(default_factory=list)


# =============================================================================
# 来源解析
# =============================================================================


def parse_ilands_html(path: Path) -> list[IncomingCandidate]:
    """老板给的 iLands 样例：107 个 KOL 池 + 3 组具名目标人物。

    ``pool_tier``（A_paid_reachable…）、``score``（96.03）和 ``ilands_fit``
    全部进 ``source_*``，一个都不进判断。iLands 的项目偏好是**这一个客户的**
    偏好，不是所有客户的固定规则 —— 把 ``ilands_fit`` 读成通用适配度，就是
    把一个客户的口味写死成产品逻辑。
    """
    text = path.read_text(encoding="utf-8")
    out: list[IncomingCandidate] = []

    pool_match = re.search(r"window\.ilandsExpandedKolPool\s*=\s*(\[.*?\n\s*\];)", text, re.S)
    if pool_match:
        for index, row in enumerate(json.loads(pool_match.group(1).rstrip(";"))):
            handle = (row.get("handle") or "").lstrip("@")
            if not handle:
                continue
            notes = "；".join(
                f"{key}={row[key]}"
                for key in (
                    "lane", "ilands_fit", "commercial_state", "risk_tags",
                    "root_visibility_signal", "traffic_signal", "reply_quality_signal",
                    "why_keep", "next_step", "source",
                )
                if row.get(key)
            )
            candidate = IncomingCandidate(
                platform="X",
                handle=handle,
                display_name=row.get("name"),
                profile_url=f"https://x.com/{handle}",
                # 名单说他是可投放 KOL，那是名单的说法。标 unknown，让筛选
                # 自己从数据里判断，并把名单的说法留在 source_notes_raw 里。
                object_kind="unknown",
                object_kind_basis="外部名单列为可投放候选，身份待本系统判定",
                source_ref=f"ilandsExpandedKolPool[{index}]",
                score_raw=row.get("score"),
                tier_raw=row.get("pool_tier"),
                notes_raw=notes or None,
            )
            candidate.signals = _signals_from_text(
                row.get("bio"), source=f"iLands sample bio: {handle}"
            )
            out.append(candidate)

    root_match = re.search(r"const rootAudienceGroups\s*=\s*(\[.*?\n\s*\];)", text, re.S)
    if root_match:
        out.extend(_parse_root_groups(root_match.group(1)))
    return out


#: 目标人物块是 JS 对象字面量，不是 JSON（键没有引号），所以按字段抓而不是
#: 硬套 json.loads。宁可漏一条也不要把一份名单解析成半份还不报错。
_ROOT_GROUP_RE = re.compile(r'name:\s*"([^"]+)"\s*,\s*\n\s*count:', re.S)
_ROOT_PERSON_RE = re.compile(
    r'\{\s*name:\s*"(?P<name>[^"]*)",\s*handle:\s*"(?P<handle>[^"]*)",\s*'
    r'role:\s*"(?P<role>[^"]*)",\s*why:\s*"(?P<why>[^"]*)",\s*'
    r'behavior:\s*"(?P<behavior>[^"]*)",\s*evidence:\s*"(?P<evidence>[^"]*)"',
    re.S,
)


def _parse_root_groups(source: str) -> list[IncomingCandidate]:
    """三组具名目标人物。标成 ``target_person``，永远不进询价队列。"""
    boundaries = [(m.start(), m.group(1)) for m in _ROOT_GROUP_RE.finditer(source)]

    def group_for(position: int) -> str | None:
        current = None
        for start, name in boundaries:
            if start <= position:
                current = name
            else:
                break
        return current

    out: list[IncomingCandidate] = []
    for match in _ROOT_PERSON_RE.finditer(source):
        handle = match.group("handle").lstrip("@")
        if not handle:
            continue
        out.append(IncomingCandidate(
            platform="X",
            handle=handle,
            display_name=match.group("name"),
            profile_url=f"https://x.com/{handle}",
            object_kind="target_person",
            object_kind_basis="外部名单列为目标人群（root），不是投放对象",
            target_group=group_for(match.start()),
            source_ref=f"rootAudienceGroups:{handle}",
            notes_raw="；".join(
                f"{key}={match.group(key)}"
                for key in ("role", "why", "behavior", "evidence")
                if match.group(key)
            ),
        ))
    return out


def parse_outreach_workbook(path: Path) -> list[IncomingCandidate]:
    """建联工作表：``AI KOL 新建联`` + ``供应商新建联``。

    表里的 S/A 优先级和备注都是别人写的判断，同样只进 ``source_*``。
    """
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=False)
    out: list[IncomingCandidate] = []

    if "AI KOL 新建联" in workbook.sheetnames:
        sheet = workbook["AI KOL 新建联"]
        for index, row in enumerate(sheet.iter_rows(min_row=8, values_only=True), start=8):
            platform, account, url, _followers, _quote, contact, _followup, notes = row[:8]
            if not account:
                continue
            handle = _handle_from(str(account), str(url or ""))
            if not handle:
                continue
            tier = None
            if notes:
                head = str(notes).strip()[:1]
                tier = head if head in {"S", "A", "B", "C"} else None
            candidate = IncomingCandidate(
                platform=str(platform or "YouTube"),
                handle=handle,
                display_name=str(account).split("（")[0].strip(),
                profile_url=str(url) if url else None,
                object_kind="unknown",
                object_kind_basis="外部建联名单列为可投放候选，身份待本系统判定",
                source_ref=f"AI KOL 新建联!row{index}",
                tier_raw=tier,
                notes_raw=str(notes) if notes else None,
            )
            candidate.signals = _signals_from_text(
                str(contact) if contact else None,
                source=f"新建联工作表 row{index} 联系方式列",
            )
            out.append(candidate)

    if "供应商新建联" in workbook.sheetnames:
        sheet = workbook["供应商新建联"]
        for index, row in enumerate(sheet.iter_rows(min_row=8, values_only=True), start=8):
            # 这张表在机构清单下面还接了一块「供应商关联 KOL 明细」，列位刚好
            # 也对得上（A 列是机构名、C 列是平台），会被当成 22 个额外的机构
            # 读进来。两块之间隔着空行，所以碰到空行就停 —— 明细块里的 KOL
            # 由上面那张 KOL 表负责，不该在这里再出现一次。
            if not any(row[:7]):
                break
            priority, supplier, kind, _n, _kols, contact, homepage = row[:7]
            if not supplier or not kind:
                continue
            candidate = IncomingCandidate(
                platform="Org",
                handle=_slug(str(supplier)),
                display_name=str(supplier),
                profile_url=str(homepage) if homepage else None,
                object_kind="supplier",
                object_kind_basis="外部名单列为机构/代理入口",
                source_ref=f"供应商新建联!row{index}",
                tier_raw=str(priority) if priority else None,
                notes_raw="；".join(str(x) for x in row[2:] if x),
            )
            candidate.signals = _signals_from_text(
                str(contact) if contact else None,
                source=f"供应商工作表 row{index} 联系方式列",
            )
            out.append(candidate)
    return out


def _signals_from_text(text: str | None, *, source: str) -> list[tuple[str, str | None, str]]:
    """只收**字面出现**的邮箱和链接。

    这里不做任何措辞推断 —— 名单里的「已确认商务邮箱」是别人的判断，不是我们
    读到的事实。规格明确不猜邮箱，所以这里也不拼域名、不补 ``info@``。
    """
    if not text:
        return []
    found: list[tuple[str, str | None, str]] = []
    for email in dict.fromkeys(_EMAIL_RE.findall(text)):
        found.append(("business_email", email, f"{source}：{text[:200]}"))
    for url in dict.fromkeys(_URL_RE.findall(text)):
        found.append(("booking_form", url, f"{source}：{text[:200]}"))
    return found


def _handle_from(account: str, url: str) -> str | None:
    match = re.search(r"[（(]@([^）)]+)[）)]", account)
    if match:
        return match.group(1).strip().lstrip("@")
    match = re.search(r"(?:youtube\.com/|x\.com/|twitter\.com/)@?([A-Za-z0-9_\-.]+)", url)
    if match:
        return match.group(1)
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


SOURCES = {
    "ilands_html": (
        parse_ilands_html,
        REPO_ROOT / "ilands_client_review_send_to_engineering_2026-09-03.html",
    ),
    "outreach_workbook": (
        parse_outreach_workbook,
        REPO_ROOT / "outputs" / "ai_kol_new_outreach_20260906_expanded"
        / "head_ai_kol_and_supplier_new_outreach_shortlist.xlsx",
    ),
}


# =============================================================================
# 去重与写入
# =============================================================================


def resolve_creator(session: Session, platform: str, handle: str) -> Creator | None:
    """这个账号在库里已经有身份记录了吗。

    三条路：同平台的社交账号、``primary_handle``、以及观察层 ``XAccount``
    已经接上的 creator。三条都试是必要的 —— 同一个人在 BD 时期可能只留了
    handle，在观察层只留了 rest_id。
    """
    cleaned = handle.lstrip("@")
    account = session.scalar(
        select(SocialAccount).where(
            func.lower(SocialAccount.handle) == cleaned.lower(),
            SocialAccount.platform == platform,
        )
    )
    if account is None:
        account = session.scalar(
            select(SocialAccount).where(func.lower(SocialAccount.handle) == cleaned.lower())
        )
    if account is not None:
        return session.get(Creator, account.creator_id)

    creator = session.scalar(
        select(Creator).where(func.lower(Creator.primary_handle) == cleaned.lower())
    )
    if creator is not None:
        return creator

    x_account = session.scalar(
        select(XAccount).where(
            func.lower(XAccount.handle) == cleaned.lower(), XAccount.creator_id.is_not(None)
        )
    )
    if x_account is not None:
        return session.get(Creator, x_account.creator_id)
    return None


def ingest(
    session: Session, source_name: str, rows: list[IncomingCandidate], *, dry_run: bool = False,
) -> dict:
    stats = {
        "seen": len(rows), "new": 0, "updated": 0,
        "linked_to_creator": 0, "linked_to_observed": 0,
        "signals_found": sum(len(row.signals) for row in rows),
        "signals_added": 0, "already_priced": 0,
    }
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    for row in rows:
        handle = row.handle.lstrip("@").lower()
        candidate = session.scalar(
            select(BDCandidate).where(
                BDCandidate.platform == row.platform, BDCandidate.handle == handle
            )
        )
        creator = resolve_creator(session, row.platform, handle) if row.platform != "Org" else None
        if creator is not None:
            stats["linked_to_creator"] += 1
            if any(q.amount_usd is not None for q in creator.quotes):
                # 已有报价：复用原记录，本轮不需要再询价。
                stats["already_priced"] += 1

        observed = session.scalar(
            select(XAccount).where(func.lower(XAccount.handle) == handle)
        )
        if observed is not None:
            stats["linked_to_observed"] += 1

        if candidate is None:
            stats["new"] += 1
            candidate = BDCandidate(
                platform=row.platform, handle=handle, source_name=source_name,
                first_ingested_at=now,
            )
            if not dry_run:
                session.add(candidate)
        else:
            stats["updated"] += 1

        candidate.creator_id = creator.id if creator else None
        candidate.org_name = row.display_name if row.platform == "Org" else None
        candidate.display_name = row.display_name or candidate.display_name
        candidate.profile_url = row.profile_url or candidate.profile_url
        candidate.target_group = row.target_group or candidate.target_group
        # 人工改过身份判定就不覆盖 —— 脚本重跑不该抹掉人的判断。
        if candidate.object_kind in ("unknown", None) or candidate.object_kind_basis is None:
            candidate.object_kind = row.object_kind
            candidate.object_kind_basis = row.object_kind_basis
        candidate.source_name = source_name
        candidate.source_ref = row.source_ref
        candidate.source_score_raw = row.score_raw
        candidate.source_tier_raw = row.tier_raw
        candidate.source_notes_raw = row.notes_raw
        # 永远是 unverified：这是外部名单的说法，不是我们核验过的结论。
        candidate.source_claims_status = "unverified"
        candidate.last_seen_in_source_at = now

        if not dry_run:
            session.flush()
            stats["signals_added"] += _add_signals(session, candidate, creator, row, source_name)

    if not dry_run:
        session.commit()
    return stats


def _add_signals(
    session: Session,
    candidate: BDCandidate,
    creator: Creator | None,
    row: IncomingCandidate,
    source_name: str,
) -> int:
    """写入名单里字面读到的联系入口，去重后追加。

    事实挂在 ``creator`` 上（这是关于这个人的事实），没有 creator 才退回挂在
    候选行上。全部 ``verified_at=None`` —— 名单说有，不等于我们确认可达。
    """
    added = 0
    for signal_type, value, quote in row.signals:
        exists = session.scalar(
            select(CommercialSignal).where(
                CommercialSignal.signal_type == signal_type,
                CommercialSignal.value == value,
                (
                    CommercialSignal.creator_id == creator.id if creator
                    else CommercialSignal.bd_candidate_id == candidate.id
                ),
            )
        )
        if exists is not None:
            continue
        session.add(CommercialSignal(
            creator_id=creator.id if creator else None,
            bd_candidate_id=None if creator else candidate.id,
            signal_type=signal_type,
            value=value,
            evidence_quote=quote,
            observed_at=dt.date.today(),
            verified_at=None,
            fact_status="research_lead",
            is_inferred=False,
            notes="来自外部名单，未核验是否可达、是否用于商务",
            source_system=f"list:{source_name}",
            source_ref=row.source_ref,
        ))
        added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCES))
    parser.add_argument("--path", type=Path)
    parser.add_argument("--all", action="store_true", help="ingest every known source")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.all and not args.source:
        parser.error("pass --source NAME or --all")

    names = sorted(SOURCES) if args.all else [args.source]
    session = sm_db.get_session()
    try:
        for name in names:
            parse, default_path = SOURCES[name]
            path = args.path if (args.path and not args.all) else default_path
            if not path.exists():
                print(f"{name}: skipped, file not found: {path}")
                continue
            rows = parse(path)
            stats = ingest(session, name, rows, dry_run=args.dry_run)
            print(f"\n=== {name} ===")
            print(f"file: {path}")
            for key, value in stats.items():
                print(f"  {key:22} {value}")
    finally:
        session.close()

    if args.dry_run:
        print("\n[DRY RUN] nothing written")
    else:
        print(
            "\n提示：名单只是「有人提到过他」。评分与分层已存为 unverified，"
            "不参与任何判断；候选池仍由 /api/internal/bd/screen 现算。"
        )


if __name__ == "__main__":
    main()
