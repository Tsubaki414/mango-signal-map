"""Monotonically fill missing operator X handles from the baked snapshot.

The production volume predates ``data/operator_x_handles.json`` for a small
set of already-existing operators.  Downstream DM/bridge snapshots resolve
operators by ``(company_id, x_handle)``, so those rows cannot be applied until
the stable handle is present.

This repair is deliberately narrow:

* map the snapshot's company display name to a stable company id;
* match exactly on a Unicode/case/whitespace-normalized person name;
* write only when the live handle is blank;
* preserve every non-blank live handle, reporting a conflict when different;
* never change evidence, source-match, human-verification, role, or budget
  fields.

It must run before ``apply_intro_bridges_snapshot.py`` in ``entrypoint.sh``.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import Operator  # noqa: E402


SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "operator_x_handles.json"

COMPANY_NAME_TO_ID = {
    "Perplexity": "company:perplexity",
    "Gumloop": "company:gumloop",
    "Krea": "company:krea",
    "Vercel (v0)": "company:v0vercel",
}


def normalize_person_name(value: str | None) -> str:
    """Normalize for exact matching without introducing fuzzy aliases."""

    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.split()).casefold()


def normalize_x_handle(value: str | None) -> str:
    """Normalize formatting only; retain snapshot casing when writing."""

    normalized = unicodedata.normalize("NFKC", value or "").strip()
    return normalized.removeprefix("@").casefold()


def apply_operator_x_handle_rows(session, rows: list[dict]) -> dict:
    """Fill missing handles and return an auditable result summary.

    ``conflicts`` and ``missing`` are skips, never implicit overwrites.  The
    caller owns the transaction so this function is straightforward to test
    and compose with the later DM snapshot.
    """

    result = {
        "updated": 0,
        "already_current": 0,
        "conflicts": [],
        "missing": [],
    }

    for row in rows:
        company_name = (row.get("company") or "").strip()
        company_id = COMPANY_NAME_TO_ID.get(company_name)
        person_name = row.get("name")
        snapshot_handle = (row.get("x_handle") or "").strip().removeprefix("@")
        normalized_name = normalize_person_name(person_name)
        normalized_snapshot_handle = normalize_x_handle(snapshot_handle)

        if not company_id or not normalized_name or not normalized_snapshot_handle:
            result["missing"].append(
                {
                    "company": company_name,
                    "name": person_name,
                    "reason": "invalid_or_unmapped_snapshot_row",
                }
            )
            continue

        matches = [
            operator
            for operator in session.query(Operator)
            .filter(Operator.company_id == company_id)
            .all()
            if normalize_person_name(operator.name) == normalized_name
        ]
        if len(matches) != 1:
            result["missing"].append(
                {
                    "company_id": company_id,
                    "name": person_name,
                    "reason": "operator_not_found" if not matches else "ambiguous_operator_name",
                    "match_count": len(matches),
                }
            )
            continue

        operator = matches[0]
        live_handle = normalize_x_handle(operator.x_handle)
        if not live_handle:
            operator.x_handle = snapshot_handle
            result["updated"] += 1
        elif live_handle == normalized_snapshot_handle:
            result["already_current"] += 1
        else:
            result["conflicts"].append(
                {
                    "company_id": company_id,
                    "name": operator.name,
                    "live_handle": operator.x_handle,
                    "snapshot_handle": snapshot_handle,
                    "reason": "non_blank_live_handle_preserved",
                }
            )

    return result


def main() -> None:
    if not SNAPSHOT_PATH.exists():
        print("no operator_x_handles.json, skipping")
        return

    init_db()
    rows = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    session = get_session()
    try:
        result = apply_operator_x_handle_rows(session, rows)
        session.commit()
    finally:
        session.close()

    print(
        "operator X handle snapshot: "
        f"updated={result['updated']}, "
        f"already_current={result['already_current']}, "
        f"conflicts={len(result['conflicts'])}, "
        f"missing={len(result['missing'])}"
    )
    for row in result["conflicts"]:
        print(
            "SKIP conflict: "
            f"{row['company_id']} / {row['name']!r}: "
            f"live=@{row['live_handle']} snapshot=@{row['snapshot_handle']}"
        )
    for row in result["missing"]:
        print(
            "SKIP missing: "
            f"{row.get('company_id') or row.get('company')!r} / "
            f"{row.get('name')!r}: {row['reason']}"
        )


if __name__ == "__main__":
    main()
