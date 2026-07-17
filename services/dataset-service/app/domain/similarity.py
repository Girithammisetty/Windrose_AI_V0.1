"""Dataset similarity ranking (DST-FR-061, V1 similar_with_schema/columns parity)."""

from __future__ import annotations

from app.domain.entities import Dataset


def rank_similar(
    candidates: list[tuple[Dataset, dict]],
    *,
    columns: list[str] | None = None,
    schema: dict | None = None,
) -> list[tuple[Dataset, float, list[str]]]:
    """Rank datasets by case-insensitive column overlap; schema match adds type bonus.

    candidates: (dataset, current schema dict). Returns (dataset, score, matched_columns)
    sorted by score desc then name, only entries with >= 1 match.
    """
    if schema:
        query = {c.lower(): (spec or {}).get("type") if isinstance(spec, dict) else spec
                 for c, spec in schema.items()}
    else:
        query = {c.lower(): None for c in (columns or [])}
    if not query:
        return []

    ranked: list[tuple[Dataset, float, list[str]]] = []
    for dataset, ds_schema in candidates:
        ds_cols = {c.lower(): (spec or {}).get("type") for c, spec in (ds_schema or {}).items()}
        matched = sorted(set(query) & set(ds_cols))
        if not matched:
            continue
        score = len(matched) / len(query)
        if schema:
            type_hits = sum(
                1 for c in matched if query[c] is not None and query[c] == ds_cols.get(c)
            )
            score += 0.5 * type_hits / len(query)
        ranked.append((dataset, round(score, 6), matched))

    ranked.sort(key=lambda r: (-r[1], r[0].name.lower()))
    return ranked
