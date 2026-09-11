"""Create/store/sync the internal-access token without printing its value.

By default this only creates (or reuses) ``INTERNAL_ACCESS_TOKEN`` in the
ignored repo-root ``.env`` with mode 0600. ``--sync-railway`` passes the value
to Railway over stdin with ``--skip-deploys``; it never places the secret in
the process arguments or triggers a deployment.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = APP_ROOT.parent / ".env"
TOKEN_KEY = "INTERNAL_ACCESS_TOKEN"


def _read_token(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith(f"{TOKEN_KEY}="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None


def ensure_local_token(env_file: Path, *, rotate: bool = False) -> tuple[str, bool]:
    env_file = Path(env_file)
    if env_file.is_symlink():
        raise ValueError("Refusing to write a symlinked .env file")
    existing_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    lines = existing_text.splitlines()
    existing = _read_token(lines)
    if existing and not rotate:
        os.chmod(env_file, 0o600)
        return existing, False

    token = secrets.token_urlsafe(48)
    replacement = f"{TOKEN_KEY}={token}"
    updated: list[str] = []
    inserted = False
    for line in lines:
        if line.startswith(f"{TOKEN_KEY}="):
            if not inserted:
                updated.append(replacement)
                inserted = True
            continue
        updated.append(line)
    if not inserted:
        if updated and updated[-1] != "":
            updated.append("")
        updated.append(replacement)

    env_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{env_file.name}.",
        dir=env_file.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(updated) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, env_file)
    finally:
        if temporary.exists():
            temporary.unlink()
    return token, True


def sync_railway_token(token: str, *, service: str, environment: str) -> None:
    railway = shutil.which("railway")
    if not railway:
        raise FileNotFoundError("Railway CLI is not installed or not on PATH")
    result = subprocess.run(
        [
            railway,
            "variable",
            "set",
            TOKEN_KEY,
            "--stdin",
            "--skip-deploys",
            "--service",
            service,
            "--environment",
            environment,
        ],
        input=token + "\n",
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Railway variable update failed; no secret output was displayed. "
            "Check `railway status` and authentication, then retry."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--rotate", action="store_true", help="Replace an existing local token.")
    parser.add_argument("--sync-railway", action="store_true")
    parser.add_argument("--service")
    parser.add_argument("--environment")
    args = parser.parse_args(argv)
    if args.sync_railway and (not args.service or not args.environment):
        parser.error("--sync-railway requires explicit --service and --environment")

    try:
        token, created = ensure_local_token(args.env_file, rotate=args.rotate)
        print(
            "Stored a new internal-access token in the ignored .env (value not printed)."
            if created
            else "Reused the existing internal-access token from .env (value not printed)."
        )
        if args.sync_railway:
            sync_railway_token(
                token,
                service=args.service,
                environment=args.environment,
            )
            print("Staged INTERNAL_ACCESS_TOKEN in Railway without deploying (value not printed).")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Internal-access bootstrap failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
