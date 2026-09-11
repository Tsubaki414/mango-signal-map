"""Normalisers for the free-text supply fields inherited from the BD system.

The BD data expressed the same fact several ways -- "English"/"en", "US"/"USA"/
"United States"/"NYC" -- which made every one of them unusable as a filter. A
client asking for "英文内容 / 美国市场" could not be served from those columns.

Two rules hold throughout:

* **Never invent.** An unmappable value returns ``None``, which the product
  renders as 未知. Nothing here guesses a language from a name, or a market
  from a language.
* **Never discard.** Every caller stores the raw value alongside the
  normalised one, so a bad mapping can be re-derived rather than being a
  lossy one-way import.
"""

from __future__ import annotations

import re

# --- language ---------------------------------------------------------------

_LANGUAGE_MAP: dict[str, str] = {
    "english": "en", "en": "en", "eng": "en",
    "chinese": "zh", "zh": "zh", "mandarin": "zh", "中文": "zh", "cn": "zh",
    "spanish": "es", "es": "es", "español": "es",
    "portuguese": "pt", "pt": "pt", "português": "pt",
    "french": "fr", "fr": "fr", "français": "fr",
    "german": "de", "de": "de", "deutsch": "de",
    "japanese": "ja", "ja": "ja", "日本語": "ja",
    "korean": "ko", "ko": "ko",
    "amharic": "am", "am": "am",
    "hindi": "hi", "hi": "hi",
    "arabic": "ar", "ar": "ar",
    "russian": "ru", "ru": "ru",
    "italian": "it", "it": "it",
    "turkish": "tr", "tr": "tr",
    "vietnamese": "vi", "vi": "vi",
    "indonesian": "id", "id": "id",
}


def normalize_language(raw: str | None) -> str | None:
    """Return comma-separated ISO-639-1 codes, or None if nothing maps.

    Handles the multi-value cells the source sheets contain ("English,
    Spanish"). An unknown token is dropped rather than passed through, so the
    column only ever holds real codes.
    """
    if not raw:
        return None
    codes: list[str] = []
    for token in re.split(r"[,/;、&+]|\band\b", raw):
        code = _LANGUAGE_MAP.get(token.strip().lower())
        if code and code not in codes:
            codes.append(code)
    return ",".join(codes) if codes else None


# --- country ----------------------------------------------------------------

_COUNTRY_MAP: dict[str, str] = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united states": "US", "united states of america": "US", "america": "US",
    "nyc": "US", "new york": "US", "san francisco": "US", "los angeles": "US",
    "uk": "GB", "u.k.": "GB", "united kingdom": "GB", "england": "GB",
    "britain": "GB", "great britain": "GB", "london": "GB", "scotland": "GB",
    "canada": "CA", "germany": "DE", "deutschland": "DE", "austria": "AT",
    "france": "FR", "spain": "ES", "italy": "IT", "ireland": "IE",
    "netherlands": "NL", "portugal": "PT", "poland": "PL", "czechia": "CZ",
    "czech republic": "CZ", "switzerland": "CH", "sweden": "SE",
    "norway": "NO", "denmark": "DK", "finland": "FI", "belgium": "BE",
    "azerbaijan": "AZ", "turkey": "TR", "türkiye": "TR",
    "india": "IN", "china": "CN", "prc": "CN", "japan": "JP",
    "south korea": "KR", "korea": "KR", "singapore": "SG",
    "hong kong": "HK", "taiwan": "TW", "vietnam": "VN", "indonesia": "ID",
    "malaysia": "MY", "thailand": "TH", "philippines": "PH",
    "australia": "AU", "new zealand": "NZ",
    "brazil": "BR", "brasil": "BR", "mexico": "MX", "argentina": "AR",
    "colombia": "CO", "chile": "CL",
    "nigeria": "NG", "kenya": "KE", "south africa": "ZA", "egypt": "EG",
    "ethiopia": "ET", "ghana": "GH",
    "uae": "AE", "united arab emirates": "AE", "dubai": "AE",
    "israel": "IL", "saudi arabia": "SA",
}

