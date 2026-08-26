"""Best-effort parsers for the messy free-text quote sheets.

The source "报价" column is unstructured natural-language text written by
dozens of different creators/managers (bulleted lists, semicolon lists,
numbered lists, ranges, mixed currencies, "on request" text with no number
at all). We do NOT try to be perfect here: every parsed RateCard keeps the
verbatim `raw_quote_text` alongside it, and any segment we can't confidently
turn into (deliverable, amount) becomes a single is_confident=False row
instead of a guessed number, so nothing is ever silently invented.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .fx import CURRENCY_SYMBOLS, FX_TO_USD

_CURRENCY_WORDS = r"(?:USDT|USDC|USD|RMB|CNY|EUR|GBP|INR)"
_SYMBOL_CLASS = "".join(re.escape(s) for s in CURRENCY_SYMBOLS)

_AMOUNT = r"[\d][\d,]*(?:\.\d+)?"
_MULT = r"[kKmM]?"  # "£6k" = 6,000 GBP -- must not be read as literally £6

# amount with a leading symbol: $500  £1,499  €4,000  £6k
_PREFIX_AMOUNT = re.compile(
    rf"(?P<cur>[{_SYMBOL_CLASS}])\s?(?P<num>{_AMOUNT})(?P<mult>{_MULT})"
    rf"(?:\s*(?:-|–|—|to)\s*(?P<cur2>[{_SYMBOL_CLASS}])?\s?(?P<num2>{_AMOUNT})(?P<mult2>{_MULT}))?"
)
# amount with a trailing symbol/word: 150$  200 USD  100usd  6k$
# (no \b after the currency group: a symbol like $ is non-word, so \b would
# never match when followed by whitespace/end-of-string)
_SUFFIX_AMOUNT = re.compile(
    rf"(?P<num>{_AMOUNT})(?P<mult>{_MULT})\s?(?:(?P<cur>[{_SYMBOL_CLASS}])|(?P<curw>{_CURRENCY_WORDS})\b)"
)


def _apply_mult(value: float, mult: str | None) -> float:
    if mult and mult.lower() == "k":
        return value * 1_000
    if mult and mult.lower() == "m":
        return value * 1_000_000
    return value
# bare number with no currency marker at all, tied to a "per/for/:" price cue
# so we don't misread turnaround times or percentages as prices.
_BARE_PER = re.compile(rf"^(?P<num>{_AMOUNT})\s*(?:per|for|/)\s+(?P<label>.+)$", re.IGNORECASE)
_BARE_TRAILING = re.compile(rf"^(?P<label>.+?)[:：]\s*(?P<num>{_AMOUNT})\s*$")

_LIST_MARKER = re.compile(r"^\s*(?:[•▪●\-\*]|\d+[\.\)]|\d+\s*[×xX]\s)\s*")
_LEADING_CONNECTOR = re.compile(r"^\s*(?:is|at|for|of)\s+", re.IGNORECASE)
_FOR_PER_LABEL = re.compile(
    r"^\s*(?:for|per|/)\s+(?:a\s+|an\s+)?([A-Za-z一-鿿][\w 一-鿿/]{0,60})", re.IGNORECASE
)

_SEGMENT_SPLIT = re.compile(
    r"[;\n。]+"  # semicolon, newline, Chinese period
    r"|(?<=\S)\s*[•▪●]\s*"  # bullets not at line start
    r"|(?<=[a-zA-Z一-鿿\d\)\.,%])\s+(?=\d+\s*[×xX]\s)"  # " 2× Label" quantity markers
    r"|(?<=[a-zA-Z一-鿿])\s+(?=\d+[\.\)]\s+[A-Za-z一-鿿])"  # " 2. Label" numbered markers
    r"|(?<=[\d$€£¥%])(?=[A-Z][a-zA-Z ]{0,30}?[:：])"  # "700$Article:" / "£999Quote repost:" glued items
)


@dataclass
class ParsedDeliverable:
    deliverable: str
    amount: float | None
    amount_min: float | None
    amount_max: float | None
    currency: str
    is_confident: bool
    raw_segment: str


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    replacements = {
        "；": ";",  # full-width semicolon
        "，": ",",
        "：": ":",
        "（": "(",
        "）": ")",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.strip()


def parse_followers(raw: str | int | float | None) -> int | None:
    """Parse a follower-count cell. Only reads the leading number -- some
    cells carry extra annotation after it (e.g. "54,591（10天均浏览1,290...)")
    which must NOT be concatenated into the count."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    if not text:
        return None
    match = re.match(r"^([\d][\d,]*(?:\.\d+)?)\s*([kKmM]?)", text)
    if not match:
        return None
    num, suffix = match.groups()
    value = float(num.replace(",", ""))
    if suffix.lower() == "k":
        value *= 1_000
    elif suffix.lower() == "m":
        value *= 1_000_000
    return int(value)


def followers_annotation(raw: str | int | float | None) -> str | None:
    """Return any text trailing the leading follower number, e.g. the
    "(10-day avg views 1,290, ...)" note some sheet cells carry -- so it can
    be preserved in internal_notes instead of silently dropped."""
    if raw is None or isinstance(raw, (int, float)):
        return None
    text = str(raw).strip()
    match = re.match(r"^[\d][\d,]*(?:\.\d+)?\s*[kKmM]?\s*(.*)$", text)
    if not match:
        return None
    rest = match.group(1).strip(" ()（）")
    return rest or None


