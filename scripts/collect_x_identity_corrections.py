#!/usr/bin/env python3
"""Resolve official corrected X handles through exact Rapid X profile lookups.

This collector never searches by company name and never falls back to a web or
social scraper.  It reads official-handle adjudications, calls Rapid X's
username profile endpoint, saves the raw response without credentials, and
confirms a result only when the returned screen name exactly matches the
official handle (case-insensitive).
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from mangobd.rapid_x import RapidXClient
from mangobd.x_network import find_user_objects


ROOT = Path(__file__).resolve().parents[1]
ADJUDICATIONS = ROOT / "data" / "pilot_v4" / "x_identity_adjudications.json"
CACHE = ROOT / "data" / "cache" / "rapidx" / "v4" / "identity-corrections"
OUTPUT = ROOT / "outputs" / "pilot_v4" / "x_identity_correction_resolutions.json"
CORRECTED_STATUS = "corrected_handle_requires_rapid_lookup"


def normalize_handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def normalize_user(user: dict[str, Any]) -> dict[str, Any]:
    legacy = user.get("legacy") if isinstance(user.get("legacy"), dict) else {}
    core = user.get("core") if isinstance(user.get("core"), dict) else {}
    rest_id = user.get("rest_id") or user.get("id_str") or user.get("id")
    return {
        "rest_id": str(rest_id) if rest_id is not None else "",
        "handle": str(
            user.get("screen_name") or legacy.get("screen_name") or core.get("screen_name") or ""
        ),
        "name": str(user.get("name") or legacy.get("name") or core.get("name") or ""),
        "description": str(user.get("description") or legacy.get("description") or ""),
        "followers_count": user.get("followers_count", legacy.get("followers_count")),
        "following_count": user.get(
            "following_count", user.get("friends_count", legacy.get("friends_count"))
        ),
        "verified": bool(user.get("verified") or user.get("is_blue_verified") or legacy.get("verified")),
    }


def response_users(payload: Any) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for user in find_user_objects(payload):
        normalized = normalize_user(user)
        if normalized["rest_id"]:
            by_id[normalized["rest_id"]] = normalized
    return sorted(by_id.values(), key=lambda row: (normalize_handle(row["handle"]), row["rest_id"]))


def resolve_profile_response(
    adjudication: dict[str, Any], payload: Any, cache_file: str, verified_at: str,
) -> dict[str, Any]:
    requested_handle = str(adjudication.get("proposed_handle") or "")
    requested_key = normalize_handle(requested_handle)
    candidates = response_users(payload)
    exact = [row for row in candidates if requested_key and normalize_handle(row["handle"]) == requested_key]
    common = {
        "company": adjudication.get("company", ""),
        "adjudication_status": adjudication.get("status", ""),
        "official_domain": adjudication.get("official_domain", ""),
        "official_social_url": adjudication.get("official_social_url", ""),
        "requested_handle": requested_handle,
        "lookup_kind": "rapid_x_exact_profile_lookup",
        "cache_file": cache_file,
        "candidate_count": len(candidates),
        "candidate_handles": [f"@{row['handle']}" for row in candidates if row["handle"]],
        "last_verified_at": verified_at,
        "evidence_scope": (
            "Rapid X profile response only; exact returned screen_name is required and does not "
            "establish relationship reachability or introduction willingness."
        ),
    }
    if len(exact) == 1:
        profile = exact[0]
        return {
            **common,
            "confirmed": True,
            "returned_handle": profile["handle"],
            "rest_id": profile["rest_id"],
            "profile": profile,
            "status": "confirmed_exact_official_handle_from_rapid_profile",
            "notes": "Returned Rapid X screen_name exactly matches the official proposed handle.",
        }
    if len(exact) > 1:
        status = "unresolved_multiple_exact_handle_profiles"
        notes = "Rapid X returned multiple User objects with the same exact screen_name."
    elif candidates:
        status = "unresolved_returned_handle_mismatch"
        notes = "Rapid X returned User objects, but none has the exact official screen_name."
    else:
        status = "unresolved_no_user_object"
        notes = "Rapid X returned no usable User object for the exact official handle lookup."
    return {
        **common,
        "confirmed": False,
        "returned_handle": "",
        "rest_id": "",
        "profile": None,
        "status": status,
        "notes": notes,
    }


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "company"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    adjudications = json.loads(ADJUDICATIONS.read_text(encoding="utf-8"))
    corrected = [row for row in adjudications if row.get("status") == CORRECTED_STATUS]
    if not corrected:
        raise RuntimeError("No corrected-handle adjudications found")

    CACHE.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    client = RapidXClient(cache_dir=CACHE / "requests", env_file=args.env_file)
    resolutions: list[dict[str, Any]] = []
    for index, adjudication in enumerate(corrected, 1):
        company = str(adjudication.get("company") or "")
        handle = str(adjudication.get("proposed_handle") or "")
        cache_path = CACHE / f"{index:02d}-{safe_slug(company)}.json"
        verified_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            payload = client.get_user(
                handle, force=args.force, cache_ttl_seconds=86_400 * 30,
            )
            raw_record = {
                "request": {
                    "company": company,
                    "handle": handle,
                    "official_social_url": adjudication.get("official_social_url", ""),
                    "source_adjudication_status": adjudication.get("status", ""),
                },
                "result_type": "exact_profile_lookup",
                "retrieved_at": verified_at,
                "response": payload,
            }
            cache_path.write_text(
                json.dumps(raw_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            resolutions.append(resolve_profile_response(
                adjudication, payload, str(cache_path.relative_to(ROOT)), verified_at,
            ))
        except Exception as exc:
            resolutions.append({
                "company": company,
                "adjudication_status": adjudication.get("status", ""),
                "official_domain": adjudication.get("official_domain", ""),
                "official_social_url": adjudication.get("official_social_url", ""),
                "requested_handle": handle,
                "lookup_kind": "rapid_x_exact_profile_lookup",
                "cache_file": str(cache_path.relative_to(ROOT)),
                "candidate_count": 0,
                "candidate_handles": [],
                "confirmed": False,
                "returned_handle": "",
                "rest_id": "",
                "profile": None,
                "status": "rapid_x_lookup_failed",
                "last_verified_at": verified_at,
                "evidence_scope": "Rapid X exact profile lookup failed; no alternate X source was used.",
                "notes": str(exc),
            })

    OUTPUT.write_text(
        json.dumps(resolutions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    counts = Counter(row["status"] for row in resolutions)
    print(json.dumps({
        "requested": len(corrected),
        "records": len(resolutions),
        "confirmed": sum(bool(row["confirmed"]) for row in resolutions),
        "status_counts": dict(sorted(counts.items())),
        "output": str(OUTPUT.relative_to(ROOT)),
        "cache_directory": str(CACHE.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
