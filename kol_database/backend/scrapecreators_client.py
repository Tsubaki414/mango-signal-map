"""Cache-first client for the ScrapeCreators unified social API
(https://api.scrapecreators.com), used for YouTube, Instagram, and TikTok
enrichment (X/Twitter still goes through Rapid X exclusively -- see
rapidx_client.py).

Key is read only from the environment / local .env (SCRAPECREATORS_API_KEY)
-- never hardcoded, never logged, never sent to the frontend. Responses are
cached to disk so repeated page loads don't burn credits; ScrapeCreators
credits do not reset/refill automatically on a free plan, so caching here
matters more than usual.

Field mappings below were confirmed against live responses at build time
(2026-08), not just the docs summary -- e.g. TikTok's `stats` object is
top-level, not nested under `user.stats` the way the docs page describes it.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from .env import get_env

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "scrapecreators"
API_BASE = "https://api.scrapecreators.com"


class ScrapeCreatorsError(RuntimeError):
    pass


class ScrapeCreatorsClient:
    def __init__(self, cache_dir: Path = CACHE_DIR, min_interval_seconds: float = 0.3) -> None:
        self.api_key = get_env("SCRAPECREATORS_API_KEY")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval_seconds = min_interval_seconds
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._last_request_at = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        material = json.dumps([endpoint, sorted(params.items())], ensure_ascii=False).encode()
        digest = hashlib.sha256(material).hexdigest()[:20]
        return self.cache_dir / f"{endpoint.strip('/').replace('/', '_')}-{digest}.json"

    def _get(self, endpoint: str, params: dict[str, Any], *, force: bool = False, cache_ttl_seconds: int = 6 * 3600) -> dict[str, Any]:
        if not self.api_key:
            raise ScrapeCreatorsError(
                "Missing SCRAPECREATORS_API_KEY. Set it in the repo-root .env before enriching YouTube/Instagram/TikTok."
            )

        cache_path = self._cache_path(endpoint, params)
        if cache_path.exists() and not force:
            age = time.time() - cache_path.stat().st_mtime
            if age <= cache_ttl_seconds:
                return json.loads(cache_path.read_text(encoding="utf-8"))

        wait = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        url = f"{API_BASE}/{endpoint.lstrip('/')}?{urlencode(params)}"
        request = Request(url, headers={"x-api-key": self.api_key}, method="GET")
        try:
            with urlopen(request, timeout=45, context=self.ssl_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise ScrapeCreatorsError(f"ScrapeCreators HTTP {exc.code}: {body}") from exc
        except (URLError, TimeoutError) as exc:
            raise ScrapeCreatorsError(f"ScrapeCreators request failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()

        if not payload.get("success", True):
            raise ScrapeCreatorsError(f"ScrapeCreators returned success=false for {endpoint}: {payload}")

        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    # -- Instagram: profile call already embeds recent posts, 1 credit total --
    def get_instagram_profile(self, handle: str, **kwargs: Any) -> dict[str, Any]:
        return self._get("v1/instagram/profile", {"handle": handle.lstrip("@")}, **kwargs)

    # -- YouTube: channel info and recent videos are two separate calls --
    def get_youtube_channel(self, handle: str, **kwargs: Any) -> dict[str, Any]:
        return self._get("v1/youtube/channel", self._youtube_ref_param(handle), **kwargs)

    def get_youtube_channel_videos(self, handle: str, count: int = 30, **kwargs: Any) -> dict[str, Any]:
        return self._get(
            "v1/youtube/channel-videos",
            {**self._youtube_ref_param(handle), "includeExtras": "true"},
            **kwargs,
        )

    @staticmethod
    def _youtube_ref_param(handle: str) -> dict[str, str]:
        """Our stored "handle" is sometimes actually a channel id (e.g. from
        a youtube.com/channel/UC.../ profile URL, which has no @handle at
        all) -- send it as channelId in that case instead of handle, which
        the API would otherwise try to resolve as a literal @-handle and
        fail to find."""
        value = handle.lstrip("@")
        if value.startswith("UC") and len(value) == 24:
            return {"channelId": value}
        return {"handle": value}

    # -- TikTok: profile and videos are two separate calls --
    def get_tiktok_profile(self, handle: str, **kwargs: Any) -> dict[str, Any]:
        return self._get("v1/tiktok/profile", {"handle": handle.lstrip("@")}, **kwargs)

    def get_tiktok_videos(self, handle: str, **kwargs: Any) -> dict[str, Any]:
        return self._get("v3/tiktok/profile/videos", {"handle": handle.lstrip("@")}, **kwargs)
