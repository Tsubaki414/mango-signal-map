"""Free-text quote parsing for Signal Map.

Forked from ``kol_database/backend/parsing.py``, whose core design was right:
split a message into segments, find a currency-marked amount in each, and emit
an explicitly low-confidence row rather than a guessed number when that fails.
Three defects in the original made ~32% of the resulting rows unusable, and
they are fixed here:

1. **Missing separators.** ``/`` was not a segment boundary, so
   ``"Quote = $30 / Thread $100 / Single $80"`` parsed as one row priced $30
   with the rest of the line stuck in the label. Glued lowercase-to-digit
   boundaries (``"single post80 usd for quote"``) had the same effect.
2. **``is_package`` was never set.** It stayed False on all 699 rows even
   where the label was literally "Package". Budget maths that adds a package
   to a single-post price produces a number Mango cannot honour.
3. **No format/platform normalisation.** ``deliverable`` was free text, so
   "X thread" / "Thread" / "推特长文" could not be filtered as one thing.

Everything is still best-effort and every row keeps its ``raw_segment``.
The parser never invents a number: no price found means ``amount is None``
and ``parse_confidence == "unparsed"``, which the product renders as
待确认 rather than hiding the row.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Reference FX rates. Approximate, set at build time, internal budgeting only
# -- never invoicing. ``FX_ASOF`` is stored on every converted row so an old
# conversion stays explainable after these numbers change.
FX_ASOF = "2026-08"
FX_TO_USD: dict[str, float] = {
    "USD": 1.0, "USDT": 1.0, "USDC": 1.0,
    "EUR": 1.08, "GBP": 1.27, "CNY": 0.14, "RMB": 0.14,
    "INR": 0.012, "JPY": 0.0067, "KRW": 0.00072,
    "SGD": 0.74, "HKD": 0.128, "AUD": 0.65, "CAD": 0.72,
}
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY", "₹": "INR"}

_CURRENCY_WORDS = r"(?:USDT|USDC|USD|RMB|CNY|EUR|GBP|INR|pounds?|dollars?|美元|欧元|英镑|元)"
_SYMBOL_CLASS = "".join(re.escape(s) for s in CURRENCY_SYMBOLS)
_AMOUNT = r"[\d][\d,]*(?:\.\d+)?"
_MULT = r"[kKmM]?"  # "£6k" is 6,000 GBP, not £6

_RANGE_SEP = r"(?:-|–|—|~|to)"

# All three are IGNORECASE: this dataset writes "80 usd" as often as "80 USD",
# and a case-sensitive currency list silently drops every lowercase price.
#
# Symbol first: "$500", "£1,499", "€6k", "$200–400"
_PREFIX_AMOUNT = re.compile(
    rf"(?P<cur>[{_SYMBOL_CLASS}])\s?(?P<num>{_AMOUNT})(?P<mult>{_MULT})"
    rf"(?:\s*{_RANGE_SEP}\s*(?P<cur2>[{_SYMBOL_CLASS}])?\s?(?P<num2>{_AMOUNT})(?P<mult2>{_MULT}))?"
    # "$150 USD" states the currency twice; consume the redundant word so it
    # does not survive as a prefix on the *next* item's label.
    rf"(?:\s?{_CURRENCY_WORDS})?",
    re.IGNORECASE,
)
# Number first: "150$", "200 USD", "899 pounds", "1000美元"
_SUFFIX_AMOUNT = re.compile(
    rf"(?P<num>{_AMOUNT})(?P<mult>{_MULT})"
    rf"(?:\s*{_RANGE_SEP}\s*(?P<num2>{_AMOUNT})(?P<mult2>{_MULT}))?"
    rf"\s?(?:(?P<cur>[{_SYMBOL_CLASS}])|(?P<curw>{_CURRENCY_WORDS}))",
    re.IGNORECASE,
)
# Currency word first: "USD 1600", "EUR 5,500" -- the original parser missed
# this shape entirely and read the number as an unlabelled bare amount.
_WORDPREFIX_AMOUNT = re.compile(
    rf"(?<![a-z])(?P<curw>{_CURRENCY_WORDS})\s?(?P<num>{_AMOUNT})(?P<mult>{_MULT})"
    rf"(?:\s*{_RANGE_SEP}\s*(?P<num2>{_AMOUNT})(?P<mult2>{_MULT}))?",
    re.IGNORECASE,
)

_BARE_PER = re.compile(rf"^(?P<num>{_AMOUNT})\s*(?:per|for|/)\s+(?P<label>.+)$", re.IGNORECASE)
_BARE_TRAILING = re.compile(rf"^(?P<label>.+?)[:：]\s*(?P<num>{_AMOUNT})\s*$")

# Amounts written with no currency marker at all. Only matched next to an
# explicit price cue, so "7-10 days" and "30-50%" are never read as prices.
# Scanned at lower priority than currency-marked amounts: on overlap the
# marked match wins, so "$150 for a post" yields one hit, not two.
_BARE_BEFORE_CUE = re.compile(rf"(?P<num>{_AMOUNT})(?P<mult>{_MULT})\s+(?=(?:per|for)\s)", re.IGNORECASE)
_BARE_AFTER_COLON = re.compile(rf"[:：]\s*(?P<num>{_AMOUNT})(?P<mult>{_MULT})(?=\s*(?:[;,。]|$))")

#: Last-resort scan for messages that state a price with **no currency marker
#: and no price cue at all** -- literally ``300``, or ``Single 250; Thread 400;
#: Quote 200``, or ``Rates depend on post type 200-500``.
#:
#: 39 creators had no price whatsoever because of this shape, which put them
#: outside the recommendable pool entirely -- ~15% of the priced inventory,
#: invisible.
#:
#: Only ever run when the message contains **no currency marker anywhere**
#: (``_has_currency_marker``). That context is what separates a bare ``300``
#: here from ``7-10`` inside ``交付7-10工作日``: a message whose entire content
#: is numbers is a price message, whereas a number sitting inside a message
#: that already prices things in dollars is something else -- a duration, a
#: revision count, a percentage.
#:
#: A numeric floor was considered and rejected: the real minimum quote in this
#: dataset is $20, so any floor high enough to exclude "2 revisions" would also
#: throw away genuine cheap quotes.
_BARE_CLAUSE_END = re.compile(
    rf"(?P<num>{_AMOUNT})(?P<mult>{_MULT})"
    rf"(?:\s*{_RANGE_SEP}\s*(?P<num2>{_AMOUNT})(?P<mult2>{_MULT}))?"
    # Must end its clause. Anything trailing -- "%", "工作日", "min", "轮" --
    # means the number measures something other than money.
    rf"(?=\s*(?:[;；,，。]|$))"
)

#: Any sign that the message prices things explicitly. Its presence disables
#: the bare scan above for the whole message.
_CURRENCY_MARKER = re.compile(rf"[{_SYMBOL_CLASS}]|{_CURRENCY_WORDS}", re.IGNORECASE)


def _has_currency_marker(text: str) -> bool:
    return bool(_CURRENCY_MARKER.search(text))

#: Does the text right after a price introduce its deliverable ("$200 for a
#: single post")? Used to detect a whole message written price-first.
_PRICE_FIRST_CUE = re.compile(r"^\s*(?:for|per)\s", re.IGNORECASE)

_LIST_MARKER = re.compile(r"^\s*(?:[•▪●·\-\*]|\d+[\.\)]|\d+\s*[×xX]\s)\s*")
_LEADING_CONNECTOR = re.compile(r"^\s*(?:is|at|for|of|price|rate)\s+", re.IGNORECASE)
_FOR_PER_LABEL = re.compile(
    r"^\s*(?:for|per|/)\s+(?:a\s+|an\s+)?([A-Za-z一-鿿][\w 一-鿿/]{0,60})",
    re.IGNORECASE,
)

_SEGMENT_SPLIT = re.compile(
    r"[;\n。]+"                                          # explicit separators
    r"|(?<=\S)\s*[•▪●]\s*"                               # mid-line bullets
    r"|\s+/\s+"                                          # FIX 1a: " / " between items
    r"|(?<=[\d$€£¥%])\s*/\s*(?=[A-Za-z一-鿿])"   # FIX 1b: "$80/ Thread"
    r"|(?<=[a-zA-Z一-鿿])(?=\d+\s*(?:usd|USD|\$))"  # FIX 1c: "post80 usd"
    r"|(?<=[a-zA-Z一-鿿\d\)\.,%])\s+(?=\d+\s*[×xX]\s)"
    r"|(?<=[a-zA-Z一-鿿])\s+(?=\d+[\.\)]\s+[A-Za-z一-鿿])"
    r"|(?<=[\d$€£¥%])(?=[A-Z][a-zA-Z ]{0,30}?[:：])"     # "£999Quote repost:"
    r"|(?<=[\d$€£¥%])(?=[一-鿿]{2,10}[:：])"     # "$150专属视频:"
)

# --- classification vocabularies --------------------------------------------
# Ordered: the first match wins, so more specific patterns come first
# ("youtube short" before "youtube", "quote repost" before "repost").

#: ``\b`` is useless here: Python treats CJK as word characters, so
#: ``\blinkedin\b`` never matches "LinkedIn帖" and ``\btiktok\b`` never matches
#: "1条TikTok" -- which is exactly how this dataset writes them. These
#: Latin-letter-only boundaries work across a CJK/Latin seam.
_B = r"(?<![a-z])"
_E = r"(?![a-z])"

_FORMAT_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # (content_format, platform, regex) -- first match wins, so specific first
    # Plural "shorts" alone is YouTube-specific. Singular "short" is not --
    # "Post + Short Video" is an X post, so it must not match here.
    ("youtube_short",       "YouTube",   rf"{_B}(yt|youtube)\s*shorts?{_E}|{_B}shorts{_E}|shorts?\s*(广告|ad){_E}|短视频"),
    ("youtube_dedicated",   "YouTube",   rf"{_B}(yt|youtube)\s*(专属|dedicated|exclusive)|专属视频|{_B}dedicated\s+(long-?form\s+)?video|\d+\s*min\s*专属|专属长视频"),
    ("youtube_integration", "YouTube",   rf"{_B}(yt|youtube)\s*(植入|integration)|植入|{_B}integrat(ed|ion){_E}|中插"),
    ("voiceover",           "Multi",     rf"{_B}voice\s*-?over{_E}|口播"),
    ("instagram_reel",      "Instagram", rf"{_B}(ig|instagram)\s*reels?{_E}|{_B}reels?{_E}"),
    ("instagram_story",     "Instagram", rf"{_B}(ig|instagram)\s*(story|stories){_E}|{_B}stor(y|ies){_E}"),
    ("instagram_post",      "Instagram", rf"{_B}(ig|instagram){_E}"),
    ("tiktok_video",        "TikTok",    rf"{_B}tiktok{_E}"),
    ("linkedin_post",       "LinkedIn",  rf"{_B}linkedin{_E}|领英"),
    ("newsletter",          "Newsletter", rf"{_B}newsletter{_E}|邮件列表|订阅"),
    ("podcast",             "Podcast",   rf"{_B}podcast{_E}|播客"),
    ("telegram_post",       "Telegram",  rf"{_B}telegram{_E}|{_B}tg{_E}"),
    ("livestream",          "Multi",     rf"{_B}live\s*stream|直播|{_B}spaces{_E}"),
    ("article",             "Multi",     rf"{_B}(article|blog){_E}|文章|长文报道"),
    # Explicit X context beats the bare-"threads" rule below, so "X threads"
    # is an X thread rather than a post on Meta's Threads.
    ("x_thread",            "X",         rf"{_B}(x|twitter)\s*threads?{_E}|长推|推特长文"),
    ("threads_post",        "Threads",   rf"{_B}threads{_E}"),
    ("x_thread",            "X",         rf"{_B}thread{_E}"),
    # "quote repost" contains "repost", so quote must be tested first.
    ("x_quote_repost",      "X",         rf"{_B}q(uo|ou)te{_E}|{_B}(qrt|qt){_E}|引用(转发|推)?"),
    ("x_repost",            "X",         rf"{_B}(repost|retweet|rt){_E}|转发"),
    ("x_space",             "X",         rf"{_B}spaces?{_E}"),
    ("other",               "X",         rf"{_B}(banner|bio\s*link|link\s*placement|pinned){_E}|置顶|头图"),
    ("x_single_post",       "X",         rf"{_B}(single|dedicated|sponsored)?\s*(x|twitter)?\s*(posts?|tweets?){_E}|单条|推文|赞助帖|专属帖|{_B}single{_E}|帖"),
)

_PACKAGE_PATTERNS = re.compile(
    r"\bpackage\b|\bbundle\b|\btakeover\b|套餐|打包|全平台|"
    r"\ball\s+(four|4|three|3)\s+platforms?\b|跨平台|多平台|"
    r"\bcombo\b|\b\d+\s*posts?\b\s*[—\-–]\s*\$",
    re.IGNORECASE,
)

#: Text that explicitly declines to give a price. These become ``no_quote``
#: rows rather than being silently dropped -- "they refused to quote" is a
#: commercially meaningful fact and generates a 询价 task.
_NO_QUOTE_PATTERNS = re.compile(
    r"未报价|面议|详谈|另议|on request|upon request|available on request|"
    r"media\s*kit|depends? on|custom(is|iz)ed quote|tbd|待定|contact (us|me) for|"
    # Added after finding 11 rows sitting in the review queue that nobody could
    # ever resolve: the creator answered, and the answer was "no fixed price".
    # That is a commercial fact worth recording, not a parse failure.
    r"not provided|no(?:t)? (?:yet )?(?:quoted|stated)|flexible|negotiable|"
    r"tailored to|varies? (?:by|with)|discuss(?:ed)? by|by (?:campaign )?scope|"
    r"需(?:先|要)?(?:提供|沟通|细谈|单独询问)|价格需",
    re.IGNORECASE,
)

#: Explicitly free/bundled deliverables. A real fact, but not a price.
_COMPLIMENTARY = re.compile(r"complimentary|free of charge|免费|赠送|included with", re.IGNORECASE)

_QUANTITY = re.compile(
    r"(?:^|\b)(?P<n>\d{1,2})\s*(?:×|x|条|个|posts?|tweets?|videos?|threads?)\b", re.IGNORECASE
)


@dataclass
class ParsedQuote:
    """One priced deliverable extracted from a quote message."""

    deliverable_raw: str
    amount: float | None
    amount_min: float | None
    amount_max: float | None
    currency: str
    content_format: str
    platform: str
    quantity: int
    is_package: bool
    parse_confidence: str
    raw_segment: str
    parse_notes: str | None = None
    status_hint: str | None = None  # "no_quote" when the text declines to price
    package_contents: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def amount_usd(self) -> float | None:
        return to_usd(self.amount, self.currency)[0]


def to_usd(amount: float | None, currency: str) -> tuple[float | None, float | None]:
    """Return ``(amount_usd, fx_rate)``. Unknown currency yields ``(None, None)``
    rather than assuming USD -- a wrong currency assumption silently corrupts
    every budget total downstream."""
    if amount is None:
        return None, None
    rate = FX_TO_USD.get((currency or "").upper())
    if rate is None:
        return None, None
    return round(amount * rate, 2), rate


#: Client-safe budget bands. Wide on purpose: a band narrow enough to
#: back-solve Mango's cost would defeat the point of having a band at all.
PRICE_BANDS: tuple[tuple[float, str], ...] = (
    (500, "$500 以下"),
    (1_000, "$500–1,000"),
    (2_500, "$1,000–2,500"),
    (5_000, "$2,500–5,000"),
    (10_000, "$5,000–10,000"),
    (25_000, "$10,000–25,000"),
    (50_000, "$25,000–50,000"),
)
_TOP_BAND = "$50,000 以上"


def price_band(amount_usd: float | None) -> str | None:
    """Place a cost in a coarse client-facing band.

    Returns None for an unknown amount -- the client sees 价格待 Mango 确认
    rather than a band invented from nothing.
    """
    if amount_usd is None:
        return None
    for ceiling, label in PRICE_BANDS:
        if amount_usd < ceiling:
            return label
    return _TOP_BAND


def normalize_text(text: str) -> str:
    """NFKC + full-width punctuation folding. Applied to a *copy*; the caller
    always stores the untouched original alongside."""
    text = unicodedata.normalize("NFKC", text or "")
    # Curly quotes are written as escapes on purpose: spelled literally they
    # get flattened to plain ASCII by editors and tooling, which turns these
    # into no-op self-mappings AND collides the dict keys -- a substitution
    # that silently does nothing while looking correct.
    for src, dst in {
        "；": ";", "，": ",", "：": ":", "（": "(", "）": ")", "、": ",",
        "\u2018": "'", "\u2019": "'",   # ' '
        "\u201c": '"', "\u201d": '"',   # " "
    }.items():
        text = text.replace(src, dst)
    return text.strip()


def _apply_mult(value: float, mult: str | None) -> float:
    if mult and mult.lower() == "k":
        return value * 1_000
    if mult and mult.lower() == "m":
        return value * 1_000_000
    return value


_LIST_INDEX_TAIL = re.compile(r"^\.\s+[A-Z一-鿿]")


def _looks_like_glued_list_index(segment: str, match: re.Match, num1: float) -> bool:
    """Guard against ``"...350$2. Per single post"`` reading the ``$`` that
    closes ``350$`` as opening ``$2``. No real per-post quote in this dataset
    is under $10, so a tiny amount followed by ``". <Capital>"`` is a swallowed
    list marker. A k/m suffix rules it out -- nobody writes a list index "2k.".
    """
    if num1 >= 10 or match.group("mult"):
        return False
    return bool(_LIST_INDEX_TAIL.match(segment[match.end() : match.end() + 4]))


@dataclass
class _AmountHit:
    """One amount found in the text, with the span it occupies."""

    start: int
    end: int
    amount: float
    amount_min: float | None
    amount_max: float | None
    currency: str | None
    #: True when the number carried no currency marker *and* no price cue --
    #: read as a price only from the shape of the message. Weaker evidence than
    #: an unmarked amount next to "per post", so it is graded separately.
    inferred_from_shape: bool = False


def _scan_amounts(text: str) -> list[_AmountHit]:
    """Find every amount in the message, left to right, without overlaps.

    Scanning beats splitting-then-searching on this dataset. The source text
    lost its line breaks somewhere upstream, so items arrive glued
    ("£999Quote repost:", "post80 usd", "USDDedicated Thread"), and no
    separator list covers every way that happens. Positions do: whatever sits
    between two prices is that second price's label, however it was glued on.
    """
    hits: list[_AmountHit] = []
    for pattern in (
        _PREFIX_AMOUNT,
        _WORDPREFIX_AMOUNT,
        _SUFFIX_AMOUNT,
        _BARE_BEFORE_CUE,
        _BARE_AFTER_COLON,
    ):
        for match in pattern.finditer(text):
            groups = match.groupdict()
            num1 = float(groups["num"].replace(",", ""))
            if pattern is _PREFIX_AMOUNT and _looks_like_glued_list_index(text, match, num1):
                continue
            num1 = _apply_mult(num1, groups.get("mult"))
            currency = _resolve_currency(groups.get("cur") or groups.get("curw"))

            amount_min = amount_max = None
            amount = num1
            if groups.get("num2"):
                num2 = _apply_mult(float(groups["num2"].replace(",", "")), groups.get("mult2"))
                amount_min, amount_max = sorted((num1, num2))
                amount = round((amount_min + amount_max) / 2, 2)

            hits.append(
                _AmountHit(match.start(), match.end(), amount, amount_min, amount_max, currency)
            )

    # Only when nothing above matched and the message never names a currency:
    # see _BARE_CLAUSE_END. Ordered as a fallback rather than another pattern
    # in the loop so it can never outrank a currency-marked amount.
    if not hits and not _has_currency_marker(text):
        for match in _BARE_CLAUSE_END.finditer(text):
            groups = match.groupdict()
            num1 = _apply_mult(float(groups["num"].replace(",", "")), groups.get("mult"))
            amount_min = amount_max = None
            amount = num1
            if groups.get("num2"):
                num2 = _apply_mult(float(groups["num2"].replace(",", "")), groups.get("mult2"))
                amount_min, amount_max = sorted((num1, num2))
                amount = round((amount_min + amount_max) / 2, 2)
            hits.append(
                _AmountHit(
                    match.start(), match.end(), amount, amount_min, amount_max,
                    currency=None, inferred_from_shape=True,
                )
            )

    # Resolve overlaps: an earlier start wins, and on a tie the longer span
    # wins so "$200–400" is one range rather than "$200" plus a stray "400".
    hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    kept: list[_AmountHit] = []
    for hit in hits:
        if kept and hit.start < kept[-1].end:
            continue
        kept.append(hit)
    return kept


def _resolve_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    if raw in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[raw]
    token = raw.strip().lower()
    word_map = {
        "pound": "GBP", "pounds": "GBP", "英镑": "GBP",
        "dollar": "USD", "dollars": "USD", "美元": "USD",
        "欧元": "EUR", "元": "CNY",
    }
    if token in word_map:
        return word_map[token]
    return raw.upper()


def _find_bare_amount(segment: str) -> tuple[float | None, str | None]:
    """Last resort for a number with no currency marker, e.g. ``"Thread: 250"``.
    Requires an explicit price cue so turnaround times ("7-10 days") and
    percentages ("30-50%") are never read as prices."""
    for pattern in (_BARE_TRAILING, _BARE_PER):
        match = pattern.match(segment)
        if match:
            return float(match.group("num").replace(",", "")), match.group("label")
    return None, None


#: A currency word left over from the previous item's price, often glued
#: straight onto this item's label ("$150 USDDedicated Thread"). Matched
#: without a word boundary precisely because the seam has none.
_LEADING_CURRENCY = re.compile(rf"^\s*{_CURRENCY_WORDS}", re.IGNORECASE)

_LABEL_STRIP = " \t-—–:,/;.>·、"


def _clean_label(text: str) -> str:
    text = _LIST_MARKER.sub("", text)
    text = text.strip(_LABEL_STRIP)
    text = _LEADING_CURRENCY.sub("", text)
    text = _LEADING_CONNECTOR.sub("", text)
    return text.strip(_LABEL_STRIP)


#: A clause saying the creator does NOT offer something, e.g.
#: "(不做专属视频);Newsletter(115K+订阅)顶部". Matching straight through the
#: negation classified that row as ``youtube_dedicated`` -- the exact format
#: the creator had just refused -- and the recommendation then told a client
#: "已有 youtube_dedicated 的报价". The clause is stripped before matching so
#: the real deliverable (here: the newsletter) is what gets classified.
_NEGATED_CLAUSE = re.compile(
    r"(?:不做|不接|不提供|不再|暂不|no longer|not doing|does\s*n[o']t do)[^;,、)）\n]*",
    re.IGNORECASE,
)


def strip_negated_clauses(text: str) -> str:
    """Remove "we don't do X" clauses so X is not read as an offering."""
    return _NEGATED_CLAUSE.sub(" ", text or "")


def classify_deliverable(text: str) -> tuple[str, str]:
    """Map a free-text deliverable label to ``(content_format, platform)``.

    Returns ``("unknown", "Unknown")`` when nothing matches. That is a real
    answer -- it routes the row to human review instead of being bucketed into
    a format it might not be.
    """
    haystack = strip_negated_clauses(text or "").lower()
    if not haystack.strip():
        return "unknown", "Unknown"
    for content_format, platform, pattern in _FORMAT_PATTERNS:
        if re.search(pattern, haystack, re.IGNORECASE):
            return content_format, platform
    return "unknown", "Unknown"


def _extract_quantity(text: str) -> int:
    """Leading count in a label ("2 posts", "3条"). Defaults to 1.

    Capped at 2 digits so a price that slipped into the label ("post 500")
    cannot be read as a quantity of 500.
    """
    match = _QUANTITY.search(text or "")
    if not match:
        return 1
    value = int(match.group("n"))
    return value if 1 <= value <= 50 else 1


def parse_quote_message(raw_text: str, default_currency: str = "USD") -> list[ParsedQuote]:
    """Split one free-text quote message into structured priced deliverables.

    Always returns at least one row for non-empty input: text that declines to
    quote becomes a ``no_quote`` row, so "asked, and they wouldn't price it"
    stays visible in the pipeline instead of vanishing.
    """
    text = normalize_text(raw_text)
    if not text:
        return []

    message_is_package = bool(_PACKAGE_PATTERNS.search(text))
    hits = _scan_amounts(text)

    if not hits:
        return _parse_unpriced_message(text, default_currency, message_is_package)

    # Most quotes are label-first ("Thread: $150"), but some are written
    # price-first ("150 for video/thread post; 130 for single post"). Under
    # gap-based labelling those two orientations are off by one from each
    # other, so decide once per message rather than per price.
    price_first = _is_price_first(text, hits)

    results: list[ParsedQuote] = []
    for index, hit in enumerate(hits):
        gap_start = hits[index - 1].end if index else 0
        gap_after_end = hits[index + 1].start if index + 1 < len(hits) else len(text)

        label, confidence, flags, notes = _label_for_hit(
            before=text[gap_start : hit.start],
            after=text[hit.end : gap_after_end],
            price_first=price_first,
        )

        is_package = message_is_package or _is_package_label(label)
        content_format, platform = classify_deliverable(label)
        if content_format == "unknown" and len(hits) == 1:
            # A single-price message with no usable label ("1000美元/条"): the
            # message itself is the only context there is. Only safe when
            # there is exactly one price -- with several, the message text
            # would tag every row with the first format it mentions.
            content_format, platform = classify_deliverable(text)
        if content_format == "unknown" and is_package:
            content_format, platform = "package", "Multi"
        elif content_format == "unknown":
            flags.append("unclassified_format")

        if hit.currency is None:
            flags.append("assumed_currency")
            notes = notes or f"currency not stated; assumed {default_currency}"
            confidence = "medium" if confidence == "high" else confidence
        if hit.inferred_from_shape:
            # Never client-visible on this basis alone. The number is money
            # because the message looks like a price message -- which is a real
            # inference from real text, and still weaker than a creator writing
            # "$300". Mango confirms before it is shown or paid.
            flags.append("amount_inferred_from_message_shape")
            notes = (
                f"金额无货币符号也无价格提示，依据整条消息只含数字判断为报价；"
                f"币种按 {default_currency} 假定，需 Mango 复核"
            )
            confidence = "low"

        results.append(
            ParsedQuote(
                # None, not a placeholder string. ``deliverable_raw`` holds what
                # the creator actually wrote, so inventing "(unlabelled)" for it
                # both falsifies the column and escapes to a client card as
                # "$500 以下（(unlabelled)）". Every reader already falls back to
                # 合作形式待确认 or the classified format when this is empty.
                deliverable_raw=label or ("Package" if is_package else None),
                amount=hit.amount,
                amount_min=hit.amount_min,
                amount_max=hit.amount_max,
                currency=hit.currency or default_currency,
                content_format=content_format,
                platform=platform,
                quantity=_extract_quantity(label),
                is_package=is_package,
                parse_confidence=confidence,
                raw_segment=text[gap_start : hit.end].strip(),
                parse_notes=notes,
                package_contents=text if is_package else None,
                flags=flags,
            )
        )

    # Text trailing the last price often carries a real unpriced deliverable
    # ("Threads/video content: custom depending on deliverables"). Keeping it
    # as a no_quote row is what turns it into a 询价 task later.
    tail = text[hits[-1].end :].strip(" \t-—–:,/;.")
    if len(tail) > 12 and (_NO_QUOTE_PATTERNS.search(tail) or _COMPLIMENTARY.search(tail)):
        results.append(_unpriced_row(tail, default_currency, message_is_package))

    return results


def _is_price_first(text: str, hits: list[_AmountHit]) -> bool:
    """True when this message names each deliverable *after* its price.

    Decided on the majority of prices rather than per price, because a single
    message is written in one style throughout, and a per-price guess would
    shift half the labels by one item.
    """
    if not hits:
        return False
    cued = sum(1 for hit in hits if _PRICE_FIRST_CUE.match(text[hit.end : hit.end + 6]))
    return cued * 2 >= len(hits)


def _label_for_hit(
    before: str, after: str, price_first: bool = False
) -> tuple[str, str, list[str], str | None]:
    """Derive a deliverable label for one amount from the text around it.

    The label normally precedes the price ("Dedicated thread: $300"). In a
    price-first message it follows it ("$200 for a single post"), and in that
    case the preceding gap holds the *previous* item's label -- so reading it
    would silently mislabel every row.

    Returns ``(label, confidence, flags, notes)``.
    """
    flags: list[str] = []
    trailing = _FOR_PER_LABEL.search(after)
    trailing_label = _clean_label(trailing.group(1)) if trailing else ""

    if price_first and len(trailing_label) >= 2:
        label = trailing_label
    else:
        label = _clean_label(before)
        if len(label) < 2 and trailing_label:
            label = trailing_label

    if not label or len(label) < 2:
        return "", "medium", ["no_label"], "no deliverable label found next to this price"

    # A label this long means the gap between two prices swallowed prose, not
    # that someone wrote a 100-character deliverable name.
    if len(label) > 70:
        label = label[-70:].lstrip()
        flags.append("long_label")
        return label, "medium", flags, "label truncated; text between prices was unusually long"

    return label, "high", flags, None


def _parse_unpriced_message(
    text: str, default_currency: str, message_is_package: bool
) -> list[ParsedQuote]:
    """A message with no currency-marked amount anywhere.

    Tries the bare-number fallback first ("Thread: 250"), which requires an
    explicit price cue so turnaround times and percentages are never read as
    prices. Otherwise emits a single unpriced row.
    """
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(text) if s and s.strip()] or [text]
    results: list[ParsedQuote] = []

    for segment in segments:
        bare_amount, bare_label = _find_bare_amount(segment)
        if bare_amount is None:
            results.append(_unpriced_row(segment, default_currency, message_is_package))
            continue

        label = _clean_label(bare_label or "")
        content_format, platform = classify_deliverable(label)
        results.append(
            ParsedQuote(
                deliverable_raw=label or "(unlabelled)",
                amount=bare_amount,
                amount_min=None,
                amount_max=None,
                currency=default_currency,
                content_format=content_format,
                platform=platform,
                quantity=_extract_quantity(label),
                is_package=message_is_package or _is_package_label(label),
                # The number is real but its unit is assumed, so this can never
                # become a client-facing price without human review.
                parse_confidence="medium",
                raw_segment=segment,
                parse_notes=f"currency not stated in text; assumed {default_currency}",
                flags=["assumed_currency"],
            )
        )
    return results


def _is_package_label(label: str) -> bool:
    return bool(_PACKAGE_PATTERNS.search(label or ""))


def _unpriced_row(segment: str, default_currency: str, message_is_package: bool) -> ParsedQuote:
    """A segment with no parseable price.

    Distinguishes three cases that must not be collapsed: the quoter declined
    to price it, the deliverable is explicitly free, or the parser simply
    failed. Only the last one is a data-quality problem.
    """
    if _NO_QUOTE_PATTERNS.search(segment):
        status_hint, note, flag = "no_quote", "quoter declined to state a price", "declined_to_quote"
    elif _COMPLIMENTARY.search(segment):
        status_hint, note, flag = None, "stated as complimentary / bundled", "complimentary"
    else:
        status_hint, note, flag = None, "no parseable price in this segment", "unparsed"

    label = _clean_label(segment) or "(see raw quote)"
    content_format, platform = classify_deliverable(label)
    return ParsedQuote(
        deliverable_raw=label[:300],
        amount=None,
        amount_min=None,
        amount_max=None,
        currency=default_currency,
        content_format=content_format,
        platform=platform,
        quantity=1,
        is_package=message_is_package,
        parse_confidence="unparsed",
        raw_segment=segment,
        parse_notes=note,
        status_hint=status_hint,
        flags=[flag],
    )