#: Values that name a scope rather than a country. Mapping these to a country
#: would be a fabrication, so they resolve to None and are preserved raw.
_NON_COUNTRY = {"global", "worldwide", "international", "remote", "eu", "europe", "asia", "latam", "mena"}


def normalize_country(raw: str | None) -> str | None:
    """Return a single ISO-3166-1 alpha-2 code, or None.

    Multi-country cells ("Japan, United States") return the first resolvable
    code: ``base_country`` is one place by definition. The raw value is kept
    so the second country is not lost, and audience reach is a separate
    question answered by ``audience_markets``, never inferred from here.
    """
    if not raw:
        return None
    for token in re.split(r"[,/;、&]|\band\b", raw):
        cleaned = token.strip().lower().rstrip(".")
        if not cleaned or cleaned in _NON_COUNTRY:
            continue
        code = _COUNTRY_MAP.get(cleaned)
        if code:
            return code
    return None


def is_global_scope(raw: str | None) -> bool:
    """True when the source said "Global" rather than naming a country."""
    if not raw:
        return False
    return raw.strip().lower() in _NON_COUNTRY


# --- market region ----------------------------------------------------------
#
# A precise audience-geography field is unfillable from the inherited data:
# only 42 of 262 priced creators have any country on file, so a country-based
# market filter would hide 84% of the sellable inventory. A coarse bucket that
# can actually be populated is worth more than a precise one that is empty.
#
# Content language is the better proxy anyway. An English-language creator
# reaches a Western audience whether they are based in Texas or Singapore, so
# *where the creator lives* answers a different question from *which market
# their content lands in*. Language leads; country only fills the gap.
#
# Every value carries a ``basis`` saying how it was derived, so the client card
# can say "依据：内容语言" rather than implying Mango measured an audience.
# This is 高概率推断, never 已验证事实.

MARKET_REGIONS = (
    "europe_america",  # 欧美
    "latam",           # 拉美
    "greater_china",   # 中文圈
    "japan_korea",     # 日韩
    "sea",             # 东南亚
    "south_asia",      # 南亚
    "mena",            # 中东
    "africa",          # 非洲
    "global",          # 全球 / 跨区
)

MARKET_REGION_LABELS_ZH = {
    "europe_america": "欧美", "latam": "拉美", "greater_china": "中文圈",
    "japan_korea": "日韩", "sea": "东南亚", "south_asia": "南亚",
    "mena": "中东", "africa": "非洲", "global": "全球",
}

#: How a market region was arrived at, strongest evidence first.
MARKET_BASES = ("language", "country", "provenance", "bio_script", "unknown")

_LANGUAGE_TO_MARKET = {
    "en": "europe_america", "de": "europe_america", "fr": "europe_america",
    "it": "europe_america", "nl": "europe_america", "ru": "europe_america",
    "es": "latam", "pt": "latam",
    "zh": "greater_china",
    "ja": "japan_korea", "ko": "japan_korea",
    "id": "sea", "vi": "sea", "th": "sea",
    "hi": "south_asia",
    "ar": "mena", "tr": "mena",
    "am": "africa",
}

_COUNTRY_TO_MARKET = {
    "US": "europe_america", "CA": "europe_america", "GB": "europe_america",
    "IE": "europe_america", "DE": "europe_america", "FR": "europe_america",
    "ES": "europe_america", "PT": "europe_america", "IT": "europe_america",
    "NL": "europe_america", "AT": "europe_america", "CH": "europe_america",
    "SE": "europe_america", "NO": "europe_america", "DK": "europe_america",
    "FI": "europe_america", "BE": "europe_america", "PL": "europe_america",
    "CZ": "europe_america", "AU": "europe_america", "NZ": "europe_america",
    "BR": "latam", "MX": "latam", "AR": "latam", "CO": "latam", "CL": "latam",
    "CN": "greater_china", "HK": "greater_china", "TW": "greater_china",
    "JP": "japan_korea", "KR": "japan_korea",
    "SG": "sea", "ID": "sea", "VN": "sea", "TH": "sea", "MY": "sea", "PH": "sea",
    "IN": "south_asia",
    "AE": "mena", "SA": "mena", "IL": "mena", "TR": "mena", "AZ": "mena", "EG": "mena",
    "NG": "africa", "KE": "africa", "ZA": "africa", "ET": "africa", "GH": "africa",
}

