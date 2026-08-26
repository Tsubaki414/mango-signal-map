"""Tiny shared .env reader. Reads the repo-root .env (already gitignored,
0600) so kol_database reuses the same RAPID_X_API_KEY / OPENAI_API_KEY
credentials as the rest of the project instead of keeping a second copy.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"


def read_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_env(name: str, *fallback_names: str) -> str | None:
    import os

    for candidate in (name, *fallback_names):
        value = os.getenv(candidate)
        if value:
            return value
    file_env = read_env_file()
    for candidate in (name, *fallback_names):
        value = file_env.get(candidate)
        if value:
            return value
    return None
