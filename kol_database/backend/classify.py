"""GPT-assisted creator classification.

Feeds the OpenAI Chat Completions API a compact summary of one X account
(bio + a handful of recent post excerpts + the metrics computed in
enrichment.py) and asks for a creator_class from the fixed enum, a one-line
reason, a promotion level, and a couple of light descriptive fields.

This is advisory only: `Creator.creator_class_locked` (set the moment a
human edits classification in the UI) means this function's output is never
written back over a manual call, per spec.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

from .env import get_env
from .models import CONFIDENCE_LEVELS, CREATOR_CLASSES, PROMOTION_LEVELS

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "gpt"
CACHE_TTL_SECONDS = 30 * 24 * 3600


class ClassifyError(RuntimeError):
    pass


SYSTEM_PROMPT = f"""You are helping Mango Labs classify already-contacted social media creators (X/Twitter, YouTube, and others) for a KOL vs KOC/marketing-account split.

Classes (pick exactly one): {", ".join(CREATOR_CLASSES)}
- Top KOL: large sustained reach, original analysis/opinion, repeated engagement from recognised accounts in its field.
- Community Leader: runs or clearly anchors an active community (Discord/Telegram/Spaces/events), not just a personal feed.
- KOL: consistent original voice in a vertical, real discussion in replies, not just impressions.
- Media / Community Account: media outlet, aggregator, or official community/brand account rather than a single creator's personal voice.
- KOC: small-to-mid reach, promotes for pay/product but without heavy bulk-distribution signatures; still a plausible low-cost distribution node.
- Marketing Account: templated copy, heavy cross-project promotion of many unrelated things, task/engagement-farm patterns, little to no original opinion.
- Unknown: genuinely not enough signal to tell (e.g. near-empty bio AND no metrics AND no content) -- a legitimate answer, use it rather than guessing. Creators classified Unknown go to a "Needs Review" queue for a human to check, they do NOT default into Strategic.

Judge using ALL available evidence, not followers alone: bio and stated identity, original-content ratio, sustained focus on one vertical vs. scattergun, community/media role, engagement quality (real replies vs. bot-like), posting frequency and burstiness, promotional-content ratio, breadth of unrelated projects promoted, how templated the copy reads, and whether the account shows a real point of view.

IMPORTANT: some platforms (Instagram, notably) never provide post captions through this pipeline -- "Recent post excerpts: (none available)" on those accounts is a structural API limitation, not a sign the creator is low-effort or unclassifiable. When post text is unavailable, classify from bio + followers + avg views + engagement rate alone rather than defaulting to Unknown; a clear, specific bio ("AI-first design resources, tools & workflows for modern designers") plus a real follower count and a plausible engagement rate is enough signal for a Medium-confidence call. Reserve Unknown for when the bio itself is vague/generic/near-empty or the account has essentially no metrics either.

Also set classification_confidence (High/Medium/Low): High needs real bio + real recent-content text; Medium is bio + metrics but no post text (the common Instagram case) or partial signal generally; Low (or class=Unknown) is for when even bio and metrics are thin (e.g. only a follower count and a price, nothing else pulled yet).

If Mango's own outreach-team name annotation or internal notes are present, treat them as ground truth over your own read of the bio/metrics -- they come from a human who already investigated this specific creator (e.g. confirmed it's an agency-run virtual persona, or flagged an abnormal views/follower ratio consistent with bought engagement). That should generally push toward KOC/Marketing Account regardless of how polished the account looks, and the reason string should say so explicitly.