#: Spanish and Portuguese default to 拉美 by creator volume, but a European
#: base country overrides that -- the two cases are commercially different.
_EUROPEAN_ROMANCE_COUNTRIES = {"ES", "PT"}

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
_LATIN = re.compile(r"[A-Za-z]")


def derive_market_region(
    languages: str | None,
    base_country: str | None = None,
    region_raw: str | None = None,
    provenance: str | None = None,
    bio: str | None = None,
) -> tuple[str | None, str]:
    """Return ``(market_region, basis)`` for the coarse market filter.

    Returns ``(None, "unknown")`` only when no signal at all exists. Such a
    creator must still appear in market-filtered results, labelled
    市场待确认 -- excluding them would silently hide sellable inventory,
    which is the exact failure this field exists to prevent.
    """
    if region_raw and is_global_scope(region_raw):
        return "global", "country"

    # 1. Content language -- the closest thing to an audience signal on file.
    if languages:
        for code in languages.split(","):
            market = _LANGUAGE_TO_MARKET.get(code.strip())
            if not market:
                continue
            if market == "latam" and (base_country or "") in _EUROPEAN_ROMANCE_COUNTRIES:
                return "europe_america", "language"
            return market, "language"

    # 2. Creator location -- weaker, but real.
    if base_country:
        market = _COUNTRY_TO_MARKET.get(base_country.upper())
        if market:
            return market, "country"

    # 3. Which sheet the creator arrived on. One source list is explicitly
    #    "英文AI KOL", which states the content language outright.
    if provenance and ("英文" in provenance or "English" in provenance):
        return "europe_america", "provenance"

    # 4. The account's own bio. Latin script with no CJK is direct evidence
    #    the account publishes for a Western-language audience -- weaker than
    #    a declared language, but far better than dropping the creator.
    if bio and _is_latin_script(bio):
        return "europe_america", "bio_script"

    return None, "unknown"


def _is_latin_script(text: str) -> bool:
    """True when the text is Latin-script with no meaningful CJK content."""
    if _CJK.search(text or ""):
        return False
    return len(_LATIN.findall(text or "")) >= 12


# --- verticals --------------------------------------------------------------

#: Controlled vocabulary. Deliberately coarse: these are filter buckets a
#: client actually asks for, not a taxonomy of every topic a creator covers.
VERTICALS = (
    "ai", "developer_tools", "crypto", "finance", "robotics",
    "marketing", "business", "design", "media_video", "education",
    "consumer_tech", "gaming", "science",
)

_VERTICAL_TOKENS: dict[str, str] = {
    # ai
    "ai": "ai", "artificial intelligence": "ai", "genai": "ai", "llm": "ai",
    "machine learning": "ai", "ml": "ai", "ai productivity": "ai",
    "automation": "ai", "data science": "ai", "agents": "ai", "ai tools": "ai",
    # developer tools
    "coding": "developer_tools", "development": "developer_tools",
    "software development": "developer_tools", "web development": "developer_tools",
    "open source": "developer_tools", "saas": "developer_tools",
    "no-code": "developer_tools", "nocode": "developer_tools",
    "developer": "developer_tools", "devtools": "developer_tools",
    "software": "developer_tools", "programming": "developer_tools",
    "workflows": "developer_tools", "api": "developer_tools",
    # crypto / finance
    "crypto": "crypto", "web3": "crypto", "blockchain": "crypto",
    "defi": "crypto", "nft": "crypto", "bitcoin": "crypto", "ethereum": "crypto",
    "finance": "finance", "trading": "finance", "investing": "finance",
    "fintech": "finance", "stocks": "finance",
    # robotics / science
    "robotics": "robotics", "hardware": "robotics", "drones": "robotics",
    "science": "science", "research": "science", "biotech": "science",
    # marketing
    "marketing": "marketing", "digital marketing": "marketing",
    "influencer marketing": "marketing", "social media": "marketing",
    "growth": "marketing", "branding": "marketing", "personal branding": "marketing",
    "content creation": "marketing", "ghostwriting": "marketing",
    "creator economy": "marketing", "seo": "marketing", "copywriting": "marketing",
    # business
    "business": "business", "entrepreneurship": "business", "startups": "business",
    "online business": "business", "career growth": "business", "e-commerce": "business",
    "ecommerce": "business", "sales": "business", "productivity": "business",
    # design
    "design": "design", "ux": "design", "ui": "design", "creative": "design",
    "art": "design", "creativity": "design", "presentation software": "design",
    # media / video
    "film": "media_video", "filmmaking": "media_video", "video editing": "media_video",
    "video production": "media_video", "youtube": "media_video", "media": "media_video",
    "news": "media_video", "podcasting": "media_video", "photography": "media_video",
    # education
    "education": "education", "tutorials": "education", "resources": "education",
    "insights": "education", "teaching": "education", "courses": "education",
    # consumer tech
    "tech": "consumer_tech", "technology": "consumer_tech", "tools": "consumer_tech",
    "reviews": "consumer_tech", "gadgets": "consumer_tech", "innovation": "consumer_tech",
    "consumer tech": "consumer_tech", "apps": "consumer_tech", "trends": "consumer_tech",
    # gaming
    "gaming": "gaming", "games": "gaming", "esports": "gaming",
}


