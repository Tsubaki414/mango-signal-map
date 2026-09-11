from __future__ import annotations

import json
import os
import ssl
import time
from http.client import IncompleteRead
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from .rapid_x import _read_env_file


class ApifyError(RuntimeError):
    """An Apify API request or Actor run failed."""


TERMINAL_RUN_STATUSES = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}


def actor_api_id(actor_id: str) -> str:
    """Convert Store-style ``owner/actor`` IDs to Apify API IDs."""

    value = actor_id.strip()
    if not value:
        raise ValueError("actor_id must not be empty")
    return value.replace("/", "~", 1)


class ApifyClient:
    """Small Apify API client with credential-safe errors and bounded runs.

    The token is sent in the Authorization header, never as a query parameter,
    so it cannot leak into cached URLs, run manifests, or exception messages.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        env_file: str | Path = ".env",
        base_url: str = "https://api.apify.com/v2",
        ca_bundle: str | Path | None = None,
        timeout_seconds: int = 60,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        file_env = _read_env_file(env_file)
        self.token = (
            token
            or os.getenv("APIFY_TOKEN")
            or os.getenv("APIFY_API_TOKEN")
            or file_env.get("APIFY_TOKEN")
            or file_env.get("APIFY_API_TOKEN")
        )
        if not self.token:
            raise ApifyError("Missing APIFY_TOKEN or APIFY_API_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        configured_ca = ca_bundle or os.getenv("SSL_CERT_FILE") or file_env.get("SSL_CERT_FILE")
        self.ssl_context = ssl.create_default_context(cafile=str(configured_ca or certifi.where()))
        self._opener = opener or urlopen

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        query = {
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in (params or {}).items()
            if value is not None
        }
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode(query)}"
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds, context=self.ssl_context) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                message = parsed.get("error", {}).get("message") or parsed.get("message")
            except json.JSONDecodeError:
                message = raw[:300]
            raise ApifyError(f"Apify HTTP {exc.code}: {message or 'request failed'}") from exc
        except (URLError, TimeoutError, IncompleteRead) as exc:
            raise ApifyError(f"Apify request failed: {exc}") from exc

    @staticmethod
    def _data(payload: Any) -> Any:
        return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload

    def _get_with_retries(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        attempts: int = 4,
    ) -> Any:
        """Retry idempotent GETs only; Actor-start POSTs are never replayed."""

        last_error: ApifyError | None = None
        for attempt in range(attempts):
            try:
                return self._request("GET", path, params=params)
            except ApifyError as exc:
                last_error = exc
                message = str(exc)
                transient = "request failed" in message or any(
                    f"HTTP {code}" in message for code in (429, 500, 502, 503, 504)
                )
                if not transient or attempt == attempts - 1:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise last_error or ApifyError("Apify GET failed")

    def get_current_user(self) -> dict[str, Any]:
        return self._data(self._get_with_retries("users/me"))

    def get_actor(self, actor_id: str) -> dict[str, Any]:
        return self._data(self._get_with_retries(f"acts/{actor_api_id(actor_id)}"))

    def get_build(self, build_id: str) -> dict[str, Any]:
        return self._data(self._get_with_retries(f"actor-builds/{build_id}"))

    def start_actor(
        self,
        actor_id: str,
        actor_input: dict[str, Any],
        *,
        build: str | None = None,
        timeout_seconds: int = 600,
        memory_mb: int | None = None,
        max_total_charge_usd: float = 2.0,
    ) -> dict[str, Any]:
        if max_total_charge_usd <= 0:
            raise ValueError("max_total_charge_usd must be positive")
        params: dict[str, Any] = {
            "build": build,
            "timeout": timeout_seconds,
            "memory": memory_mb,
            "maxTotalChargeUsd": max_total_charge_usd,
        }
        return self._data(
            self._request(
                "POST",
                f"acts/{actor_api_id(actor_id)}/runs",
                params=params,
                payload=actor_input,
            )
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._data(self._get_with_retries(f"actor-runs/{run_id}"))

    def list_runs(self, *, limit: int = 10, desc: bool = True) -> list[dict[str, Any]]:
        payload = self._data(
            self._get_with_retries("actor-runs", params={"limit": limit, "desc": desc})
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ApifyError("Actor runs response had an unexpected shape")
        return [item for item in payload["items"] if isinstance(item, dict)]

    def wait_for_run(
        self,
        run_id: str,
        *,
        poll_interval_seconds: float = 4.0,
        wait_timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + wait_timeout_seconds
        while True:
            run = self.get_run(run_id)
            status = str(run.get("status") or "")
            if status in TERMINAL_RUN_STATUSES:
                if status != "SUCCEEDED":
                    message = run.get("statusMessage") or "no status message"
                    raise ApifyError(f"Actor run {run_id} ended as {status}: {message}")
                return run
            if time.monotonic() >= deadline:
                raise ApifyError(f"Timed out waiting for Actor run {run_id}")
            time.sleep(poll_interval_seconds)

    def get_dataset_items(
        self,
        dataset_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        clean: bool = True,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        payload = self._get_with_retries(
            f"datasets/{dataset_id}/items",
            params={"limit": limit, "offset": offset, "clean": clean, "format": "json"},
        )
        if not isinstance(payload, list):
            raise ApifyError("Dataset items response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    def get_key_value_record(self, store_id: str, key: str) -> Any:
        return self._get_with_retries(f"key-value-stores/{store_id}/records/{key}")
