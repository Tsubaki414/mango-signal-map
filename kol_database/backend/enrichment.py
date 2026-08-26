"""Rapid X enrichment: pulls a profile + recent tweets for one X SocialAccount
and turns them into stable metrics.

Averaging method (documented here + README): we fetch the most recent
`count` tweets, drop the pinned tweet (it's curated, not representative),
and split the rest into original posts vs reposts (retweets). Over the
original posts we compute both:
  - median_views: robust midpoint, unaffected by one outlier.
  - avg_views: a trimmed mean (drop the single highest and single lowest
    value when we have >=5 eligible posts) so one viral post or one dead
    post doesn't swing the number. Falls back to a plain mean below that.
Engagement rate is (avg likes + avg replies + avg reposts) / avg views over
the same trimmed set. Everything here is a snapshot -- if Rapid X has no
usable posts we leave the metric fields as None rather than inventing one.
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import PromotedProject, RecentContent, SocialAccount
from .rapidx_client import RapidXClient, RapidXError

CASHTAG_RE = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,9}\b")
PROMO_KEYWORDS_RE = re.compile(
    r"\b(ad|sponsored|partnership|partnered|airdrop|giveaway|use code|discount code|"
    r"affiliate|collab|brand deal|presale|whitelist|mint\s*now|sign\s*up|link in bio)\b"
    r"|#ad\b",
    re.IGNORECASE,
)


@dataclass
class TweetRecord:
    post_id: str
    text: str
    created_at_raw: str | None
    created_at: dt.datetime | None
    views: int | None
    likes: int | None
    replies: int | None
    reposts: int | None
    is_repost: bool
    is_pinned: bool


def _parse_created_at(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def _unwrap_tweet(tweet: dict) -> dict:
    # Some responses wrap a restricted tweet as {"__typename":
    # "TweetWithVisibilityResults", "tweet": {...}}; unwrap one level.
    if tweet.get("__typename") == "TweetWithVisibilityResults" and "tweet" in tweet:
        return tweet["tweet"]
    return tweet


def extract_tweets(tweets_resp: dict) -> list[TweetRecord]:
    instructions = tweets_resp.get("result", {}).get("timeline", {}).get("instructions", [])
    items: list[tuple[dict, bool]] = []
    for ins in instructions:
        if ins.get("type") == "TimelinePinEntry" and "entry" in ins:
            item = ins["entry"].get("content", {}).get("itemContent", {})
            if item.get("itemType") == "TimelineTweet":
                items.append((item, True))
        elif "entries" in ins:
            for entry in ins["entries"]:
                item = entry.get("content", {}).get("itemContent", {})
                if item.get("itemType") == "TimelineTweet":
                    items.append((item, False))

    records: list[TweetRecord] = []
    for item, is_pinned in items:
        tweet = _unwrap_tweet(item.get("tweet_results", {}).get("result", {}) or {})
        legacy = tweet.get("legacy")
        if not legacy:
            continue
        post_id = str(tweet.get("rest_id") or legacy.get("id_str") or "")
        views_raw = (tweet.get("views") or {}).get("count")
        views = int(views_raw) if views_raw and str(views_raw).isdigit() else None
        created_raw = legacy.get("created_at")
        records.append(
            TweetRecord(
                post_id=post_id,
                text=legacy.get("full_text") or "",
                created_at_raw=created_raw,
                created_at=_parse_created_at(created_raw),
                views=views,
                likes=legacy.get("favorite_count"),
                replies=legacy.get("reply_count"),
                reposts=legacy.get("retweet_count"),
                is_repost=bool(legacy.get("retweeted_status_result")),
                is_pinned=is_pinned,
            )
        )
    return records


def _trimmed_mean(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) >= 5:
        trimmed = sorted(values)[1:-1]
    else:
        trimmed = values
    return statistics.mean(trimmed) if trimmed else None


@dataclass
class AccountStats:
    avg_views: float | None
    median_views: float | None
    engagement_rate: float | None
    posting_frequency: str | None
    original_repost_ratio: float | None
    promotional_content_ratio: float | None
    sample_size: int
    original_sample_size: int
    low_sample: bool


def compute_stats(records: list[TweetRecord]) -> AccountStats:
    non_pinned = [r for r in records if not r.is_pinned]
    originals = [r for r in non_pinned if not r.is_repost]
    originals_with_views = [r for r in originals if r.views is not None]

    views = [float(r.views) for r in originals_with_views]
    likes = [float(r.likes) for r in originals_with_views if r.likes is not None]
    replies = [float(r.replies) for r in originals_with_views if r.replies is not None]
    reposts = [float(r.reposts) for r in originals_with_views if r.reposts is not None]

    avg_views = _trimmed_mean(views)
    median_views = statistics.median(views) if views else None
    avg_likes = _trimmed_mean(likes)
    avg_replies = _trimmed_mean(replies)
    avg_reposts = _trimmed_mean(reposts)

    engagement_rate = None
    if avg_views and avg_views > 0:
        parts = [v for v in (avg_likes, avg_replies, avg_reposts) if v is not None]
        engagement_rate = round((sum(parts) / avg_views) * 100, 3)

    dated = [r for r in non_pinned if r.created_at is not None]
    posting_frequency = None
    if len(dated) >= 2:
        dated_sorted = sorted(dated, key=lambda r: r.created_at)
        span = (dated_sorted[-1].created_at - dated_sorted[0].created_at).total_seconds() / 86400
        if span > 0:
            freq = len(dated) / span
            posting_frequency = f"~{freq:.2f} posts/day (last {len(dated)} posts, {span:.1f}d span)"
        else:
            posting_frequency = f"{len(dated)} posts within <1 day"

    original_repost_ratio = round(len(originals) / len(non_pinned), 3) if non_pinned else None

    promo_matches = sum(
        1 for r in originals if PROMO_KEYWORDS_RE.search(r.text) or CASHTAG_RE.search(r.text)
    )
    promotional_content_ratio = round(promo_matches / len(originals), 3) if originals else None

    return AccountStats(
        avg_views=round(avg_views, 1) if avg_views is not None else None,
        median_views=round(median_views, 1) if median_views is not None else None,
        engagement_rate=engagement_rate,
        posting_frequency=posting_frequency,
        original_repost_ratio=original_repost_ratio,
        promotional_content_ratio=promotional_content_ratio,
        sample_size=len(non_pinned),
        original_sample_size=len(originals),
        low_sample=len(originals_with_views) < 5,
    )


def extract_promoted_projects(records: list[TweetRecord], top_n: int = 8) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        if r.is_repost:
            continue
        for match in CASHTAG_RE.findall(r.text):
            counts[match] = counts.get(match, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return dict(ranked)


def enrich_x_account(
    session: Session, account: SocialAccount, client: RapidXClient, *, force: bool = False, tweet_count: int = 30
) -> None:
    if account.platform.upper() not in {"X", "TWITTER", "X(TWITTER)"}:
        raise ValueError(f"enrich_x_account only supports X accounts, got platform={account.platform!r}")
    if not account.handle:
        raise ValueError("Social account has no handle to look up")

    try:
        profile_resp = client.get_user(account.handle, force=force)
        user = profile_resp.get("result", {}).get("data", {}).get("user", {}).get("result")
        if not user:
            raise RapidXError(f"No profile data returned for @{account.handle} (account may be suspended/renamed)")

        legacy = user.get("legacy", {})
        core = user.get("core", {})
        account.x_rest_id = user.get("rest_id")
        account.avatar_url = (user.get("avatar") or {}).get("image_url")
        account.display_name_x = core.get("name") or legacy.get("name")
        account.bio = legacy.get("description")
        account.verified = bool(user.get("is_blue_verified") or (user.get("verification") or {}).get("verified"))
        account.account_created_at = core.get("created_at") or legacy.get("created_at")
        followers = legacy.get("followers_count") or legacy.get("normal_followers_count")
        if followers is not None:
            account.followers = int(followers)
            account.followers_source = "rapidx"
        following = legacy.get("friends_count")
        if following is not None:
            account.following = int(following)

        tweets_resp = client.get_user_tweets(account.x_rest_id, count=tweet_count, force=force)
        records = extract_tweets(tweets_resp)
        stats = compute_stats(records)

        account.avg_views = stats.avg_views
        account.median_views = stats.median_views
        account.engagement_rate = stats.engagement_rate
        account.posting_frequency = stats.posting_frequency
        account.original_repost_ratio = stats.original_repost_ratio
        account.promotional_content_ratio = stats.promotional_content_ratio
        account.enrichment_error = (
            f"low sample: only {stats.original_sample_size} original posts with view counts" if stats.low_sample else None
        )

        session.query(RecentContent).filter(RecentContent.social_account_id == account.id).delete()
        for r in records[:tweet_count]:
            session.add(
                RecentContent(
                    social_account_id=account.id,
                    post_id=r.post_id,
                    text=r.text,
                    posted_at=r.created_at_raw,
                    views=r.views,
                    likes=r.likes,
                    replies=r.replies,
                    reposts=r.reposts,
                    is_repost=r.is_repost,
                    is_pinned=r.is_pinned,
                )
            )

        session.query(PromotedProject).filter(PromotedProject.social_account_id == account.id).delete()
        for project, count in extract_promoted_projects(records).items():
            session.add(PromotedProject(social_account_id=account.id, project_name=project, mention_count=count))

        account.last_enriched_at = dt.datetime.utcnow()
    except RapidXError as exc:
        account.enrichment_error = str(exc)
        account.last_enriched_at = dt.datetime.utcnow()
        raise
