"""Per-corpus event mappers (MEM-FR-031, standard corpora table §3).

Each mapper turns a consumed domain event payload into a ``MappedSource``:
the source URN plus the raw text to chunk/embed, and an optional user linkage
used by right-to-erasure (MEM-FR-040 step 3).
"""

from __future__ import annotations

from dataclasses import dataclass

CORPUS_SCHEMAS = "schemas"
CORPUS_DASHBOARDS = "dashboards"
CORPUS_RESOLVED_CASES = "resolved_cases"
CORPUS_DOCS = "docs"


@dataclass
class MappedSource:
    corpus_key: str
    source_urn: str
    text: str
    user_linkage: str | None = None
    tombstone: bool = False  # source deleted -> remove chunks


def map_dataset_profiled(env: dict) -> MappedSource | None:
    p = env.get("payload", {})
    urn = env.get("resource_urn") or p.get("dataset_urn")
    if not urn:
        return None
    cols = p.get("columns", [])
    col_lines = ", ".join(
        f"{c.get('name')}:{c.get('type')}" for c in cols if isinstance(c, dict)
    )
    prof = p.get("profile", {})
    highlights = (
        f"rows={prof.get('row_count')} distincts={prof.get('distinct_count')}"
        if prof else ""
    )
    text = " ".join(
        x for x in [
            p.get("name", ""), p.get("description", ""),
            f"columns: {col_lines}" if col_lines else "", highlights,
        ] if x
    ).strip()
    return MappedSource(CORPUS_SCHEMAS, urn, text)


def map_dashboard_updated(env: dict) -> MappedSource | None:
    p = env.get("payload", {})
    urn = env.get("resource_urn") or p.get("dashboard_urn")
    if not urn:
        return None
    charts = p.get("charts", [])
    chart_titles = ", ".join(c.get("title", "") for c in charts if isinstance(c, dict))
    measures = ", ".join(p.get("measures", []))
    text = " ".join(
        x for x in [
            p.get("title", ""), p.get("description", ""),
            f"charts: {chart_titles}" if chart_titles else "",
            f"measures: {measures}" if measures else "",
        ] if x
    ).strip()
    return MappedSource(CORPUS_DASHBOARDS, urn, text)


def map_case_resolved(env: dict) -> MappedSource | None:
    p = env.get("payload", {})
    urn = env.get("resource_urn") or p.get("case_urn")
    if not urn:
        return None
    # case-service's live case.resolved payload carries case_number,
    # disposition_code, disposition_category, resolution_note and authored_by
    # (see case-service handlers_transitions.go resolveMutation); the remaining
    # narrative fields (resolution_narrative, evidence_summary, case_type) are
    # optional enrichments no producer sends yet.
    disposition = p.get("disposition") or p.get("disposition_code")
    text = " ".join(
        x for x in [
            p.get("resolution_narrative", ""),
            p.get("resolution_note", ""),
            f"disposition={disposition}" if disposition else "",
            f"category={p.get('disposition_category','')}"
            if p.get("disposition_category") else "",
            p.get("evidence_summary", ""),
            f"case_type={p.get('case_type','')}" if p.get("case_type") else "",
            f"case_number={p.get('case_number')}"
            if p.get("case_number") is not None else "",
        ] if x
    ).strip()
    if not text:
        return None  # nothing to embed — skip rather than write an empty chunk
    return MappedSource(
        CORPUS_RESOLVED_CASES, urn, text,
        user_linkage=p.get("authored_by") or p.get("comment_author"),
    )


_MAPPERS = {
    "dataset.profiled": map_dataset_profiled,
    "dashboard.updated": map_dashboard_updated,
    "case.resolved": map_case_resolved,
}


def map_event(env: dict) -> MappedSource | None:
    fn = _MAPPERS.get(env.get("event_type", ""))
    return fn(env) if fn else None
