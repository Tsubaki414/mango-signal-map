"""Generate a short-lived internal browser link without exposing the token."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.access import build_access_link  # noqa: E402
from scripts.bootstrap_internal_access import DEFAULT_ENV_FILE, TOKEN_KEY  # noqa: E402


def _token_from_env_file(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{TOKEN_KEY}="):
            return line.split("=", 1)[1].strip() or None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Deployed HTTPS origin.")
    parser.add_argument("--next", default="/", dest="next_path", help="Same-origin path after access is granted.")
    parser.add_argument("--ttl-minutes", type=int, default=15)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args(argv)

    token = os.environ.get("INTERNAL_ACCESS_TOKEN", "").strip() or _token_from_env_file(args.env_file)
    if not token:
        print("INTERNAL_ACCESS_TOKEN is not configured", file=sys.stderr)
        return 1
    if not args.base_url.lower().startswith("https://"):
        print("Refusing to generate a production access link for a non-HTTPS URL", file=sys.stderr)
        return 1
    if not 1 <= args.ttl_minutes <= 1440:
        print("--ttl-minutes must be between 1 and 1440", file=sys.stderr)
        return 1

    expires = int(time.time()) + args.ttl_minutes * 60
    print(
        build_access_link(
            args.base_url,
            token,
            expires=expires,
            next_path=args.next_path,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
