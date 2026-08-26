from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi


class RapidXError(RuntimeError):
    pass


def _read_env_file(path: str | Path) -> dict[str, str]:
    """Read a small dotenv file without mutating the process environment."""
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class RapidXClient:
    """Small, cache-first adapter for the Twttr API on RapidAPI.

    All X/Twitter reads in this project flow through this class. Credentials are
    accepted only via environment variables and are never persisted in cache.
    """

    def __init__(
        self,
        api_key: str | None = None,
        host: str | None = None,
        cache_dir: str | Path = "data/cache/rapidx",
        min_interval_seconds: float = 0.35,
        env_file: str | Path = ".env",
        ca_bundle: str | Path | None = None,
    ) -> None:
        file_env = _read_env_file(env_file)
        self.api_key = (
            api_key
            or os.getenv("RAPID_X_API_KEY")
            or os.getenv("RAPIDAPI_KEY")
            or file_env.get("RAPID_X_API_KEY")
            or file_env.get("RAPIDAPI_KEY")
        )
        self.host = host or os.getenv("RAPID_X_HOST") or file_env.get("RAPID_X_HOST") or "twitter241.p.rapidapi.com"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval_seconds = min_interval_seconds
        configured_ca = ca_bundle or os.getenv("SSL_CERT_FILE") or file_env.get("SSL_CERT_FILE")
        self.ssl_context = ssl.create_default_context(cafile=str(configured_ca or certifi.where()))
        self._last_request_at = 0.0
        if not self.api_key:
            raise RapidXError("Missing RAPID_X_API_KEY or RAPIDAPI_KEY")

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
        cache_ttl_seconds: int = 86_400,
    ) -> dict[str, Any]:
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

    def get_users_by_ids(self, user_ids: list[str], **kwargs: Any) -> dict[str, Any]:
        """Resolve a batch of X rest IDs to profiles through Rapid X."""
        if not user_ids:
            return {"result": []}
        return self.get("get-users-v2", {"users": ",".join(str(value) for value in user_ids)}, **kwargs)

    def search(self, query: str, search_type: str = "Top", count: int = 20, cursor: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Search X through Rapid X; use Top for mixed people/post discovery."""
        params: dict[str, Any] = {"query": query, "type": search_type, "count": count}
        if cursor:
            params["cursor"] = cursor
        return self.get("search", params, **kwargs)

    def get_followings(self, user_id: str, count: int = 100, cursor: str | None = None, **kwargs: Any) -> dict[str, Any]:
        params: dict[str, Any] = {"user": user_id, "count": count}
        if cursor:
            params["cursor"] = cursor
        return self.get("followings", params, **kwargs)

    def get_following_ids(self, username: str, count: int = 500, cursor: str | None = None, **kwargs: Any) -> dict[str, Any]:
        params: dict[str, Any] = {"username": username.lstrip("@"), "count": count}
        if cursor:
            params["cursor"] = cursor
        return self.get("following-ids", params, **kwargs)

    def get_follower_ids(self, username: str, count: int = 500, cursor: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Return follower IDs through Rapid X's cursor-paginated endpoint."""
        params: dict[str, Any] = {"username": username.lstrip("@"), "count": count}
        if cursor:
            params["cursor"] = cursor
        return self.get("followers-ids", params, **kwargs)

    def get_user_tweets(self, user_id: str, count: int = 40, cursor: str | None = None, **kwargs: Any) -> dict[str, Any]:
        params: dict[str, Any] = {"user": user_id, "count": count}
        if cursor:
            params["cursor"] = cursor
        return self.get("user-tweets", params, **kwargs)
