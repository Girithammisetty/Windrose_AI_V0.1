"""First-party entity-resolution engine (BRD 56 inc1) — the "build" core.

Given a record set (a dataset's rows) and a per-entity-type config, produce
resolved entity CLUSTERS: which records are the same real-world entity, at what
confidence, on what evidence. Two-stage matching, exactly as the BRD specifies:

  1. Deterministic keys — records sharing an exact composite key (e.g.
     national_id, or name+dob) are the same entity. High confidence, no scoring.
  2. Probabilistic scoring — within a blocking key, remaining records are scored
     on weighted attribute similarity. A score >= auto_merge_threshold merges
     automatically; between review_threshold and auto it becomes a HUMAN-review
     merge candidate (four-eyes, ER-FR-030), never silently merged; below review
     the records stay separate entities.

Pure and deterministic (no I/O, no randomness) so it is exhaustively unit-
testable and its clusters are reproducible for audit (ER-FR-040). It produces a
LINK layer only — it never mutates source records (ER-FR-050).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


@dataclass(slots=True)
class ScoringField:
    column: str
    weight: float = 1.0


@dataclass(slots=True)
class ResolutionConfig:
    entity_type: str
    # Each inner list is a COMPOSITE exact-match key (all columns must be present
    # and equal). Any one satisfied key merges the records deterministically.
    deterministic_keys: list[list[str]] = field(default_factory=list)
    scoring_fields: list[ScoringField] = field(default_factory=list)
    auto_merge_threshold: float = 0.85
    review_threshold: float = 0.60
    # Records must agree on ALL of these (when both present) to even be scored —
    # a cheap block that stops, e.g., merging two different DOBs on name alone.
    blocking_fields: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Cluster:
    resolved_entity_id: str
    member_pks: list[str]
    confidence: float
    method: str            # "deterministic" | "probabilistic" | "singleton"
    evidence: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class MergeCandidate:
    # A proposed (below auto, above review) merge for a steward's four-eyes review.
    left_pk: str
    right_pk: str
    score: float
    evidence: dict


@dataclass(slots=True)
class ResolutionResult:
    clusters: list[Cluster]
    merge_candidates: list[MergeCandidate]

    @property
    def resolved_count(self) -> int:
        return len(self.clusters)


# ---- normalization + similarity (dependency-free) --------------------------

def _norm(v) -> str:
    if v is None:
        return ""
    s = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _bigrams(s: str) -> set[str]:
    s = s.replace(" ", "")
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def string_similarity(a: str, b: str) -> float:
    """Dice coefficient over character bigrams — 1.0 identical, 0.0 disjoint.
    Robust to minor spelling/format variation ("Viktor" vs "Victor") without a
    heavy dependency; exact-equal short tokens still score 1.0."""
    a, b = _norm(a), _norm(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    return (2 * inter) / (len(ga) + len(gb))


def _det_key_values(row: dict, key: list[str]) -> tuple | None:
    """The composite key's normalized value tuple, or None if any part is blank
    (a partial key must NEVER merge — missing id != missing id)."""
    vals = []
    for col in key:
        v = _norm(row.get(col))
        if not v:
            return None
        vals.append(v)
    return tuple(vals)


def _pair_score(a: dict, b: dict, cfg: ResolutionConfig) -> tuple[float, dict]:
    # Blocking gate: if any blocking field is present on BOTH and disagrees, the
    # pair is not a match regardless of name similarity.
    for bf in cfg.blocking_fields:
        va, vb = _norm(a.get(bf)), _norm(b.get(bf))
        if va and vb and va != vb:
            return 0.0, {"blocked_on": bf}
    total_w = sum(max(0.0, f.weight) for f in cfg.scoring_fields) or 1.0
    acc = 0.0
    per_field = {}
    for f in cfg.scoring_fields:
        s = string_similarity(a.get(f.column), b.get(f.column))
        per_field[f.column] = round(s, 4)
        acc += s * max(0.0, f.weight)
    return acc / total_w, {"fields": per_field}


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self._p = {x: x for x in items}

    def find(self, x: str) -> str:
        while self._p[x] != x:
            self._p[x] = self._p[self._p[x]]
            x = self._p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._p[ra] = rb


def resolve(rows: list[dict], config: ResolutionConfig, *, pk_column: str,
            id_prefix: str = "ent") -> ResolutionResult:
    """Resolve a record set into entity clusters + human-review merge candidates.

    ``rows`` are dicts (a dataset's materialized rows); ``pk_column`` is the stable
    record id. Deterministic across runs: cluster ids derive from the smallest
    member pk, so the same input yields the same resolved_entity_ids (audit)."""
    pks = [str(r.get(pk_column)) for r in rows if r.get(pk_column) is not None]
    by_pk = {str(r.get(pk_column)): r for r in rows if r.get(pk_column) is not None}
    uf = _UnionFind(pks)
    method: dict[str, str] = {p: "singleton" for p in pks}
    evidence: dict[str, list[dict]] = {p: [] for p in pks}
    candidates: list[MergeCandidate] = []

    # Stage 1 — deterministic keys: records sharing an exact composite key merge.
    for key in config.deterministic_keys:
        buckets: dict[tuple, list[str]] = {}
        for p in pks:
            kv = _det_key_values(by_pk[p], key)
            if kv is not None:
                buckets.setdefault(kv, []).append(p)
        for kv, members in buckets.items():
            if len(members) > 1:
                anchor = members[0]
                for m in members[1:]:
                    uf.union(anchor, m)
                    method[m] = method[anchor] = "deterministic"
                    evidence[m].append({"key": key, "values": list(kv)})

    # Stage 2 — probabilistic scoring on the residual (block by blocking_fields to
    # keep it O(block^2), not O(n^2)). Only score pairs not already merged.
    if config.scoring_fields:
        blocks: dict[tuple, list[str]] = {}
        for p in pks:
            bkey = tuple(_norm(by_pk[p].get(bf)) for bf in config.blocking_fields)
            blocks.setdefault(bkey, []).append(p)
        for members in blocks.values():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    if uf.find(a) == uf.find(b):
                        continue  # already same entity (deterministic)
                    score, ev = _pair_score(by_pk[a], by_pk[b], config)
                    if score >= config.auto_merge_threshold:
                        uf.union(a, b)
                        if method[a] == "singleton":
                            method[a] = "probabilistic"
                        if method[b] == "singleton":
                            method[b] = "probabilistic"
                        evidence[b].append({"score": round(score, 4), **ev})
                    elif score >= config.review_threshold:
                        candidates.append(MergeCandidate(
                            left_pk=a, right_pk=b, score=round(score, 4), evidence=ev))

    # Materialize clusters (stable id = smallest member pk).
    groups: dict[str, list[str]] = {}
    for p in pks:
        groups.setdefault(uf.find(p), []).append(p)
    clusters: list[Cluster] = []
    for members in groups.values():
        members_sorted = sorted(members)
        cid = f"{id_prefix}:{config.entity_type}:{members_sorted[0]}"
        cmethod = "singleton"
        if len(members_sorted) > 1:
            cmethod = "deterministic" if any(
                method[m] == "deterministic" for m in members_sorted) else "probabilistic"
        ev = [e for m in members_sorted for e in evidence[m]]
        confidence = 1.0 if cmethod in ("deterministic", "singleton") else round(
            min(1.0, max((e.get("score", 0.0) for e in ev), default=0.0)), 4)
        clusters.append(Cluster(resolved_entity_id=cid, member_pks=members_sorted,
                                confidence=confidence, method=cmethod, evidence=ev))
    clusters.sort(key=lambda c: c.resolved_entity_id)
    return ResolutionResult(clusters=clusters, merge_candidates=candidates)
