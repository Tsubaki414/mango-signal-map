from __future__ import annotations

import heapq
from collections import defaultdict

from .models import Relationship


def strongest_path(
    relationships: list[Relationship], source_id: str, target_id: str, max_hops: int = 4
) -> tuple[float, list[str]] | None:
    """Return maximum-product trust path, using relationship strength as probability."""
    adjacent: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for edge in relationships:
        weight = max(1, min(edge.strength, 100)) / 100
        adjacent[edge.source_id].append((edge.target_id, weight))
        if not edge.directional:
            adjacent[edge.target_id].append((edge.source_id, weight))

    queue: list[tuple[float, int, str, list[str]]] = [(-1.0, 0, source_id, [source_id])]
    best: dict[tuple[str, int], float] = {(source_id, 0): 1.0}
    while queue:
        negative_score, hops, node, path = heapq.heappop(queue)
        score = -negative_score
        if node == target_id:
            return round(score * 100, 1), path
        if hops >= max_hops:
            continue
        for neighbor, edge_score in adjacent.get(node, []):
            next_score = score * edge_score
            key = (neighbor, hops + 1)
            if next_score > best.get(key, 0):
                best[key] = next_score
                heapq.heappush(queue, (-next_score, hops + 1, neighbor, path + [neighbor]))
    return None

