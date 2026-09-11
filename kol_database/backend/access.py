"""Public-read, internal-write access boundary for the Cockpit.

There is intentionally no password page. GET, HEAD, and OPTIONS requests remain
public while state-changing requests require either a short-lived signed access
session or ``INTERNAL_ACCESS_TOKEN`` in an API header. If the token is missing,
writes fail closed instead of silently becoming public.

This is defense in depth, not a substitute for organisation SSO/VPN or a
private hosting boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse


ACCESS_COOKIE_NAME = "mango_internal_access"
ACCESS_LINK_PATH = "/internal/access"
ACCESS_SESSION_PATH = "/internal/session"
ACCESS_STATUS_PATH = "/internal/access-status"
PUBLIC_PATHS = {"/healthz"}
SIGNING_PURPOSE = b"mango-internal-access-cookie-v1"


def configured_access_token() -> str | None:
    value = os.environ.get("INTERNAL_ACCESS_TOKEN", "").strip()
    return value or None


def _cookie_value(token: str) -> str:
    return hmac.new(token.encode("utf-8"), SIGNING_PURPOSE, hashlib.sha256).hexdigest()


def _safe_next(value: str | None) -> str:
    candidate = value or "/"
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/"
    return candidate


def access_link_signature(token: str, expires: int, next_path: str) -> str:
    payload = f"{expires}:{_safe_next(next_path)}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def build_access_link(base_url: str, token: str, *, expires: int, next_path: str = "/") -> str:
    safe_next = _safe_next(next_path)
    signature = access_link_signature(token, expires, safe_next)
    # The credential-bearing values live only in the URL fragment. Browsers
    # never send fragments in HTTP requests, reverse-proxy logs, or Referer.
    fragment = urlencode({"expires": expires, "next": safe_next, "sig": signature})
    return f"{base_url.rstrip('/')}{ACCESS_LINK_PATH}#{fragment}"


def _request_has_access(request: Request, token: str) -> bool:
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    header_token = request.headers.get("x-internal-access-token", "")
    cookie = request.cookies.get(ACCESS_COOKIE_NAME, "")
    return any(
        (
            candidate
            and hmac.compare_digest(candidate, expected)
            for candidate, expected in (
                (bearer, token),
                (header_token, token),
                (cookie, _cookie_value(token)),
            )
        )
    )


def _harden_response(response, *, protected: bool = True):
    response.headers["Cache-Control"] = "private, no-store" if protected else "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if "content-security-policy" not in response.headers:
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    return response


def install_access_gate(app: FastAPI) -> None:
    @app.middleware("http")
    async def internal_access_gate(request: Request, call_next):
        token = configured_access_token()
        has_write_access = token is not None and _request_has_access(request, token)
        # Endpoints use the same authenticated state to redact internal-only
        # fields from otherwise-public GET responses.
        request.state.write_access = has_write_access
        if request.url.path == ACCESS_STATUS_PATH and request.method == "GET":
            return _harden_response(
                JSONResponse(
                    {
                        "read_access": "public",
                        "write_access": has_write_access,
                        "write_access_configured": token is not None,
                        "mode": "full_access" if has_write_access else "public_read_only",
                    }
                ),
                protected=has_write_access,
            )
        if token is None:
            if request.url.path in {ACCESS_LINK_PATH, ACCESS_SESSION_PATH}:
                return _harden_response(
                    JSONResponse(
                        {"detail": "Internal write access is not configured"},
                        status_code=503,
                    ),
                    protected=False,
                )
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                return _harden_response(
                    JSONResponse(
                        {"detail": "Internal write access is not configured"},
                        status_code=503,
                    ),
                    protected=False,
                )
            response = await call_next(request)
            response.headers["X-Mango-Access-Mode"] = "public-read-only"
            return _harden_response(response, protected=False)
        if request.url.path in PUBLIC_PATHS:
            return _harden_response(await call_next(request), protected=False)

        if request.url.path == ACCESS_LINK_PATH:
            if request.method != "GET":
                return PlainTextResponse("Method not allowed", status_code=405)
            nonce = secrets.token_urlsafe(18)
            response = HTMLResponse(
                f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mango internal access</title></head>
<body><p id="status">Opening the internal cockpit…</p>
<script nonce="{nonce}">
(async () => {{
  const values = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, "", location.pathname);
  const response = await fetch("{ACCESS_SESSION_PATH}", {{
    method: "POST", credentials: "same-origin",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{
      expires: values.get("expires"),
      next: values.get("next") || "/",
      sig: values.get("sig") || ""
    }})
  }});
  if (!response.ok) {{ document.getElementById("status").textContent = "Access link is invalid or expired."; return; }}
  const result = await response.json();
  location.replace(result.next || "/");
}})().catch(() => {{ document.getElementById("status").textContent = "Could not establish internal access."; }});
</script></body></html>"""
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                f"script-src 'nonce-{nonce}'; "
                "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            )
            return _harden_response(response, protected=False)

        if request.url.path == ACCESS_SESSION_PATH:
            if request.method != "POST":
                return PlainTextResponse("Method not allowed", status_code=405)
            # The bootstrap page is same-origin. Reject an explicitly
            # cross-site browser request while still permitting test clients
            # that omit Fetch Metadata headers.
            if request.headers.get("sec-fetch-site", "same-origin") not in {
                "same-origin",
                "none",
            }:
                return PlainTextResponse("Cross-site session request rejected", status_code=403)
            try:
                payload = await request.json()
                expires = int(payload.get("expires", "0"))
            except (ValueError, TypeError, AttributeError):
                payload = {}
                expires = 0
            next_path = _safe_next(payload.get("next"))
            supplied = str(payload.get("sig", ""))
            expected = access_link_signature(token, expires, next_path)
            # Five seconds of clock skew avoids rejecting a link at the exact
            # boundary while keeping expired shared links short-lived.
            if expires < int(time.time()) - 5 or not hmac.compare_digest(supplied, expected):
                return PlainTextResponse("Access link is invalid or expired", status_code=401)

            response = JSONResponse({"ok": True, "next": next_path})
            response.set_cookie(
                ACCESS_COOKIE_NAME,
                _cookie_value(token),
                max_age=int(os.environ.get("INTERNAL_ACCESS_COOKIE_MAX_AGE", "43200")),
                httponly=True,
                secure=os.environ.get("INTERNAL_ACCESS_COOKIE_SECURE", "1") != "0",
                samesite="lax",
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return _harden_response(response)

        if _request_has_access(request, token):
            return _harden_response(await call_next(request))

        if request.method in {"GET", "HEAD", "OPTIONS"}:
            response = await call_next(request)
            response.headers["X-Mango-Access-Mode"] = "public-read-only"
            return _harden_response(response, protected=False)

        # No WWW-Authenticate header: browsers must not show a password UI.
        return _harden_response(JSONResponse(
            {"detail": "Write access requires internal authorization"},
            status_code=401,
        ), protected=False)
