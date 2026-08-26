"""YouTube Data API v3 enrichment: channel profile + recent uploads -> the
same shape of metrics as the Rapid X path (see enrichment.py), so the rest
of the app (serializers, CPM math, classification) doesn't need to know
which platform a SocialAccount came from.

Averaging method: same trimmed-mean approach as X (see enrichment.py
docstring) -- fetch the most recent uploads (default 15, one API page),
drop the single highest and single lowest view count when there are >=5
videos, and average the rest. YouTube's API does not expose a channel's
pinned/featured video reliably, so there is no pinned-post exclusion step
here (unlike X).

BLOCKED without YOUTUBE_API_KEY: this module is fully implemented against
the documented YouTube Data API v3 contract, but has not been exercised
against a live key (none was available at build time). Verify against a
real channel before trusting it at scale.
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import PromotedProject, RecentContent, SocialAccount
from .youtube_client import YouTubeClient, YouTubeError, parse_channel_ref

PROMO_KEYWORDS_RE = re.compile(
    r"\b(sponsored by|in partnership with|thanks to|use code|promo code|discount code|"
    r"affiliate link|paid partnership|ad\b|advertisement)\b",
    re.IGNORECASE,
)
SPONSOR_MENTION_RE = re.compile(
    r"(?:sponsored by|in partnership with|thanks to)\s+([A-Z][A-Za-z0-9&.\- ]{1,40})", re.IGNORECASE
)


@dataclass
class VideoRecord:
    video_id: str
    title: str
    description: str
    published_at: str | None
    published_dt: dt.datetime | None
    views: int | None
    likes: int | None
    comments: int | None
    duration_seconds: int | None


def _parse_duration(iso_duration: str | None) -> int | None:
    """Parse an ISO-8601 duration like "PT4M13S" into seconds."""
    if not iso_duration:
        return None
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso_duration)
    if not match:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _trimmed_mean(values: list[float]) -> float | None:
    if not values:
        return None
    trimmed = sorted(values)[1:-1] if len(values) >= 5 else values
    return statistics.mean(trimmed) if trimmed else None


def _resolve_channel(client: YouTubeClient, profile_url: str | None, handle: str | None, *, force: bool) -> dict:
    kind, value = parse_channel_ref(profile_url, handle)
    if not value:
        raise YouTubeError(f"Could not derive a channel reference from profile_url={profile_url!r}")

    if kind == "id":
        resp = client.get_channel_by_id(value, force=force)
    elif kind == "handle":
        resp = client.get_channel_by_handle(value, force=force)
    else:
        resp = None

    items = (resp or {}).get("items") or []
    if items:
        return items[0]

    # Fall back to search-by-name for vanity URLs (/c/Name) the handle
    # lookup can't resolve, then fetch the resolved channel by id.
    search_resp = client.search_channel(value, force=force)
    search_items = search_resp.get("items") or []
    if not search_items:
        raise YouTubeError(f"No YouTube channel found for {kind}={value!r}")
    channel_id = search_items[0]["snippet"].get("channelId") or search_items[0]["id"].get("channelId")
    channel_resp = client.get_channel_by_id(channel_id, force=force)
    channel_items = channel_resp.get("items") or []
    if not channel_items:
        raise YouTubeError(f"Search resolved a channel id but channels.list returned nothing for {channel_id!r}")
    return channel_items[0]


def _fetch_recent_videos(client: YouTubeClient, uploads_playlist_id: str, *, count: int, force: bool) -> list[VideoRecord]:
    playlist_resp = client.get_playlist_items(uploads_playlist_id, max_results=count, force=force)
    video_ids = [
        item["contentDetails"]["videoId"]
        for item in playlist_resp.get("items", [])
        if item.get("contentDetails", {}).get("videoId")
    ]
    if not video_ids:
        return []

    videos_resp = client.get_videos(video_ids, force=force)
    records: list[VideoRecord] = []
    for item in videos_resp.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        published_raw = snippet.get("publishedAt")
        published_dt = None
        if published_raw:
            try:
                published_dt = dt.datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            except ValueError:
                published_dt = None
        records.append(
            VideoRecord(
                video_id=item.get("id", ""),
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                published_at=published_raw,
                published_dt=published_dt,
                views=int(stats["viewCount"]) if stats.get("viewCount", "").isdigit() else None,
                likes=int(stats["likeCount"]) if stats.get("likeCount", "").isdigit() else None,
                comments=int(stats["commentCount"]) if stats.get("commentCount", "").isdigit() else None,
                duration_seconds=_parse_duration(content.get("duration")),
            )
        )
    return records


def extract_promoted_projects(records: list[VideoRecord], top_n: int = 8) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        for match in SPONSOR_MENTION_RE.findall(r.description or ""):
            name = match.strip().rstrip(".")
            if name:
                counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return dict(ranked)


def enrich_youtube_account(
    session: Session, account: SocialAccount, client: YouTubeClient, *, force: bool = False, video_count: int = 15
) -> dict[str, str | None]:
    """Enrich one YouTube SocialAccount in place. Returns {"country":,
    "language":} best-guess signals for the caller to apply to Creator-level
    region/language if those are still empty (YouTube channels carry this at
    the channel level, X profiles don't expose it at all)."""
    if account.platform != "YouTube":
        raise ValueError(f"enrich_youtube_account only supports YouTube, got platform={account.platform!r}")

    channel = _resolve_channel(client, account.profile_url, account.handle, force=force)
    snippet = channel.get("snippet", {})
    stats = channel.get("statistics", {})
    branding = channel.get("brandingSettings", {}).get("channel", {})
    content_details = channel.get("contentDetails", {})

    account.youtube_channel_id = channel.get("id")
    account.avatar_url = (snippet.get("thumbnails", {}).get("high") or snippet.get("thumbnails", {}).get("default") or {}).get("url")
    account.display_name_x = snippet.get("title")
    account.bio = snippet.get("description")
    if not stats.get("hiddenSubscriberCount") and stats.get("subscriberCount", "").isdigit():
        account.followers = int(stats["subscriberCount"])
        account.followers_source = "youtube"
    account.account_created_at = snippet.get("publishedAt")

    uploads_playlist_id = content_details.get("relatedPlaylists", {}).get("uploads")
    records = _fetch_recent_videos(client, uploads_playlist_id, count=video_count, force=force) if uploads_playlist_id else []

    views = [float(r.views) for r in records if r.views is not None]
    likes = [float(r.likes) for r in records if r.likes is not None]
    comments = [float(r.comments) for r in records if r.comments is not None]

    avg_views = _trimmed_mean(views)
    median_views = statistics.median(views) if views else None
    avg_likes = _trimmed_mean(likes)
    avg_comments = _trimmed_mean(comments)

    account.avg_views = round(avg_views, 1) if avg_views is not None else None
    account.median_views = round(median_views, 1) if median_views is not None else None
    if avg_views and avg_views > 0:
        parts = [v for v in (avg_likes, avg_comments) if v is not None]
        account.engagement_rate = round((sum(parts) / avg_views) * 100, 3) if parts else None
    else:
        account.engagement_rate = None

    dated = [r for r in records if r.published_dt is not None]
    if len(dated) >= 2:
        dated_sorted = sorted(dated, key=lambda r: r.published_dt)
        span = (dated_sorted[-1].published_dt - dated_sorted[0].published_dt).total_seconds() / 86400
        account.posting_frequency = (
            f"~{len(dated) / span:.2f} videos/day (last {len(dated)} videos, {span:.1f}d span)" if span > 0 else f"{len(dated)} videos within <1 day"
        )
    else:
        account.posting_frequency = None

    promo_matches = sum(1 for r in records if PROMO_KEYWORDS_RE.search(r.description or ""))
    account.promotional_content_ratio = round(promo_matches / len(records), 3) if records else None
    account.original_repost_ratio = None  # not a meaningful concept for YouTube uploads

    account.enrichment_error = (
        f"low sample: only {len(views)} recent videos with view counts" if len(views) < 5 else None
    )

    session.query(RecentContent).filter(RecentContent.social_account_id == account.id).delete()
    for r in records[:video_count]:
        session.add(
            RecentContent(
                social_account_id=account.id,
                post_id=r.video_id,
                text=r.title,
                posted_at=r.published_at,
                views=r.views,
                likes=r.likes,
                replies=r.comments,
                reposts=None,
                is_repost=False,
                is_pinned=False,
            )
        )

    session.query(PromotedProject).filter(PromotedProject.social_account_id == account.id).delete()
    for project, count in extract_promoted_projects(records).items():
        session.add(PromotedProject(social_account_id=account.id, project_name=project, mention_count=count))

    account.last_enriched_at = dt.datetime.utcnow()

    return {
        "country": snippet.get("country"),
        "language": branding.get("defaultLanguage") or snippet.get("defaultLanguage"),
    }
