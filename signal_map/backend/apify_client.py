"""Apify client for YouTube channel data.

YouTube creators are a real segment of the priced inventory, and many carried
no description at all -- so neither the market nor the audience derivation had
anything to read for them.

Actor choice was decided by testing, not by store ranking:

* ``apidojo/youtube-channel-information-scraper`` returned nothing for 14 of
  23 channels that all resolve fine in a browser (HTTP 200). Cheap, but
  unreliable on this set.
* ``streamers/youtube-channel-scraper`` returned every one of them, and also
  yields recent **video titles**, ``channelLocation``, and subscriber counts.

Video titles matter: a channel description is marketing copy, while its recent
titles are what it actually publishes -- much better evidence for judging who
watches. ``channelLocation`` is a genuine country signal, stronger than
guessing from the script a bio is written in.

Cost control (AGENTS.md): channels are fetched in chunks, results are cached
to disk, and the caller caps the batch. A cached channel is never re-sent.

Reads ``APIFY_TOKEN`` from the environment only -- never logged, never written
into a cache file.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "apify"
CACHE_TTL_SECONDS = 30 * 24 * 3600
ACTOR = "streamers~youtube-channel-scraper"
BASE_URL = "https://api.apify.com/v2"

#: Recent videos per channel. Enough to characterise output; more would raise
#: cost without changing a classification that is already coarse.
VIDEOS_PER_CHANNEL = 5

#: One sync run returns whatever the dataset holds when it finishes, so a slow
#: channel can truncate a large batch. Small chunks keep failures small.
CHUNK_SIZE = 8

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class ApifyError(RuntimeError):
    pass


@dataclass
class YouTubeChannel:
    """The subset of the actor payload Signal Map actually uses."""

    input_url: str
    handle: str | None = None
    name: str | None = None
    description: str | None = None
    location: str | None = None
    subscriber_count: int | None = None
    total_videos: int | None = None
    total_views: int | None = None
    is_verified: bool | None = None
    video_titles: list[str] = field(default_factory=list)
    from_cache: bool = False

    @property
    def content_text(self) -> str | None:
        """Recent video titles as one string -- what the channel publishes."""
        return " | ".join(self.video_titles) if self.video_titles else None

    @classmethod
    def from_items(cls, input_url: str, items: list[dict]) -> "YouTubeChannel":
        """Fold the actor's one-row-per-video output into one channel.

        Channel fields repeat on every row, so the first row carries them.
        """
        head = items[0]
        titles = [str(item["title"]).strip() for item in items if item.get("title")]
        return cls(
            input_url=input_url,
            handle=head.get("channelUsername"),
            name=head.get("channelName"),
            description=head.get("channelDescription"),
            location=head.get("channelLocation"),
            subscriber_count=head.get("numberOfSubscribers"),
            total_videos=head.get("channelTotalVideos"),
            total_views=head.get("channelTotalViews"),
            is_verified=head.get("isChannelVerified"),
            video_titles=list(dict.fromkeys(titles))[:VIDEOS_PER_CHANNEL],
        )


def _token() -> str:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise ApifyError("APIFY_TOKEN is not set in the repo-root .env")
    return token


def normalize_channel_url(url: str | None, handle: str | None = None) -> str | None:
    """Canonical channel URL, or None when there is nothing to resolve.

    Accepts the ``/@handle``, ``/channel/UC...`` and legacy ``/c/name`` forms
    -- all three appear in the inherited data and the actor handles each.
    """
    candidate = (url or "").strip()
    if "youtube.com" in candidate:
        candidate = candidate.split("?")[0].rstrip("/")
        match = re.search(
            r"(https?://(?:www\.)?youtube\.com/(?:@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+))",
            candidate,
        )
        if match:
            return match.group(1)
    if handle:
        return f"https://www.youtube.com/@{handle.strip().lstrip('@')}"
    return None


def _cache_path(url: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.@-]", "_", url.rsplit("/", 1)[-1])[:70]
    return CACHE_DIR / f"channel-{safe}.json"


def _read_cache(url: str) -> YouTubeChannel | None:
    path = _cache_path(url)
    if not path.exists() or time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
        return None
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not items:
        return None
    channel = YouTubeChannel.from_items(url, items)
    channel.from_cache = True
    return channel


def _fetch_chunk(urls: list[str], timeout: int) -> list[dict]:
    endpoint = f"{BASE_URL}/acts/{ACTOR}/run-sync-get-dataset-items?token={_token()}"
    payload = {
        "startUrls": [{"url": url} for url in urls],
        "maxResults": VIDEOS_PER_CHANNEL,
        # Shorts and streams would multiply cost without adding signal the
        # regular uploads do not already carry.
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
            items = json.loads(response.read().decode())
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise ApifyError(f"Apify HTTP {exc.code}: {body}") from exc
    except (URLError, TimeoutError) as exc:
        raise ApifyError(f"Apify request failed: {str(exc)[:200]}") from exc
    return items if isinstance(items, list) else []


def fetch_youtube_channels(
    urls: list[str], *, force: bool = False, timeout: int = 600
) -> dict[str, YouTubeChannel]:
    """Fetch many channels, chunked. Returns ``{requested_url: channel}``.

    A channel the actor cannot resolve is simply absent from the result rather
    than raising -- one dead channel must not lose the batch.
    """
    wanted = [url for url in dict.fromkeys(urls) if url]
    results: dict[str, YouTubeChannel] = {}

    if not force:
        for url in wanted:
            cached = _read_cache(url)
            if cached:
                results[url] = cached

    missing = [url for url in wanted if url not in results]
    if not missing:
        return results

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for start in range(0, len(missing), CHUNK_SIZE):
        chunk = missing[start : start + CHUNK_SIZE]
        try:
            items = _fetch_chunk(chunk, timeout)
        except ApifyError:
            if start == 0:
                raise  # a first-chunk failure is auth/quota, not a bad channel
            continue

        # ``inputChannelUrl`` echoes exactly what was submitted, which survives
        # the handle-case and /c/ -> /@ redirects that broke matching on the
        # actor's own canonical URL.
        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            key = item.get("inputChannelUrl") or item.get("input") or item.get("channelUrl")
            if key in chunk:
                grouped[key].append(item)

        for url, channel_items in grouped.items():
            results[url] = YouTubeChannel.from_items(url, channel_items)
            _cache_path(url).write_text(
                json.dumps(channel_items, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    return results
