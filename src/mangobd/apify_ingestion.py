from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
URL_RE = re.compile(r"https?://[^\s<>\]\[\"')]+", re.I)

SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "commercialization": ("arr", "revenue", "profitable", "valuation", "paid plan", "subscription"),
    "creator_affiliate": ("creator", "affiliate", "influencer", "ambassador", "content marketing"),
    "growth_marketing": ("growth", "user acquisition", "paid channel", "performance marketing", "top-of-funnel"),
    "regional_expansion": ("japan", "korea", "apac", "regional", "localized", "localised", "language"),
    "community_events": ("community", "workshop", "hackathon", "meetup", "event"),
    "partnership_ecosystem": (
        "partnership",
        "partner program",
        "strategic partner",
        "distribution partner",
        "ecosystem",
        "integration",
        " api ",
    ),
    "public_contact": ("contact", "email", "apply", "application"),
}

# LinkedIn commonly exposes the legal company name while Mango stores the
# product/brand name.  Keep this explicit and deliberately small: operator
# eligibility must never depend on fuzzy matching a similar company name.
COMPANY_NAME_ALIASES: dict[str, frozenset[str]] = {
    "Cursor": frozenset({"Anysphere", "Anysphere Inc", "Anysphere, Inc."}),
    "Meshy": frozenset({"MeshyAI", "Meshy AI", "Meshy LLC"}),
    "Wispr Flow": frozenset({"Wispr", "Wispr AI", "Wispr AI, Inc."}),
    "PixVerse": frozenset({"PixVerse AI"}),
    "Creatify": frozenset({"Creatify AI", "Creatify Lab Inc"}),
    "OpenArt": frozenset({"OpenArt AI"}),
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nested(item: dict[str, Any], *path: str) -> Any:
    value: Any = item
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _compact_excerpt(value: str | None, limit: int = 1400) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _flatten_text(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 3:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        return [text for item in value[:20] for text in _flatten_text(item, depth=depth + 1)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if key.casefold() not in {"id", "urn", "trackingid"}
            for text in _flatten_text(item, depth=depth + 1)
        ]
    return []


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _is_present_role(row: dict[str, Any]) -> bool:
    end_date = row.get("endDate")
    if isinstance(end_date, dict):
        return str(end_date.get("text") or "").casefold() == "present"
    return str(end_date or "").casefold() == "present"


def _role_relevance(titles: list[str]) -> str:
    text = " | ".join(titles).casefold()
    external_participant_markers = (
        "affiliate partner",
        "beta tester",
        "creator programme participant",
        "creator program participant",
        "ambassador",
        "contractor",
    )
    if any(token in text for token in external_participant_markers):
        return "external_program_participant"
    direct = (
        "creator",
        "influencer",
        "affiliate",
        "partnership",
        "marketing",
        "community",
        "developer relations",
        "devrel",
        "ecosystem",
    )
    if any(token in text for token in direct):
        return "direct_buyer_function"
    if "growth" in text and "engineer" not in text:
        return "growth_adjacent"
    return "not_buyer_function"


def _candidate_entity(item: dict[str, Any], source_kind: str, company_name: str) -> dict[str, Any]:
    if source_kind == "youtube_sponsorship":
        language_sample = f"{item.get('title') or ''} {item.get('text') or ''}"
        if re.search(r"[\u3040-\u30ff]", language_sample):
            content_language = "Japanese"
        elif re.search(r"[\u4e00-\u9fff]", language_sample):
            content_language = "Chinese_or_Japanese_needs_review"
        else:
            content_language = "English_or_other"
        return {
            "name": _first_text(item.get("channelName"), item.get("channelTitle"), item.get("author")),
            "channel_url": _first_text(item.get("channelUrl"), item.get("channelURL"), item.get("authorUrl")),
            "channel_id": _first_text(item.get("channelId"), item.get("channelID")),
            "platform": "YouTube",
            "content_language_observed": content_language,
        }
    if source_kind == "campaign_creator":
        username = _first_text(item.get("ownerUsername"), item.get("username"))
        return {
            "name": _first_text(item.get("ownerFullName"), item.get("fullName"), username),
            "handle": username,
            "profile_url": f"https://www.instagram.com/{username}/" if username else None,
            "platform": "Instagram",
        }
    if source_kind != "linkedin":
        return {}
    first = _first_text(item.get("firstName"), item.get("first_name")) or ""
    last = _first_text(item.get("lastName"), item.get("last_name")) or ""
    name = _first_text(item.get("fullName"), item.get("name"), f"{first} {last}".strip())
    current_positions = _as_rows(item.get("currentPosition"))
    experience = _as_rows(item.get("experience"))
    company_keys = {
        value.casefold().strip().rstrip(".")
        for value in {company_name, *COMPANY_NAME_ALIASES.get(company_name, frozenset())}
    }

    def company_match(row: dict[str, Any]) -> bool:
        observed = str(row.get("companyName") or "").casefold().strip().rstrip(".")
        return observed in company_keys

    matched_current = [row for row in current_positions if company_match(row)]
    matched_present_experience = [row for row in experience if company_match(row) and _is_present_role(row)]
    current_roles = [
        str(row.get("position") or row.get("title") or "").strip()
        for row in matched_current
        if row.get("position") or row.get("title")
    ]
    experience_roles = [
        str(row.get("position") or row.get("title") or "").strip()
        for row in matched_present_experience
        if row.get("position") or row.get("title")
    ]
    relevance = _role_relevance(current_roles)
    if matched_current and relevance == "direct_buyer_function":
        candidate_status = "current_direct_buyer"
    elif matched_current and relevance == "growth_adjacent":
        candidate_status = "current_adjacent_role"
    elif matched_current:
        candidate_status = "current_non_buyer_role"
    elif matched_present_experience:
        candidate_status = "profile_claim_needs_current_role_validation"
    else:
        candidate_status = "not_current_company_operator"
    location_value = item.get("location")
    if isinstance(location_value, dict):
        parsed = location_value.get("parsed") if isinstance(location_value.get("parsed"), dict) else {}
        location = _first_text(parsed.get("text"), location_value.get("linkedinText"))
    else:
        location = _first_text(location_value)
    return {
        "name": name,
        "headline": _first_text(item.get("headline"), item.get("jobTitle"), *(current_roles or [None])),
        "company": company_name if matched_current else None,
        "location": _first_text(location, item.get("geoLocationName")),
        "current_roles_at_company": current_roles,
        "present_experience_claims_at_company": experience_roles,
        "role_relevance": relevance if matched_current else "unresolved",
        "operator_candidate_status": candidate_status,
        "eligible_for_operator_review": candidate_status in {"current_direct_buyer", "current_adjacent_role"},
    }


def _disclosure_snippets(text: str) -> list[str]:
    chunks = [re.sub(r"\s+", " ", chunk).strip() for chunk in re.split(r"[\r\n]+|(?<=[.!?])\s+", text)]
    disclosure_terms = (
        "sponsor",
        "#ad",
        "paid promotion",
        "affiliate",
        "commission",
        "referral link",
        "partner link",
    )
    return [chunk[:500] for chunk in chunks if chunk and any(term in chunk.casefold() for term in disclosure_terms)][:10]


def _commercial_candidate(item: dict[str, Any], source_kind: str, company_name: str, body: str) -> dict[str, Any]:
    if source_kind != "youtube_sponsorship":
        return {}
    lowered = body.casefold()
    company_key = company_name.casefold()
    company_mentioned = company_key in lowered
    denial = bool(re.search(r"\b(not|isn't|wasn't|is not|was not|no)\s+(a\s+)?sponsor(ed|ship)?\b", lowered))
    structured_paid = any(
        item.get(key) is True
        for key in ("isPaidPromotion", "paidPromotion", "isSponsored", "containsPaidPromotion", "isPaidContent")
    )
    paid = structured_paid or bool(
        re.search(
            r"(#ad\b|paid promotion|sponsored by|sponsor\s*:|sponsor(?:ed|ing) (?:this|today|the|our)|thanks? to .{0,80} sponsor)",
            lowered,
        )
    )
    affiliate = bool(re.search(r"affiliate (?:link|links|program)|\baffiliate\b|earn(?:s|ing)? (?:a )?commission", lowered))
    brand_paid = bool(
        re.search(rf"(?:sponsor(?:ed|ship)?(?:\s+by)?|sponsor\s*:).{{0,100}}{re.escape(company_key)}", lowered)
        or re.search(rf"{re.escape(company_key)}.{{0,100}}sponsor(?:ed|ship)?", lowered)
    )
    description_links = _as_rows(item.get("descriptionLinks"))
    link_rows = [
        {"url": str(row.get("url") or ""), "text": str(row.get("text") or "")}
        for row in description_links
        if row.get("url")
    ]
    tracked_brand_links: list[str] = []
    non_tracking_hosts = {
        "youtube.com",
        "linkedin.com",
        "instagram.com",
        "facebook.com",
        "tiktok.com",
        "x.com",
        "twitter.com",
    }
    for row in link_rows:
        url = row["url"]
        parsed = urlparse(url)
        host = parsed.netloc.casefold().removeprefix("www.")
        path = parsed.path.casefold()
        is_official_home = host in {f"{company_key}.app", f"{company_key}.com", f"{company_key}.ai"}
        if (
            "gam.link/" in url.casefold()
            or "partnerstack" in url.casefold()
            or (host not in non_tracking_hosts and not is_official_home and f"/{company_key}" in path)
            or f"{company_key}affiliate" in f"{url} {row['text']}".casefold()
        ):
            tracked_brand_links.append(url)
    explicit_brand_affiliate = bool(
        re.search(rf"{re.escape(company_key)}\s+(?:affiliate|partner|referral)\s+(?:link|program)", lowered)
        or re.search(rf"(?:affiliate|partner|referral)\s+(?:link|program).{{0,40}}{re.escape(company_key)}", lowered)
        or f"#{company_key}partner" in lowered.replace(" ", "")
    )
    if denial:
        classification = "explicit_organic_or_denial"
    elif paid and affiliate:
        classification = "paid_sponsorship_and_affiliate_candidate"
    elif paid:
        classification = "paid_sponsorship_candidate"
    elif affiliate:
        classification = "affiliate_candidate"
    elif any(term in lowered for term in ("partner link", "referral link", "promo code", "discount code")):
        classification = "ambiguous_commercial_candidate"
    else:
        classification = "no_commercial_disclosure_found"
    if denial:
        attribution_status = "explicit_denial"
    elif (paid or affiliate) and (brand_paid or explicit_brand_affiliate or tracked_brand_links):
        attribution_status = "brand_attributed"
    elif (paid or affiliate) and company_mentioned:
        attribution_status = "brand_link_or_disclosure_needs_resolution"
    elif paid or affiliate:
        attribution_status = "generic_disclosure_only"
    else:
        attribution_status = "not_applicable"
    return {
        "company_mentioned": company_mentioned,
        "classification_candidate": classification,
        "structured_paid_promotion_flag": structured_paid,
        "paid_brand_attributed": brand_paid,
        "affiliate_brand_attributed": explicit_brand_affiliate or bool(tracked_brand_links),
        "brand_link_candidates": sorted(set(tracked_brand_links)),
        "attribution_status": attribution_status,
        "disclosure_snippets": _disclosure_snippets(body),
        "eligible_for_sponsorship_review": company_mentioned
        and classification
        not in {"no_commercial_disclosure_found", "explicit_organic_or_denial"},
    }


def _public_links(text: str) -> dict[str, list[str]]:
    urls = sorted({url.rstrip(".,;:") for url in URL_RE.findall(text)})
    return {
        "emails": sorted({email.casefold() for email in EMAIL_RE.findall(text)}),
        "linkedin_urls": [url for url in urls if "linkedin.com/" in url.casefold()],
        "youtube_urls": [url for url in urls if "youtube.com/" in url.casefold() or "youtu.be/" in url.casefold()],
        "instagram_urls": [url for url in urls if "instagram.com/" in url.casefold()],
        "tiktok_urls": [url for url in urls if "tiktok.com/" in url.casefold()],
        "x_urls": [url for url in urls if "x.com/" in url.casefold() or "twitter.com/" in url.casefold()],
        "contact_urls": [
            url
            for url in urls
            if any(token in url.casefold() for token in ("contact", "partner", "affiliate", "ambassador", "community"))
        ],
    }


def _candidate_metrics(item: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "views": ("viewCount", "views", "videoViewCount"),
        "likes": ("likes", "likesCount"),
        "comments": ("commentsCount", "comments"),
        "subscribers": ("numberOfSubscribers", "subscribers"),
        "followers": ("followersCount", "followers"),
        "duration": ("duration",),
        "is_paid_content": ("isPaidContent", "isPaidPromotion"),
    }
    return {
        output: next((item.get(key) for key in keys if item.get(key) is not None), None)
        for output, keys in mapping.items()
    }


def _candidate_signals(text: str, limit: int = 14) -> list[dict[str, str]]:
    """Extract verbatim review snippets; never turn them into confirmed claims."""

    chunks = [re.sub(r"\s+", " ", chunk).strip(" -*#\t") for chunk in re.split(r"[\r\n]+|(?<=[.!?])\s+", text)]
    seen: set[tuple[str, str]] = set()
    signals: list[dict[str, str]] = []
    for chunk in chunks:
        if not 30 <= len(chunk) <= 700:
            continue
        lowered = chunk.casefold()
        for signal_type, keywords in SIGNAL_PATTERNS.items():
            if not any(keyword in lowered for keyword in keywords):
                continue
            key = (signal_type, chunk.casefold())
            if key in seen:
                continue
            seen.add(key)
            signals.append({"signal_type": signal_type, "verbatim_candidate": chunk[:500]})
            if len(signals) >= limit:
                return signals
    return signals


@dataclass(slots=True)
class ResearchObservation:
    observation_id: str
    schema_version: int
    company_id: str
    company_name: str
    source_kind: str
    evidence_type: str
    actor_id: str
    actor_build: str | None
    apify_run_id: str
    apify_dataset_id: str
    source_url: str
    source_title: str | None
    published_at: str | None
    collected_at: str
    raw_text_excerpt: str
    content_hash: str
    raw_cache_path: str
    raw_item_index: int
    source_http_status: int | None
    source_validity: str
    eligible_for_evidence_review: bool
    candidate_contacts: dict[str, list[str]] = field(default_factory=dict)
    candidate_signals: list[dict[str, str]] = field(default_factory=list)
    candidate_entity: dict[str, Any] = field(default_factory=dict)
    candidate_commercial_relationship: dict[str, Any] = field(default_factory=dict)
    candidate_metrics: dict[str, Any] = field(default_factory=dict)
    collection_context: dict[str, Any] = field(default_factory=dict)
    confidence: str = "lead"
    fact_status: str = "observed_unreviewed"
    review_status: str = "unreviewed"
    reviewed_at: str | None = None
    reviewed_note: str | None = None


def normalize_apify_item(
    item: dict[str, Any],
    *,
    item_index: int,
    company_id: str,
    company_name: str,
    source_kind: str,
    actor_id: str,
    actor_build: str | None,
    run_id: str,
    dataset_id: str,
    raw_cache_path: str,
    collected_at: str,
) -> ResearchObservation | None:
    source_url = _first_text(
        item.get("url"),
        item.get("sourceUrl"),
        item.get("profileUrl"),
        item.get("videoUrl"),
        item.get("linkedinUrl"),
    )
    if not source_url:
        return None
    body = _first_text(
        item.get("text"),
        item.get("markdown"),
        item.get("description"),
        item.get("caption"),
        item.get("about"),
        item.get("content"),
    ) or ""
    if not body:
        relevant = {
            key: item.get(key)
            for key in (
                "headline",
                "jobTitle",
                "currentPosition",
                "companyName",
                "about",
                "summary",
                "experience",
                "description",
                "channelName",
                "title",
                "email",
                "emails",
                "socialLinks",
            )
            if item.get(key) is not None
        }
        body = " | ".join(_flatten_text(relevant))
    status_value = _nested(item, "crawl", "httpStatusCode")
    try:
        http_status = int(status_value) if status_value is not None else None
    except (TypeError, ValueError):
        http_status = None
    soft_404 = bool(re.search(r"\b(page not found|404 not found|doesn't exist)\b", body[:800], re.I))
    valid_status = http_status is None or 200 <= http_status < 400
    eligible = valid_status and not soft_404
    if not valid_status:
        source_validity = "invalid_http_status"
    elif soft_404:
        source_validity = "soft_404"
    else:
        source_validity = "valid_http_response"
    entity = _candidate_entity(item, source_kind, company_name)
    commercial_candidate = _commercial_candidate(item, source_kind, company_name, body)
    title = _first_text(
        item.get("title"),
        item.get("name"),
        entity.get("name"),
        _nested(item, "metadata", "title"),
        _nested(item, "metadata", "openGraph", "og:title"),
    )
    published_at = _first_text(
        item.get("publishedAt"),
        item.get("published_at"),
        item.get("date"),
        _nested(item, "metadata", "publishedTime"),
        _nested(item, "metadata", "article:published_time"),
    )
    hash_material = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    content_hash = hashlib.sha256(hash_material).hexdigest()
    id_material = f"{company_id}|{source_kind}|{source_url}|{content_hash}".encode("utf-8")
    observation_id = f"apify:{hashlib.sha256(id_material).hexdigest()[:24]}"
    evidence_types = {
        "official_web": "first_party_web_snapshot",
        "linkedin": "public_professional_profile_observation",
        "youtube_sponsorship": "youtube_sponsorship_candidate",
        "public_contact": "public_contact_candidate",
        "campaign_creator": "campaign_creator_candidate",
    }
    return ResearchObservation(
        observation_id=observation_id,
        schema_version=1,
        company_id=company_id,
        company_name=company_name,
        source_kind=source_kind,
        evidence_type=evidence_types.get(source_kind, "external_research_observation"),
        actor_id=actor_id,
        actor_build=actor_build,
        apify_run_id=run_id,
        apify_dataset_id=dataset_id,
        source_url=source_url,
        source_title=title,
        published_at=published_at,
        collected_at=collected_at,
        raw_text_excerpt=_compact_excerpt(body),
        content_hash=content_hash,
        raw_cache_path=raw_cache_path,
        raw_item_index=item_index,
        source_http_status=http_status,
        source_validity=source_validity,
        eligible_for_evidence_review=eligible,
        candidate_contacts=_public_links(body),
        candidate_signals=_candidate_signals(body) if eligible else [],
        candidate_entity=entity,
        candidate_commercial_relationship=commercial_candidate,
        candidate_metrics=_candidate_metrics(item),
        collection_context={
            "input": item.get("input"),
            "from_url": item.get("fromYTUrl"),
        },
    )


def normalize_apify_items(
    items: list[dict[str, Any]],
    *,
    company_id: str,
    company_name: str,
    source_kind: str,
    actor_id: str,
    actor_build: str | None,
    run_id: str,
    dataset_id: str,
    raw_cache_path: str,
    collected_at: str | None = None,
) -> list[dict[str, Any]]:
    timestamp = collected_at or utc_now()
    observations = [
        normalize_apify_item(
            item,
            item_index=index,
            company_id=company_id,
            company_name=company_name,
            source_kind=source_kind,
            actor_id=actor_id,
            actor_build=actor_build,
            run_id=run_id,
            dataset_id=dataset_id,
            raw_cache_path=raw_cache_path,
            collected_at=timestamp,
        )
        for index, item in enumerate(items)
    ]
    return [asdict(item) for item in observations if item is not None]


def merge_review_queue(path: Path, observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Idempotently merge observations while preserving human review fields."""

    existing: dict[str, Any] = {"schema_version": 1, "observations": []}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    old_rows = {row["observation_id"]: row for row in existing.get("observations", [])}
    for row in observations:
        previous = old_rows.get(row["observation_id"])
        if previous:
            for key in ("review_status", "reviewed_at", "reviewed_note"):
                row[key] = previous.get(key)
        old_rows[row["observation_id"]] = row
    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "observations": sorted(
            old_rows.values(),
            key=lambda row: (row.get("company_name", "").casefold(), row.get("source_url", "")),
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def safe_run_manifest(run: dict[str, Any], *, actor_input: dict[str, Any]) -> dict[str, Any]:
    """Retain provenance and cost fields without persisting credentials."""

    stats = run.get("stats") if isinstance(run.get("stats"), dict) else {}
    usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
    return {
        "schema_version": 1,
        "actor_id": run.get("actId"),
        "actor_run_id": run.get("id"),
        "actor_build_id": run.get("buildId"),
        "actor_build_number": run.get("buildNumber"),
        "dataset_id": run.get("defaultDatasetId"),
        "status": run.get("status"),
        "started_at": run.get("startedAt"),
        "finished_at": run.get("finishedAt"),
        "usage_total_usd": run.get("usageTotalUsd"),
        "compute_units": usage.get("ACTOR_COMPUTE_UNITS"),
        "run_time_seconds": stats.get("runTimeSecs"),
        "duration_millis": stats.get("durationMillis"),
        "actor_input": actor_input,
    }


def website_actor_input(start_urls: list[str], *, max_pages: int = 12, max_depth: int = 1) -> dict[str, Any]:
    if not start_urls:
        raise ValueError("At least one start URL is required")
    domains = {urlparse(url).netloc.casefold().removeprefix("www.") for url in start_urls}
    if "" in domains or len(domains) != 1:
        raise ValueError("Official website pilot must stay on one valid domain")
    domain = next(iter(domains))
    return {
        "startUrls": [{"url": url} for url in start_urls],
        "crawlerType": "playwright:adaptive",
        "includeUrlGlobs": [f"https://{domain}/**", f"https://www.{domain}/**"],
        "excludeUrlGlobs": [
            f"https://{domain}/**?*",
            f"https://www.{domain}/**?*",
            f"https://{domain}/cdn-cgi/**",
            f"https://www.{domain}/cdn-cgi/**",
        ],
        "maxCrawlDepth": max_depth,
        "maxCrawlPages": max_pages,
        "useSitemaps": False,
        "useLlmsTxt": False,
        "respectRobotsTxtFile": True,
        "maxConcurrency": 4,
        "maxRequestRetries": 2,
        "saveHtmlAsFile": False,
        "saveMarkdown": True,
        "saveFiles": False,
        "saveScreenshots": False,
        "removeCookieWarnings": True,
        "blockMedia": True,
        "debugMode": False,
    }


def linkedin_actor_input(
    company_urls: list[str],
    *,
    max_items: int = 25,
    job_titles: list[str] | None = None,
) -> dict[str, Any]:
    if not company_urls or any("linkedin.com/company/" not in url.casefold() for url in company_urls):
        raise ValueError("LinkedIn collection requires public company URLs")
    roles = job_titles or [
        "Growth",
        "Marketing",
        "Creator Partnerships",
        "Partnerships",
        "Community",
        "Affiliate",
        "Developer Relations",
        "Ecosystem",
    ]
    return {
        "profileScraperMode": "Full ($8 per 1k)",
        "maxItems": max_items,
        "companies": company_urls,
        "jobTitles": roles,
        "companyBatchMode": "one_by_one",
        "maxItemsPerCompany": max_items,
    }


def youtube_actor_input(search_queries: list[str], *, max_results: int = 50) -> dict[str, Any]:
    if not search_queries:
        raise ValueError("At least one YouTube search query is required")
    return {
        "searchQueries": search_queries,
        "maxResults": max_results,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
        "sortingOrder": "date",
        "dateFilter": "year",
        "downloadSubtitles": False,
        "aiVideoDescription": False,
        "aiVideoSummary": False,
    }


def contact_actor_input(start_urls: list[str], *, max_requests: int = 30, max_depth: int = 1) -> dict[str, Any]:
    if not start_urls:
        raise ValueError("At least one website URL is required")
    return {
        "startUrls": [{"url": url} for url in start_urls],
        "maxDepth": max_depth,
        "maxRequestsPerCrawl": max_requests,
        "useJsBrowser": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }


def instagram_actor_input(profile_urls: list[str], *, max_results: int = 30) -> dict[str, Any]:
    if not profile_urls or any("instagram.com/" not in url.casefold() for url in profile_urls):
        raise ValueError("Creator expansion requires explicit Instagram profile URLs")
    return {
        "resultsType": "posts",
        "directUrls": profile_urls,
        "resultsLimit": max_results,
        "addParentData": True,
    }
