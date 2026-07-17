"""Resource defaults, floors, ceilings + predecessor inheritance (PIPE-FR-016, BR-1/2)."""

from __future__ import annotations

DEFAULTS = {"cpus": 1, "ram_gb": 2, "timeout_minutes": 30}
FLOOR = {"cpus": 1, "ram_gb": 2, "timeout_minutes": 5}
PLATFORM_CEILING = {"cpus": 7, "ram_gb": 24, "timeout_minutes": 480}


def topo_order(aliases: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Kahn topological order of node aliases given (from_alias, to_alias) edges."""
    from collections import defaultdict, deque

    indeg = dict.fromkeys(aliases, 0)
    adj: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        if a in indeg and b in indeg:
            adj[a].append(b)
            indeg[b] += 1
    queue = deque([a for a in aliases if indeg[a] == 0])
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for nxt in adj[node]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    # If there is a cycle some nodes won't appear; append them so callers still see all.
    order.extend(a for a in aliases if a not in order)
    return order


def resolve_resources(
    nodes: dict[str, dict], edges: list[tuple[str, str]], ceiling: dict
) -> dict[str, dict]:
    """Fill each node's effective resources: explicit values (clamped to floor/ceiling),
    else the element-wise max of its predecessors' effective resources (BR-2), else
    defaults. Returns alias -> {cpus, ram_gb, timeout_minutes}. (AC-11)."""
    from collections import defaultdict

    preds: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        preds[b].append(a)

    effective: dict[str, dict] = {}
    for alias in topo_order(list(nodes), edges):
        node = nodes[alias]
        explicit = node.get("resources") or {}
        if explicit:
            res = {k: explicit.get(k, DEFAULTS[k]) for k in DEFAULTS}
        elif preds[alias]:
            res = {}
            for k in DEFAULTS:
                res[k] = max(
                    (effective[p][k] for p in preds[alias] if p in effective),
                    default=DEFAULTS[k],
                )
        else:
            res = dict(DEFAULTS)
        # Clamp to floor then to the tenant ceiling (platform default fallback).
        eff_ceiling = {k: min(ceiling.get(k, PLATFORM_CEILING[k]), PLATFORM_CEILING[k])
                       for k in DEFAULTS}
        for k in DEFAULTS:
            res[k] = max(FLOOR[k], min(res[k], eff_ceiling[k]))
        effective[alias] = res
    return effective