Respond ONLY with a compact JSON object with keys:
creator_class, creator_class_reason (<=25 words, concrete, e.g. "KOL: 长期发布原创 AI agent 分析，近期互动主要来自开发者和 AI founders。" or "Marketing Account: 最近样本中大部分为跨项目推广，文案重复度较高。"),
classification_confidence, promotion_level, categories (array of up to 4 short topic tags, e.g. ["AI", "Crypto", "Tech"]),
region (best-guess country/region or null), language (primary posting language or null),
content_summary (one sentence: what this account mostly posts about)."""


def _build_user_prompt(payload: dict[str, Any]) -> str:
    recent_texts = [t for t in payload.get("recent_texts", []) if t and t.strip()]
    lines = [
        f"Platform: {payload.get('platform') or 'unknown'}",
        f"Handle: @{payload.get('handle')}",
        f"Display name: {payload.get('display_name')}",
        f"Bio: {payload.get('bio') or '(none)'}",
        f"Followers: {payload.get('followers')}",
        f"Avg views (trimmed mean, recent originals): {payload.get('avg_views')}",
        f"Engagement rate: {payload.get('engagement_rate')}",
        f"Original vs repost ratio: {payload.get('original_repost_ratio')}",
        f"Heuristic promotional content ratio (keyword-based): {payload.get('promotional_content_ratio')}",
        f"Posting frequency: {payload.get('posting_frequency')}",
    ]
    if payload.get("sheet_display_name"):
        lines.append(
            f"IMPORTANT -- name as recorded by Mango's own outreach team (may contain their own annotation, e.g. flagging an agency-run or virtual-persona account): {payload['sheet_display_name']}"
        )
    if payload.get("internal_notes"):
        lines.append(
            f"IMPORTANT -- Mango's own internal notes on this creator (human due-diligence, weight this heavily, e.g. suspicious metrics the team already flagged): {payload['internal_notes']}"
        )
    if not recent_texts:
        lines.append("Recent post excerpts: (none available -- no content pulled yet)")
    else:
        lines.append("Recent post excerpts:")
        for text in recent_texts[:10]:
            snippet = text.replace("\n", " ")[:200]
            lines.append(f"- {snippet}")
    return "\n".join(lines)


def _cache_path(cache_key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(cache_key.encode()).hexdigest()[:24]
    return CACHE_DIR / f"classify-{digest}.json"


def is_configured() -> bool:
    return bool(get_env("OPENAI_API_KEY"))


def classify_account(payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    api_key = get_env("OPENAI_API_KEY")
    if not api_key:
        raise ClassifyError("Missing OPENAI_API_KEY. Set it in the repo-root .env before classifying.")

    model = get_env("OPENAI_MODEL") or "gpt-4o-mini"
    user_prompt = _build_user_prompt(payload)
    # SYSTEM_PROMPT is part of the cache key (not just model+user_prompt):
    # editing the classification rules must invalidate old cached verdicts
    # rather than silently keep serving results reasoned under the old
    # rules forever.
    cache_key = json.dumps([model, SYSTEM_PROMPT, user_prompt], ensure_ascii=False)
    cache_path = _cache_path(cache_key)
    if cache_path.exists() and not force:
        age = time.time() - cache_path.stat().st_mtime
        if age <= CACHE_TTL_SECONDS:
            # Re-normalize on every read, not just on a fresh API call: a
            # cache entry written before a normalize_classification_result()
            # fix (e.g. the literal-string-"null" cleanup) must not keep
            # serving the unfixed value forever just because it's cached.
            return normalize_classification_result(json.loads(cache_path.read_text(encoding="utf-8")))

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
    ).encode("utf-8")

    request = Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(request, timeout=45, context=ssl_context) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ClassifyError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise ClassifyError(f"OpenAI request failed: {exc}") from exc

    try:
        content = raw["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ClassifyError(f"Unexpected OpenAI response shape: {exc}") from exc

    result = normalize_classification_result(result)
    cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


_NULLISH_STRINGS = {"null", "none", "n/a", "unknown", ""}


def normalize_classification_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate enum fields and strip models' occasional literal-string
    "null"/"N/A" placeholders (instead of real JSON null) so callers never
    store the word "null" as if it were a real region/language value."""
    result = dict(result)
    if result.get("creator_class") not in CREATOR_CLASSES:
        result["creator_class"] = "Unknown"
    if result.get("promotion_level") not in PROMOTION_LEVELS:
        result["promotion_level"] = None
    if result.get("classification_confidence") not in CONFIDENCE_LEVELS:
        result["classification_confidence"] = "Low" if result["creator_class"] == "Unknown" else None

    for key in ("region", "language", "creator_class_reason", "content_summary"):
        value = result.get(key)
        if isinstance(value, str) and value.strip().lower() in _NULLISH_STRINGS:
            result[key] = None
    return result