def normalize_verticals(raw: str | None) -> str | None:
    """Map a free-text category cell to comma-separated controlled verticals.

    Order is preserved from the source so the creator's primary topic stays
    first. Unmappable tokens are dropped, not coerced -- ``categories_raw``
    keeps them, and a creator whose categories map to nothing returns None
    (未知) rather than a misleading bucket.
    """
    if not raw:
        return None
    result: list[str] = []
    for token in raw.split(","):
        cleaned = token.strip().lower()
        if not cleaned:
            continue
        vertical = _VERTICAL_TOKENS.get(cleaned)
        if vertical is None:
            # Multi-word cells like "AI productivity tools" -- take the first
            # token that maps rather than requiring an exact whole-cell match.
            for word in cleaned.split():
                vertical = _VERTICAL_TOKENS.get(word)
                if vertical:
                    break
        if vertical and vertical not in result:
            result.append(vertical)
    return ",".join(result) if result else None


# --- audience types ---------------------------------------------------------
#
# 目标人群 is a filter a client reaches for immediately ("I want developers"),
# and the inherited data had nothing to answer it with -- so the filter matched
# zero creators. Same failure mode as market: an empty precise field deletes
# inventory. Same fix, same three constraints (stated basis, kept apart from
# measured data, unknowns never excluded).
#
# This describes who the creator's content addresses, inferred from what they
# publish. It is not audience demographics, which nobody has measured.

AUDIENCE_TYPES = (
    "founders", "investors", "developers", "researchers", "marketers",
    "traders", "designers", "creators", "enterprise", "students", "consumers",
)

AUDIENCE_TYPE_LABELS_ZH = {
    "founders": "创始人", "investors": "投资人", "developers": "开发者",
    "researchers": "研究员", "marketers": "营销从业者", "traders": "交易者",
    "designers": "设计师", "creators": "内容创作者", "enterprise": "企业决策者",
    "students": "学习者", "consumers": "普通消费者",
}

#: content = read off the creator's own bio / content summary (stronger)
#: vertical = inferred from their topic categories (coarser)
AUDIENCE_BASES = ("content", "vertical", "unknown")

