"""GPT-assisted classification for creators auto-created from BD
sponsorship evidence (migrate_bd_data.py) -- a different situation from
classify.py's normal path: these stub creators have zero enrichment (no
bio, no followers, no post history), so the only real, groundable signal
is their channel name/handle and the real sponsorship-evidence rows that
caused them to be created (company sponsored, disclosure type, real video
titles). This module never invents metrics to fill that gap -- it either
classifies from what's actually on record, or returns Unknown.
"""

from __future__ import annotations

from typing import Any

from .classify import call_openai_json
from .models import CONFIDENCE_LEVELS, CREATOR_CLASSES

SYSTEM_PROMPT = f"""You are helping Mango Labs triage creator accounts that were auto-discovered from AI-company sponsorship evidence (a video/post where the account promoted an AI product), not from Mango's own creator database. You have NO follower count, NO bio, NO engagement data, and NO post history for this account -- only its channel name/handle and a list of real video/post titles plus how each was disclosed (paid_sponsorship / affiliate / mention).

Classes (pick exactly one): {", ".join(CREATOR_CLASSES)}
- Top KOL: only pick this if the account is a widely-recognized, large-reach individual creator you have strong general knowledge of (e.g. a well-known tech YouTuber). Do NOT pick this just because they did a paid sponsorship -- paid sponsorships happen at many scales. If you are not confident about actual scale, use KOL instead.
- KOL: an individual person publishing content (tutorials, reviews, opinions) under their own name or persona, whether or not you recognize them by name. This is the default for "some person's channel that made sponsored/tutorial content," which is most of what you'll see here.
- Community Leader: the evidence indicates they run/anchor a community, event series, or conference (not just personal content) -- e.g. a channel that's clearly a meetup/summit/conference brand rather than one person's voice.
- Media / Community Account: a news outlet, publication, review-aggregator, or official brand/community account -- not an individual's personal creative voice, but still a real content channel with its own audience worth treating as a distribution channel.
- KOC: reads as a smaller/newer individual creator promoting for pay, not clearly a distribution/media brand.
- Marketing Account: heavy cross-project promotional pattern, template-like titles across many unrelated sponsors, little sign of an original voice.
- Non-creator / Irrelevant: not actually a creator at all -- a bot, tool/product account, or an entity that is not meaningfully a KOL/KOC candidate for campaigns.
- Unknown: you genuinely cannot tell from a channel name/handle and a short title list alone. This is a legitimate, expected answer for many of these -- use it rather than guessing when the name and titles give no real signal either way.

Be conservative: with only a channel name, handle, and video titles to go on (no scale data), most real individual-creator accounts should land as KOL, not Top KOL. Reserve Non-creator/Irrelevant for cases where the name/titles clearly indicate a publication, brand, tool, or bot rather than a person's content channel. When you are not sure, prefer Unknown over guessing at Strategic or Distribution -- a wrong guess here would incorrectly surface this account as a campaign creator recommendation.

Respond ONLY with a compact JSON object with keys:
creator_class, creator_class_reason (<=30 words, must cite what in the given name/handle/titles led to this call, e.g. "Media / Community Account: name and 'Weekly Review' style titles read as a publication brand, not a personal channel."),
classification_confidence (High/Medium/Low -- Low or Unknown class whenever you're inferring from name pattern alone with no corroborating title content)."""


def _build_user_prompt(payload: dict[str, Any]) -> str:
    lines = [
        f"Platform: {payload.get('platform')}",
        f"Handle: @{payload.get('handle')}",
        f"Display name / channel name: {payload.get('display_name')}",
        "Evidence on record (real, from sponsorship-evidence extraction):",
    ]
    for row in payload.get("evidence", []):
        lines.append(f"- company: {row['company_name']} | disclosure: {row['disclosure_type']} | title: {row['content_title'] or '(no title on file)'}")
    return "\n".join(lines)


def classify_bd_creator(payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    user_prompt = _build_user_prompt(payload)
    result = call_openai_json(SYSTEM_PROMPT, user_prompt, cache_namespace="bd_classify", force=force)
    return normalize_bd_classification_result(result)


def normalize_bd_classification_result(result: dict[str, Any]) -> dict[str, Any]:
    result = dict(result)
    if result.get("creator_class") not in CREATOR_CLASSES:
        result["creator_class"] = "Unknown"
    if result.get("classification_confidence") not in CONFIDENCE_LEVELS:
        result["classification_confidence"] = "Low" if result["creator_class"] == "Unknown" else None
    reason = result.get("creator_class_reason")
    if isinstance(reason, str) and reason.strip().lower() in {"null", "none", "n/a", ""}:
        result["creator_class_reason"] = None
    return result
