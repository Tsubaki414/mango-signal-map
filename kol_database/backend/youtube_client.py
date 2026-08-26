"""Cache-first client for the official YouTube Data API v3.

Key is read only from the environment / local .env (YOUTUBE_API_KEY) --
never hardcoded, never logged, never sent to the frontend. Responses are
cached to disk (same pattern as rapidx_client.py) so repeat page loads
don't burn quota. YouTube Data API v3 has a daily quota (default 10,000
units/day); channels.list/videos.list cost 1 unit each, playlistItems.list
costs 1 unit per call -- a full channel enrichment (profile + uploads list +
video stats) costs roughly 3 units, so 10,000 units/day comfortably covers
hundreds of creators, but caching still matters for repeat page loads.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import certifi

from .env import get_env

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "youtube"
API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeError(RuntimeError):
    pass


class YouTubeClient:
    def __init__(self, cache_dir: Path = CACHE_DIR, min_interval_seconds: float = 0.2) -> None:
        self.api_key = get_env("YOUTUBE_API_KEY")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval_seconds = min_interval_seconds
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._last_request_at = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        cacheable = {k: v for k, v in params.items() if k != "key"}
        material = json.dumps([endpoint, sorted(cacheable.items())], ensure_ascii=False).encode()
        digest = hashlib.sha256(material).hexdigest()[:20]
        return self.cache_dir / f"{endpoint}-{digest}.json"

    def _get(self, endpoint: str, params: dict[str, Any], *, force: bool = False, cache_ttl_seconds: int = 6 * 3600) -> dict[str, Any]:
        if not self.api_key:
            raise YouTubeError("Missing YOUTUBE_API_KEY. Set it in the repo-root .env before enriching YouTube creators.")

        cache_path = self._cache_path(endpoint, params)
        if cache_path.exists() and not force:
            age = time.time() - cache_path.stat().st_mtime
            if age <= cache_ttl_seconds:
                return json.loads(cache_path.read_text(encoding="utf-8"))

        wait = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        query = {**params, "key": self.api_key}
        url = f"{API_BASE}/{endpoint}?{urlencode(query)}"
        request = Request(url, method="GET")
        try:
            with urlopen(request, timeout=45, context=self.ssl_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise YouTubeError(f"YouTube Data API HTTP {exc.code}: {body}") from exc
        except (URLError, TimeoutError) as exc:
            raise YouTubeError(f"YouTube Data API request failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()

        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def get_channel_by_handle(self, handle: str, **kwargs: Any) -> dict[str, Any]:
        handle = handle.lstrip("@")
        return self._get(
            "channels",
            {"part": "snippet,statistics,brandingSettings,contentDetails", "forHandle": f"@{handle}"},
            **kwargs,
        )

    def get_channel_by_id(self, channel_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._get(
            "channels",
            {"part": "snippet,statistics,brandingSettings,contentDetails", "id": channel_id},
            **kwargs,
        )

    def search_channel(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self._get("search", {"part": "snippet", "type": "channel", "q": query, "maxResults": 1}, **kwargs)

    def get_playlist_items(self, playlist_id: str, max_results: int = 15, **kwargs: Any) -> dict[str, Any]:
        return self._get(
            "playlistItems",
            {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": max_results},
            **kwargs,
        )

    def get_videos(self, video_ids: list[str], **kwargs: Any) -> dict[str, Any]:
        if not video_ids:
            return {"items": []}
        return self._get(
            "videos",
            {"part": "snippet,statistics,contentDetails", "id": ",".join(video_ids)},
            **kwargs,
        )


def parse_channel_ref(profile_url: str | None, handle: str | None) -> tuple[str, str]:
    """Return (kind, value) where kind is "id" | "handle" | "query".
    YouTube profile URLs come in several shapes: /channel/UC..., /@handle,
    /c/CustomName, /user/legacyName, or a bare vanity path -- there is no
    single reliable way to resolve all of them without a query fallback."""
    if profile_url:
        path = urlparse(profile_url.strip()).path.strip("/")
        parts = path.split("/") if path else []
        if len(parts) >= 2 and parts[0] == "channel":
            return "id", parts[1]
        if parts and parts[0].startswith("@"):
            return "handle", parts[0]
        if len(parts) >= 2 and parts[0] in {"c", "user"}:
            return "query", parts[1]
        if parts:
            return "query", parts[0]
    if handle:
        return ("handle", handle) if handle.startswith("@") else ("query", handle)
    return "query", ""
