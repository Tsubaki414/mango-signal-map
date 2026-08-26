from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


TERMINAL_CURSORS = {None, "", "0", 0, "-1", -1}


def _user_rest_id(value: dict) -> str | None:
    raw = value.get("rest_id") or value.get("id_str") or value.get("id")
    return str(raw) if raw is not None else None


def _is_user_object(value: dict) -> bool:
    """Accept actual GraphQL users and flat legacy users, but never Tweet wrappers.

    Search responses put ``Tweet`` objects (which also have ``rest_id`` and
    ``legacy`` fields) above the author ``User`` object.  Treating any object
    with a legacy payload as a user hides the nested author and can turn tweet
    IDs into apparent user IDs.  A usable identity candidate must expose a
    screen name itself.
    """
    rest_id = _user_rest_id(value)
    legacy = value.get("legacy") if isinstance(value.get("legacy"), dict) else {}
    core = value.get("core") if isinstance(value.get("core"), dict) else {}
    screen_name = value.get("screen_name") or legacy.get("screen_name") or core.get("screen_name")
    typename = str(value.get("__typename") or "")
    if typename and typename not in {"User", "UserWithVisibilityResults"}:
        return False
    return bool(rest_id and screen_name)


def _numbered_page(path: Path) -> int:
    match = re.search(r"-p(\d+)$", path.stem)
    return int(match.group(1)) if match else 1


def load_legacy_id_pages(cache_dir: Path, handle: str, kind: str = "following") -> tuple[set[str], bool, int]:
    """Load the original per-page Rapid X ID cache for one handle."""
    first = cache_dir / f"{kind}-ids-{handle}.json"
    pages = ([first] if first.exists() else []) + sorted(
        cache_dir.glob(f"{kind}-ids-{handle}-p*.json"), key=_numbered_page
    )
    ids: set[str] = set()
    complete = False
    for page in pages:
        payload = json.loads(page.read_text(encoding="utf-8"))
        ids.update(str(value) for value in payload.get("ids", []))
        cursor = payload.get("next_cursor_str", payload.get("next_cursor"))
        complete = cursor in TERMINAL_CURSORS
    return ids, complete, len(pages)


def load_best_id_cache(cache_dir: Path, handle: str, kind: str = "following") -> tuple[set[str], bool, int]:
    """Prefer the fullest of legacy or combined v2/v3 caches for one account."""
    candidates = [load_legacy_id_pages(cache_dir, handle, kind)]
    for version in ("v3", "v2"):
        path = cache_dir / version / f"{kind}-{handle}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidates.append((
            {str(value) for value in payload.get("ids", [])},
            bool(payload.get("complete")),
            int(payload.get("pages", 0)),
        ))
    return max(candidates, key=lambda item: (item[1], len(item[0]), item[2]))


def find_user_object(payload: object) -> dict | None:
    """Find the first Rapid X user object in the provider's nested response."""
    if isinstance(payload, dict):
        if _is_user_object(payload):
            return payload
        for value in payload.values():
            found = find_user_object(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_user_object(value)
            if found:
                return found
    return None


def find_user_objects(payload: object) -> list[dict]:
    """Find all unique Rapid X user objects in a nested response."""
    found: dict[str, dict] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if _is_user_object(value):
                found[_user_rest_id(value)] = value
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return list(found.values())


def enumerate_paths(adjacency: dict[str, set[str]], source: str, target: str, max_hops: int = 3) -> list[list[str]]:
    """Enumerate simple directed follow paths up to max_hops."""
    found: list[list[str]] = []

    def visit(node: str, path: list[str]) -> None:
        hops = len(path) - 1
        if node == target:
            found.append(path)
            return
        if hops >= max_hops:
            return
        for neighbor in sorted(adjacency.get(node, set())):
            if neighbor not in path:
                visit(neighbor, path + [neighbor])

    visit(source, [source])
    return sorted(found, key=lambda path: (len(path), path))


def relationship_adjacency(adjacency: dict[str, set[str]]) -> dict[str, set[str]]:
    """Create a weak/undirected view for degree calculation while retaining edge direction separately."""
    related: dict[str, set[str]] = {}
    for source, targets in adjacency.items():
        for target in targets:
            related.setdefault(source, set()).add(target)
            related.setdefault(target, set()).add(source)
    return related


def enumerate_routable_paths(
    adjacency: dict[str, set[str]], source: str, target: str, max_hops: int = 3
) -> list[list[str]]:
    """Enumerate degree paths while pruning shared-interest V shapes during traversal."""
    related = relationship_adjacency(adjacency)
    found: list[list[str]] = []

    def visit(node: str, path: list[str], previous_kind: str | None) -> None:
        hops = len(path) - 1
        if node == target:
            found.append(path)
            return
        if hops >= max_hops:
            return
        for neighbor in sorted(related.get(node, set())):
            if neighbor in path:
                continue
            kind = edge_kind(adjacency, node, neighbor)
            if previous_kind == "follows" and kind == "followed_by":
                continue
            visit(neighbor, path + [neighbor], kind)

    visit(source, [source], None)
    return sorted(found, key=lambda path: (len(path), path))


def edge_kind(adjacency: dict[str, set[str]], source: str, target: str) -> str:
    forward = target in adjacency.get(source, set())
    reverse = source in adjacency.get(target, set())
    if forward and reverse:
        return "mutual_follow"
    if forward:
        return "follows"
    if reverse:
        return "followed_by"
    return "no_observed_edge"


def path_strength(adjacency: dict[str, set[str]], path: Iterable[str]) -> float:
    """Conservative product score: mutual=0.65, one-way follow=0.25."""
    nodes = list(path)
    score = 1.0
    for source, target in zip(nodes, nodes[1:]):
        score *= {"mutual_follow": 0.65, "followed_by": 0.40, "follows": 0.25}.get(
            edge_kind(adjacency, source, target), 0.0
        )
    return round(score * 100, 1)


def is_routable_path(adjacency: dict[str, set[str]], path: Iterable[str]) -> bool:
    """Reject shared-interest V shapes where both sides merely follow the same middle account."""
    nodes = list(path)
    kinds = [edge_kind(adjacency, source, target) for source, target in zip(nodes, nodes[1:])]
    if "no_observed_edge" in kinds:
        return False
    return not any(left == "follows" and right == "followed_by" for left, right in zip(kinds, kinds[1:]))


def operational_x_score(adjacency: dict[str, set[str]], path: list[str] | None) -> int:
    """Map an observed X path to a reachability score without claiming intro consent."""
    if not path:
        return 0
    hops = len(path) - 1
    kinds = [edge_kind(adjacency, source, target) for source, target in zip(path, path[1:])]
    if hops == 1:
        return {"mutual_follow": 80, "followed_by": 70, "follows": 62}.get(kinds[0], 0)
    if hops == 2:
        if kinds == ["mutual_follow", "mutual_follow"]:
            return 66
        if kinds[0] == "mutual_follow" and kinds[1] == "followed_by":
            return 58
        return 52 if kinds[0] == "mutual_follow" else 46
    if hops == 3:
        return 38 if all(kind == "mutual_follow" for kind in kinds[:-1]) else 32
    return 0
