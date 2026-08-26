"""Cache-first client for the X/Twitter API on RapidAPI (twitter241.p.rapidapi.com).

All X/Twitter public-data reads for Mango KOL database go through this
class. The key is read only from the environment / local .env
(RAPID_X_API_KEY, matching the rest of this repo's convention, or
RAPIDAPI_KEY) -- never hardcoded, never logged, never sent to the frontend.
Responses are cached to disk so repeatedly opening the app does not burn
quota; force a refresh explicitly via the enrich endpoints.
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

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "rapidx"


class RapidXError(RuntimeError):
    pass


class RapidXClient:
    def __init__(self, cache_dir: Path = CACHE_DIR, min_interval_seconds: float = 0.35) -> None:
        self.api_key = get_env("RAPID_X_API_KEY", "RAPIDAPI_KEY")
        self.host = get_env("RAPID_X_HOST") or "twitter241.p.rapidapi.com"
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

    def get(
        self,
        endpoint: str,
        params: dict[str, Any],
        *,
        force: bool = False,
        cache_ttl_seconds: int = 6 * 3600,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RapidXError(
                "Missing RAPID_X_API_KEY (or RAPIDAPI_KEY). Set it in the repo-root .env before enriching."
            )

        cache_path = self._cache_path(endpoint, params)
        if cache_path.exists() and not force:
            age = time.time() - cache_path.stat().st_mtime
            if age <= cache_ttl_seconds:
                return json.loads(cache_path.read_text(encoding="utf-8"))

        wait = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        url = f"https://{self.host}/{endpoint.lstrip('/')}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Content-Type": "application/json",
                "x-rapidapi-host": self.host,
                "x-rapidapi-key": self.api_key,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=45, context=self.ssl_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RapidXError(f"Rapid X HTTP {exc.code}: {body}") from exc
        except (URLError, TimeoutError) as exc:
            raise RapidXError(f"Rapid X request failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()

        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def get_user(self, username: str, **kwargs: Any) -> dict[str, Any]:
        return self.get("user", {"username": username.lstrip("@")}, **kwargs)

    def get_user_tweets(self, user_id: str, count: int = 30, **kwargs: Any) -> dict[str, Any]:
        return self.get("user-tweets", {"user": user_id, "count": count}, **kwargs)
