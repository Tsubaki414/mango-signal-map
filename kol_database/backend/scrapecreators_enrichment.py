"""Enrichment via ScrapeCreators (https://api.scrapecreators.com) for
YouTube, Instagram, and TikTok. Writes into the same SocialAccount columns
Rapid X uses for X, so the rest of the app (serializers, CPM math,
classification) is platform-agnostic.

Averaging method: same trimmed-mean approach as every other adapter (see
stats.py / enrichment.py) -- drop the single highest and lowest value once
there are >=5 data points.

Credits: ScrapeCreators charges per call, not per creator -- Instagram is
1 call (profile response embeds recent posts), YouTube and TikTok are 2
calls each (profile/channel, then a separate videos list). A free/starter
plan only carries ~100 credits total, which will not cover a large creator
set in one pass; batch/bulk callers should budget accordingly rather than
assume unlimited quota the way Rapid X's flat-rate plan allows.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import PromotedProject, RecentContent, SocialAccount
from .scrapecreators_client import ScrapeCreatorsClient, ScrapeCreatorsError
from .stats import median, trimmed_mean

PROMO_KEYWORDS_RE = re.compile(
    r"\b(sponsored|sponsored by|paid partnership|in partnership with|use code|promo code|"
    r"discount code|affiliate link|ad\b|advertisement|swipe up|link in bio)\b",
    re.IGNORECASE,
)


def _first_url(*candidates) -> str | None:
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------


def enrich_instagram_account(session: Session, account: SocialAccount, client: ScrapeCreatorsClient, *, force: bool = False) -> None:
    if account.platform != "Instagram":
        raise ValueError(f"enrich_instagram_account only supports Instagram, got platform={account.platform!r}")
    if not account.handle:
        raise ValueError("Social account has no handle to look up")

    resp = client.get_instagram_profile(account.handle, force=force)
    user = resp.get("data", {}).get("user")
    if not user:
        raise ScrapeCreatorsError(f"No profile data returned for @{account.handle}")

    account.avatar_url = _first_url(user.get("profile_pic_url_hd"), user.get("profile_pic_url"))
    account.display_name_x = user.get("full_name")
    account.bio = user.get("biography")
    account.verified = bool(user.get("is_verified"))
    followers = (user.get("edge_followed_by") or {}).get("count")
    if followers is not None:
        account.followers = int(followers)
        account.followers_source = "scrapecreators"
    following = (user.get("edge_follow") or {}).get("count")
    if following is not None:
        account.following = int(following)

    edges = (user.get("edge_owner_to_timeline_media") or {}).get("edges", [])
    views: list[float] = []
    likes: list[float] = []
    comments: list[float] = []
    records = []
    for edge in edges:
        node = edge.get("node", {})
        view_count = node.get("video_view_count")
        like_count = (node.get("edge_liked_by") or {}).get("count")
        comment_count = (node.get("edge_media_to_comment") or {}).get("count")
        taken_at = node.get("taken_at_timestamp")
        if view_count is not None:
            views.append(float(view_count))
        if like_count is not None:
            likes.append(float(like_count))
        if comment_count is not None:
            comments.append(float(comment_count))
        records.append(
            {
                "id": node.get("id") or node.get("shortcode"),
                "views": int(view_count) if view_count is not None else None,
                "likes": int(like_count) if like_count is not None else None,
                "comments": int(comment_count) if comment_count is not None else None,
                "posted_at": dt.datetime.utcfromtimestamp(taken_at).isoformat() if taken_at else None,
            }
        )

    # Instagram only exposes a view count on video/reel posts -- photo posts
    # have real likes/comments but no `video_view_count` at all. Averaging
    # likes across ALL posts while averaging views across only the (much
    # smaller, often much-lower-view) video subset produced nonsensical
    # >100% "engagement rates" (a viral photo's 35K likes divided by a video
    # subset's ~600-view average). Instagram engagement is computed against
    # followers instead, the industry-standard approach for exactly this
    # reason, and uses every post's likes/comments, not just the video ones.
    avg_views = trimmed_mean(views)
    account.avg_views = round(avg_views, 1) if avg_views is not None else None
    med = median(views)
    account.median_views = round(med, 1) if med is not None else None
    avg_likes = trimmed_mean(likes)
    avg_comments = trimmed_mean(comments)
    if account.followers and account.followers > 0:
        parts = [v for v in (avg_likes, avg_comments) if v is not None]
        account.engagement_rate = round((sum(parts) / account.followers) * 100, 3) if parts else None
    else:
        account.engagement_rate = None
    account.original_repost_ratio = None  # not observable from this endpoint
    # Captions/text are not included in this endpoint's embedded post edges,
    # so we cannot keyword-scan for promo ratio here without a second,
    # credit-costing call -- leave it unset rather than fabricate a number.
    account.promotional_content_ratio = None
    account.posting_frequency = None
    account.enrichment_error = "no post captions available from this endpoint (profile+stats only)" if not records else account.enrichment_error
    if len(views) < 5:
        account.enrichment_error = f"low sample: only {len(views)} recent posts with view counts"

    session.query(RecentContent).filter(RecentContent.social_account_id == account.id).delete()
    for r in records:
        session.add(
            RecentContent(
                social_account_id=account.id,
                post_id=r["id"],
                text=None,
                posted_at=r["posted_at"],
                views=r["views"],
                likes=r["likes"],
                replies=r["comments"],
                reposts=None,
                is_repost=False,
                is_pinned=False,
            )
        )
    session.query(PromotedProject).filter(PromotedProject.social_account_id == account.id).delete()

    account.last_enriched_at = dt.datetime.utcnow()


# ---------------------------------------------------------------------------
# YouTube (ScrapeCreators variant -- alternative to the official-API adapter
# in youtube_client.py / youtube_enrichment.py; app.py prefers this one when
# SCRAPECREATORS_API_KEY is configured, since that's what's actually
# available, and falls back to the official adapter otherwise)
# ---------------------------------------------------------------------------

SPONSOR_MENTION_RE = re.compile(
    r"(?:sponsored by|in partnership with|thanks to)\s+([A-Z][A-Za-z0-9&.\- ]{1,40})", re.IGNORECASE
)


def enrich_youtube_account_sc(session: Session, account: SocialAccount, client: ScrapeCreatorsClient, *, force: bool = False, video_count: int = 30) -> dict[str, str | None]:
    if account.platform != "YouTube":
        raise ValueError(f"enrich_youtube_account_sc only supports YouTube, got platform={account.platform!r}")
    if not account.handle:
        raise ValueError("Social account has no handle to look up")

    channel = client.get_youtube_channel(account.handle, force=force)
    if not channel.get("channelId"):
        raise ScrapeCreatorsError(f"No channel data returned for @{account.handle}")

    avatar = channel.get("avatar")
    avatar_url = None
    if isinstance(avatar, str):
        avatar_url = avatar
    elif isinstance(avatar, dict):
        sources = avatar.get("thumbnails") or avatar.get("sources") or avatar.get("images") or []
        if isinstance(sources, list) and sources:
            avatar_url = sources[-1].get("url") if isinstance(sources[-1], dict) else None
        avatar_url = avatar_url or avatar.get("url")

    account.youtube_channel_id = channel.get("channelId")
    account.avatar_url = avatar_url
    account.display_name_x = channel.get("name")
    account.bio = channel.get("description")
    account.verified = bool(channel.get("isVerified"))
    subs = channel.get("subscriberCount")
    if isinstance(subs, (int, float)):
        account.followers = int(subs)
        account.followers_source = "scrapecreators"

    videos_resp = client.get_youtube_channel_videos(account.handle, count=video_count, force=force)
    videos = videos_resp.get("videos", [])[:video_count]

    views = [float(v["viewCountInt"]) for v in videos if v.get("viewCountInt") is not None]
    likes = [float(v["likeCountInt"]) for v in videos if v.get("likeCountInt") is not None]
    comments = [float(v["commentCountInt"]) for v in videos if v.get("commentCountInt") is not None]

    avg_views = trimmed_mean(views)
    account.avg_views = round(avg_views, 1) if avg_views is not None else None
    med = median(views)
    account.median_views = round(med, 1) if med is not None else None
    avg_likes = trimmed_mean(likes)
    avg_comments = trimmed_mean(comments)
    if avg_views and avg_views > 0:
        parts = [v for v in (avg_likes, avg_comments) if v is not None]
        account.engagement_rate = round((sum(parts) / avg_views) * 100, 3) if parts else None
    else:
        account.engagement_rate = None

    dated = []
    for v in videos:
        raw = v.get("publishedTime")
        if not raw:
            continue
        try:
            dated.append(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(dated) >= 2:
        dated.sort()
        span = (dated[-1] - dated[0]).total_seconds() / 86400
        account.posting_frequency = f"~{len(dated) / span:.2f} videos/day (last {len(dated)} videos, {span:.1f}d span)" if span > 0 else f"{len(dated)} videos within <1 day"
    else:
        account.posting_frequency = None

    descriptions = [v.get("description") or "" for v in videos]
    promo_matches = sum(1 for d in descriptions if PROMO_KEYWORDS_RE.search(d))
    account.promotional_content_ratio = round(promo_matches / len(videos), 3) if videos else None
    account.original_repost_ratio = None  # no repost concept for YouTube uploads

    account.enrichment_error = f"low sample: only {len(views)} recent videos with view counts" if len(views) < 5 else None

    session.query(RecentContent).filter(RecentContent.social_account_id == account.id).delete()
    for v in videos:
        session.add(
            RecentContent(
                social_account_id=account.id,
                post_id=v.get("id"),
                text=v.get("title"),
                posted_at=v.get("publishedTime"),
                views=v.get("viewCountInt"),
                likes=v.get("likeCountInt"),
                replies=v.get("commentCountInt"),
                reposts=None,
                is_repost=False,
                is_pinned=False,
            )
        )

    session.query(PromotedProject).filter(PromotedProject.social_account_id == account.id).delete()
    counts: dict[str, int] = {}
    for d in descriptions:
        for match in SPONSOR_MENTION_RE.findall(d):
            name = match.strip().rstrip(".")
            if name:
                counts[name] = counts.get(name, 0) + 1
    for project, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]:
        session.add(PromotedProject(social_account_id=account.id, project_name=project, mention_count=count))

    account.last_enriched_at = dt.datetime.utcnow()
    return {"country": channel.get("country"), "language": None}


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------


def enrich_tiktok_account(session: Session, account: SocialAccount, client: ScrapeCreatorsClient, *, force: bool = False, video_count: int = 30) -> dict[str, str | None]:
    if account.platform != "TikTok":
        raise ValueError(f"enrich_tiktok_account only supports TikTok, got platform={account.platform!r}")
    if not account.handle:
        raise ValueError("Social account has no handle to look up")

    profile = client.get_tiktok_profile(account.handle, force=force)
    user = profile.get("user")
    if not user:
        raise ScrapeCreatorsError(f"No profile data returned for @{account.handle}")
    stats = profile.get("stats") or {}

    account.avatar_url = _first_url(user.get("avatarLarger"), user.get("avatarMedium"), user.get("avatarThumb"))
    account.display_name_x = user.get("nickname")
    account.bio = user.get("signature")
    account.verified = bool(user.get("verified"))
    followers = stats.get("followerCount")
    if followers is not None:
        account.followers = int(followers)
        account.followers_source = "scrapecreators"
    following = stats.get("followingCount")
    if following is not None:
        account.following = int(following)

    videos_resp = client.get_tiktok_videos(account.handle, force=force)
    aweme_list = videos_resp.get("aweme_list", [])[:video_count]

    views, likes, comments = [], [], []
    records = []
    for v in aweme_list:
        stat = v.get("statistics", {})
        play_count = stat.get("play_count")
        digg_count = stat.get("digg_count")
        comment_count = stat.get("comment_count")
        if play_count is not None:
            views.append(float(play_count))
        if digg_count is not None:
            likes.append(float(digg_count))
        if comment_count is not None:
            comments.append(float(comment_count))
        create_time = v.get("create_time")
        records.append(
            {
                "id": v.get("aweme_id"),
                "text": v.get("desc"),
                "views": play_count,
                "likes": digg_count,
                "comments": comment_count,
                "shares": stat.get("share_count"),
                "posted_at": dt.datetime.utcfromtimestamp(create_time).isoformat() if create_time else None,
            }
        )

    avg_views = trimmed_mean(views)
    account.avg_views = round(avg_views, 1) if avg_views is not None else None
    med = median(views)
    account.median_views = round(med, 1) if med is not None else None
    avg_likes = trimmed_mean(likes)
    avg_comments = trimmed_mean(comments)
    if avg_views and avg_views > 0:
        parts = [v for v in (avg_likes, avg_comments) if v is not None]
        account.engagement_rate = round((sum(parts) / avg_views) * 100, 3) if parts else None
    else:
        account.engagement_rate = None

    dated = [r for r in records if r["posted_at"]]
    if len(dated) >= 2:
        times = sorted(dt.datetime.fromisoformat(r["posted_at"]) for r in dated)
        span = (times[-1] - times[0]).total_seconds() / 86400
        account.posting_frequency = f"~{len(dated) / span:.2f} videos/day (last {len(dated)} videos, {span:.1f}d span)" if span > 0 else f"{len(dated)} videos within <1 day"
    else:
        account.posting_frequency = None

    texts = [r["text"] or "" for r in records]
    promo_matches = sum(1 for t in texts if PROMO_KEYWORDS_RE.search(t))
    account.promotional_content_ratio = round(promo_matches / len(records), 3) if records else None
    account.original_repost_ratio = None  # TikTok API here doesn't distinguish reposts/duets from originals

    account.enrichment_error = f"low sample: only {len(views)} recent videos with view counts" if len(views) < 5 else None

    session.query(RecentContent).filter(RecentContent.social_account_id == account.id).delete()
    for r in records:
        session.add(
            RecentContent(
                social_account_id=account.id,
                post_id=r["id"],
                text=r["text"],
                posted_at=r["posted_at"],
                views=r["views"],
                likes=r["likes"],
                replies=r["comments"],
                reposts=r["shares"],
                is_repost=False,
                is_pinned=False,
            )
        )
    session.query(PromotedProject).filter(PromotedProject.social_account_id == account.id).delete()

    account.last_enriched_at = dt.datetime.utcnow()
    return {"country": None, "language": user.get("language")}