_LIST_INDEX_TAIL = re.compile(r"^\.\s+[A-Z一-鿿]")


def _looks_like_glued_list_index(segment: str, match: re.Match, num1: float) -> bool:
    """Guard against "...350$2. Per single post..." (no space before the
    next numbered-list item) being misread as a PREFIX_AMOUNT match of "$2"
    -- the "$" that closes "350$" gets reinterpreted as opening "$2". Real
    per-post quotes in this dataset are never under $10, so a tiny amount
    immediately followed by ". <Capitalized word>" is almost certainly a
    swallowed list marker ("2. Per...."), not a price -- skip it and let the
    scan continue for the real amount. A "k"/"m" multiplier suffix rules
    this out immediately: a list index is never written "2k."."""
    if num1 >= 10 or match.group("mult"):
        return False
    tail = segment[match.end() : match.end() + 4]
    return bool(_LIST_INDEX_TAIL.match(tail))


def _find_amount(segment: str) -> tuple[float | None, float | None, float | None, str | None, str]:
    """Find the first currency-marked amount (and optional range) in a
    segment. Returns (amount, amount_min, amount_max, currency,
    segment_without_amount_span). currency is None if no marker was found --
    callers should fall back to a bare-number match or a default currency."""
    match = None
    for candidate in _PREFIX_AMOUNT.finditer(segment):
        num1 = float(candidate.group("num").replace(",", ""))
        if _looks_like_glued_list_index(segment, candidate, num1):
            continue
        match = candidate
        break
    if not match:
        match = _SUFFIX_AMOUNT.search(segment)
    if not match:
        return None, None, None, None, segment

    groups = match.groupdict()
    cur_raw = groups.get("cur") or groups.get("curw")
    currency = CURRENCY_SYMBOLS.get(cur_raw, cur_raw.upper() if cur_raw else None)

    num1 = _apply_mult(float(groups["num"].replace(",", "")), groups.get("mult"))
    num2_raw = groups.get("num2")
    remainder = segment[: match.start()] + " " + segment[match.end():]
    if num2_raw:
        num2 = _apply_mult(float(num2_raw.replace(",", "")), groups.get("mult2"))
        low, high = sorted((num1, num2))
        amount = round((low + high) / 2, 2)
        return amount, low, high, currency or "USD", remainder

    return num1, None, None, currency or "USD", remainder


def _find_bare_amount(segment: str) -> tuple[float | None, str | None]:
    """Last-resort match for segments with a number but no currency marker
    at all, e.g. 'Tweeet: 250' or '150 per tweet'. Requires an explicit
    price cue (':', 'per', 'for', '/') so turnaround times ('7-10 days') and
    percentages ('30-50%') are not misread as prices."""
    match = _BARE_TRAILING.match(segment)
    if match:
        return float(match.group("num").replace(",", "")), match.group("label")
    match = _BARE_PER.match(segment)
    if match:
        return float(match.group("num").replace(",", "")), match.group("label")
    return None, None


def _clean_label(text: str) -> str:
    text = _LIST_MARKER.sub("", text)
    text = text.strip(" \t-—–:,/")
    text = _LEADING_CONNECTOR.sub("", text)
    text = text.strip(" \t-—–:,/")
    return text


def parse_quote_text(raw_text: str, default_currency: str = "USD") -> list[ParsedDeliverable]:
    """Split a free-text quote into best-effort (deliverable, price) rows."""
    text = normalize_text(raw_text)
    if not text:
        return []

    segments = [s.strip() for s in _SEGMENT_SPLIT.split(text) if s and s.strip()]
    if not segments:
        segments = [text]

    results: list[ParsedDeliverable] = []
    for segment in segments:
        amount, amt_min, amt_max, currency, remainder = _find_amount(segment)
        if amount is None:
            bare_amount, bare_label = _find_bare_amount(segment)
            if bare_amount is not None:
                results.append(
                    ParsedDeliverable(
                        deliverable=_clean_label(bare_label) or "Package",
                        amount=bare_amount,
                        amount_min=None,
                        amount_max=None,
                        currency=default_currency,
                        is_confident=True,
                        raw_segment=segment,
                    )
                )
                continue
            # No parseable price in this segment at all -- keep it visible, not guessed.
            results.append(
                ParsedDeliverable(
                    deliverable=_clean_label(segment) or "General / see raw quote",
                    amount=None,
                    amount_min=None,
                    amount_max=None,
                    currency=default_currency,
                    is_confident=False,
                    raw_segment=segment,
                )
            )
            continue

        # `remainder` is the segment with the amount span removed. Usually the
        # label sits before the amount ("Thread: $500" -> remainder="Thread: ").
        # If that's empty (price came first, e.g. "$200 for a single post"),
        # fall back to a trailing "for/per X" phrase.
        label = _clean_label(remainder)
        if not label or len(label) < 2:
            for_per = _FOR_PER_LABEL.search(remainder)
            if for_per:
                label = _clean_label(for_per.group(1))
        if not label or len(label) < 2:
            label = "Package"

        results.append(
            ParsedDeliverable(
                deliverable=label,
                amount=amount,
                amount_min=amt_min,
                amount_max=amt_max,
                currency=currency or default_currency,
                is_confident=True,
                raw_segment=segment,
            )
        )
    return results
