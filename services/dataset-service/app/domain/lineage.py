"""Lineage graph traversal (DST-FR-042, BR-7).

Breadth-first over URN edges with a hard depth limit, a node cap, and a visited
set so cycles in stored data can never hang a query.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.entities import LineageEdge
from app.domain.ports import LineageRepo


@dataclass(slots=True)
class GraphResult:
    nodes: set[str] = field(default_factory=set)
    edges: list[LineageEdge] = field(default_factory=list)
    truncated: bool = False


def _neighbors(edge: LineageEdge, urns: set[str], direction: str) -> list[str]:
    out: list[str] = []
    if direction in ("downstream", "both") and edge.from_urn in urns:
        out.append(edge.to_urn)
    if direction in ("upstream", "both") and edge.to_urn in urns:
        out.append(edge.from_urn)
    return out


async def traverse(
    repo: LineageRepo,
    start_urns: set[str],
    *,
    direction: str,
    depth: int,
    activities: list[str] | None,
    node_cap: int,
) -> GraphResult:
    result = GraphResult(nodes=set(start_urns))
    seen_edges: set[str] = set()
    frontier = set(start_urns)

    for _ in range(depth):
        if not frontier:
            break
        edges = await repo.edges_touching(frontier, direction, activities)
        next_frontier: set[str] = set()
        for edge in edges:
            if edge.id in seen_edges:
                continue
            seen_edges.add(edge.id)
            result.edges.append(edge)
            for neighbor in _neighbors(edge, frontier, direction):
                if neighbor not in result.nodes:
                    if len(result.nodes) >= node_cap:
                        result.truncated = True
                        continue
                    result.nodes.add(neighbor)
                    next_frontier.add(neighbor)
        frontier = next_frontier

    # Flag truncation when unexplored edges remain past the requested depth.
    if frontier and not result.truncated:
        remaining = await repo.edges_touching(frontier, direction, activities)
        if any(e.id not in seen_edges for e in remaining):
            result.truncated = True
    return result


async def would_create_cycle(
    repo: LineageRepo, from_urn: str, to_urn: str, *, max_depth: int = 10, node_cap: int = 1000
) -> bool:
    """True if from_urn is reachable downstream from to_urn (bounded search)."""
    if from_urn == to_urn:
        return True
    reach = await traverse(
        repo,
        {to_urn},
        direction="downstream",
        depth=max_depth,
        activities=None,
        node_cap=node_cap,
    )
    return from_urn in reach.nodes