#: Phrases in a creator's own bio or content summary. Stronger evidence than a
#: category label because the creator wrote it about their own work.
_AUDIENCE_TEXT_CUES: tuple[tuple[str, str], ...] = (
    ("founders", r"founder|startup|entrepreneur|indie hacker|solopreneur|business owner|"
                 r"passive income|side hustle|monetiz|monetis|make money|创业|创始人"),
    ("investors", r"\binvestor|venture capital|\bvc\b|angel invest|fundrais|投资人|融资"),
    ("developers", r"developer|\bcoding\b|\bcode\b|engineer|programming|programmer|software|"
                   r"\bapi\b|open source|\bdevops\b|\bpython\b|javascript|\bjava\b|\bphp\b|开发者|程序员"),
    ("researchers", r"research|scientist|\bpaper\b|academia|\bphd\b|economist|specialist in|研究"),
    ("marketers", r"marketer|marketing|growth|\bseo\b|copywrit|brand strateg|营销|增长"),
    ("traders", r"trader|trading|crypto|defi|token|portfolio|交易|加密"),
    ("designers", r"designer|\bux\b|\bui\b|figma|design system|设计师"),
    ("creators", r"creator|youtuber|content creation|video edit|streamer|filmmak|"
                 r"visual storytelling|cinematic|\bedits\b|\breels\b|创作者|博主"),
    ("enterprise", r"enterprise|\bb2b\b|\bsaas\b|business leader|\bcto\b|\bceo\b|企业"),
    ("students", r"tutorial|beginner|learn|course|teach|step-by-step|教程|入门|学习"),
    ("consumers", r"no-code|nocode|non-technical|everyone|anyone can|everyday|普通人|小白"),
)

#: Fallback when the creator published nothing about themselves. Coarser, and
#: recorded as such. "ai" is deliberately absent: it says nothing about
#: audience on its own, which was the original recommender's mistake.
_VERTICAL_TO_AUDIENCE: dict[str, tuple[str, ...]] = {
    "developer_tools": ("developers",),
    "crypto": ("traders",),
    "finance": ("traders", "investors"),
    "marketing": ("marketers",),
    "business": ("founders",),
    "design": ("designers",),
    "media_video": ("creators",),
    "education": ("students",),
    "robotics": ("developers",),
    "science": ("researchers",),
    "consumer_tech": ("consumers",),
    "gaming": ("consumers",),
}

_MAX_AUDIENCE_TYPES = 3


def derive_audience_types(
    verticals: str | None,
    bio: str | None = None,
    content_summary: str | None = None,
) -> tuple[str | None, str, str | None]:
    """Return ``(audience_types_csv, basis, evidence)`` -- who this creator's
    content addresses, and the words that said so.

    Prefers the creator's own words over a category label. Capped at three
    types: a creator who plausibly reaches everyone is not a useful filter
    result, and a long list reads as padding rather than judgment.

    ``evidence`` records the phrase that triggered each type, e.g.
    ``founders:"passive income"; developers:"python"``. Some of the input
    (a creator's recent posts) is transient and not stored anywhere, so
    without this the assignment would be unauditable -- and an unexplainable
    judgment is exactly what this product must not ship.

    ``(None, "unknown", None)`` when there is no signal. Such a creator must
    still appear in audience-filtered results, labelled 人群待确认.
    """
    text = " ".join(part for part in (bio, content_summary) if part)
    if text.strip():
        found: list[str] = []
        evidence: list[str] = []
        for audience, pattern in _AUDIENCE_TEXT_CUES:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                found.append(audience)
                evidence.append(f'{audience}:"{match.group(0)}"')
        if found:
            kept = found[:_MAX_AUDIENCE_TYPES]
            return ",".join(kept), "content", "; ".join(evidence[: len(kept)])

    if verticals:
        found = []
        evidence = []
        for vertical in verticals.split(","):
            vertical = vertical.strip()
            for audience in _VERTICAL_TO_AUDIENCE.get(vertical, ()):
                if audience not in found:
                    found.append(audience)
                    evidence.append(f"{audience}:vertical={vertical}")
        if found:
            kept = found[:_MAX_AUDIENCE_TYPES]
            return ",".join(kept), "vertical", "; ".join(evidence[: len(kept)])

    return None, "unknown", None


# --- follower tiers ---------------------------------------------------------

#: Size bands for the client's 体量偏好 filter. Boundaries are conventional
#: rather than derived; they exist to make the filter expressible, and the
#: exact follower count travels alongside so nothing depends on the band.
FOLLOWER_TIERS = ("nano", "micro", "mid", "macro", "mega")


def follower_tier(followers: int | None) -> str | None:
    """Return a size band, or None when the follower count is unknown."""
    if followers is None:
        return None
    if followers < 10_000:
        return "nano"
    if followers < 100_000:
        return "micro"
    if followers < 500_000:
        return "mid"
    if followers < 1_000_000:
        return "macro"
    return "mega"
