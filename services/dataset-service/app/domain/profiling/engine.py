"""Profile document generation (BRD §4.4, schema_version 1).

This is the reference profiler implementation used by the in-process
ProfilerRunner fake; the containerized `windrose/profiler` image ships the same
logic. Deterministic thresholds are pinned to profiler_version.
"""

from __future__ import annotations

import html as html_mod
import json
import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from app.domain.entities import ProfileErrorCategory
from app.domain.profiling.types import InferredType, infer_logical_type, infer_semantic

SCHEMA_VERSION = 1
MAX_HISTOGRAM_BINS = 50
MAX_TOP_VALUES = 20
TOP_VALUE_TRUNCATE = 128
MAX_CORRELATION_PAIRS = 200
CORRELATION_MIN_ABS = 0.5
SUMMARY_MAX_BYTES = 64 * 1024


class ProfilerError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


def _py(value: Any) -> Any:
    """Numpy/pandas scalar -> JSON-safe python scalar."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else round(f, 6)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _check_columns(df: pd.DataFrame) -> None:
    for col in df.columns:
        name = str(col).strip()
        if not name or name.lower().startswith("unnamed:"):
            raise ProfilerError(
                ProfileErrorCategory.UNNAMED_COLUMNS, f"unnamed column at position {col!r}"
            )


def _numeric_stats(values: pd.Series) -> dict:
    if len(values) == 0:
        return {}
    q = values.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    stats = {
        "min": _py(values.min()),
        "max": _py(values.max()),
        "mean": _py(values.mean()),
        "stddev": _py(values.std()),
        "median": _py(q.loc[0.5]),
        "p5": _py(q.loc[0.05]),
        "p25": _py(q.loc[0.25]),
        "p75": _py(q.loc[0.75]),
        "p95": _py(q.loc[0.95]),
    }
    bins = min(MAX_HISTOGRAM_BINS, max(1, int(values.nunique())))
    counts, edges = np.histogram(values.to_numpy(dtype="float64"), bins=bins)
    stats["histogram"] = {
        "bins": [
            {"lo": _py(edges[i]), "hi": _py(edges[i + 1]), "count": int(counts[i])}
            for i in range(len(counts))
        ],
        "max_bins": MAX_HISTOGRAM_BINS,
    }
    return stats


def _temporal_stats(values: pd.Series, generated_at: datetime) -> tuple[dict, bool]:
    if len(values) == 0:
        return {}, False
    q = values.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    stats = {
        "min": _py(values.min()),
        "max": _py(values.max()),
        "median": _py(q.loc[0.5]),
        "p5": _py(q.loc[0.05]),
        "p25": _py(q.loc[0.25]),
        "p75": _py(q.loc[0.75]),
        "p95": _py(q.loc[0.95]),
    }
    gen = pd.Timestamp(generated_at)
    max_ts = values.max()
    if max_ts.tzinfo is None and gen.tzinfo is not None:
        gen = gen.tz_localize(None)
    elif max_ts.tzinfo is not None and gen.tzinfo is None:
        gen = gen.tz_localize("UTC")
    future = bool(max_ts > gen + timedelta(days=1))
    return stats, future


def _profile_column(
    name: str, series: pd.Series, inferred: InferredType, generated_at: datetime
) -> dict:
    total = len(series)
    non_null = series.dropna()
    null_count = int(total - len(non_null))
    null_pct = round(null_count / total * 100, 4) if total else 0.0
    distinct_count = int(non_null.nunique())
    distinct_pct = round(distinct_count / len(non_null) * 100, 4) if len(non_null) else 0.0
    is_unique = len(non_null) > 0 and distinct_count == len(non_null)

    col: dict[str, Any] = {
        "name": name,
        "logical_type": inferred.logical_type,
        "nullable": null_count > 0,
        "null_count": null_count,
        "null_pct": null_pct,
        "distinct_count": distinct_count,
        "distinct_pct": distinct_pct,
        "is_unique": is_unique,
        "tags": [],
        "quality_flags": [],
    }
    if inferred.coercion_hint:
        col["coercion_hint"] = inferred.coercion_hint

    flags: list[str] = []
    avg_length: float | None = None
    future_dates = False

    numeric_like = inferred.logical_type in ("int", "long", "float", "double") or (
        inferred.logical_type.startswith("decimal")
    )

    if inferred.logical_type == "boolean":
        truthy = {"y", "t", "true", "1", "yes"}
        if pd.api.types.is_bool_dtype(series) or all(isinstance(v, bool) for v in non_null):
            true_count = int(non_null.astype(bool).sum())
        else:
            true_count = int(non_null.astype(str).str.strip().str.lower().isin(truthy).sum())
        col["true_count"] = true_count
        col["false_count"] = int(len(non_null) - true_count)
    elif numeric_like:
        if inferred.coerced is not None:
            values = inferred.coerced.dropna().astype(float)
        else:
            values = pd.to_numeric(non_null, errors="coerce").dropna().astype(float)
        col.update(_numeric_stats(values))
        if len(values) >= 4:
            q1, q3 = values.quantile(0.25), values.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
                frac = float(((values < lo) | (values > hi)).mean())
                if frac > 0.005:
                    flags.append("OUTLIERS_IQR")
            skew = values.skew()
            if pd.notna(skew) and abs(float(skew)) > 3:
                flags.append("SKEWED")
    elif inferred.logical_type in ("date", "timestamp"):
        if inferred.coerced is not None:
            values = inferred.coerced.dropna()
        else:
            values = pd.to_datetime(non_null, errors="coerce").dropna()
        stats, future_dates = _temporal_stats(values, generated_at)
        col.update(stats)
    else:  # string / categorical
        text = non_null.astype(str)
        if len(text):
            lengths = text.str.len()
            avg_length = float(lengths.mean())
            col["min_length"] = int(lengths.min())
            col["max_length"] = int(lengths.max())
            col["avg_length"] = round(avg_length, 4)
        top = text.value_counts().head(MAX_TOP_VALUES)
        col["top_values"] = [
            {"value": str(v)[:TOP_VALUE_TRUNCATE], "count": int(c)} for v, c in top.items()
        ]

    semantic = infer_semantic(
        name,
        series,
        inferred.logical_type,
        is_unique=is_unique,
        avg_length=avg_length,
        distinct_pct=distinct_pct,
    )
    col["inferred_semantic"] = semantic

    # Quality flags (deterministic thresholds — BRD §4.4 table)
    if null_pct > 20:
        flags.append("HIGH_NULLS")
    if len(non_null) > 0 and distinct_count == 1:
        flags.append("CONSTANT")
    if distinct_pct > 95 and semantic != "id" and len(non_null) > 1:
        flags.append("MOSTLY_UNIQUE")
    if inferred.parse_fail_pct > 1:
        flags.append("MIXED_TYPES")
    if future_dates:
        flags.append("FUTURE_DATES")
    if semantic == "currency" and numeric_like:
        min_v = col.get("min")
        if min_v is not None and float(min_v) < 0:
            flags.append("NEGATIVE_IN_AMOUNT")

    col["quality_flags"] = sorted(set(flags))
    return col


def _correlations(df: pd.DataFrame, columns: list[dict]) -> dict:
    numeric_names = [
        c["name"]
        for c in columns
        if c["logical_type"] in ("int", "long", "float", "double")
        or c["logical_type"].startswith("decimal")
    ]
    numeric_df = pd.DataFrame(
        {n: pd.to_numeric(df[n], errors="coerce") for n in numeric_names if n in df.columns}
    )
    pairs: list[list] = []
    if numeric_df.shape[1] >= 2:
        corr = numeric_df.rank().corr()  # spearman via rank+pearson (no scipy dependency)
        names = list(corr.columns)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                r = corr.loc[a, b]
                if pd.notna(r) and abs(float(r)) >= CORRELATION_MIN_ABS:
                    pairs.append([a, b, round(float(r), 4)])
    pairs.sort(key=lambda p: -abs(p[2]))
    return {"method": "spearman", "pairs": pairs[:MAX_CORRELATION_PAIRS]}


def _alerts(columns: list[dict]) -> list[dict]:
    alerts = []
    for col in columns:
        for flag in col["quality_flags"]:
            severity = "warn" if flag in ("HIGH_NULLS", "MIXED_TYPES", "FUTURE_DATES") else "info"
            detail = flag.replace("_", " ").lower()
            if flag == "HIGH_NULLS":
                detail = f"{col['null_pct']}% null"
            alerts.append(
                {"column": col["name"], "flag": flag, "severity": severity, "detail": detail}
            )
    return alerts


def profile_dataframe(
    df: pd.DataFrame,
    *,
    dataset_urn: str,
    version_no: int,
    profiler_version: str,
    generated_at: datetime,
    sample_strategy: str = "full",
    max_rows: int = 10_000_000,
    sample_seed: int = 42,
    total_bytes: int | None = None,
) -> dict:
    """Produce the profile.json document (schema_version 1). Raises ProfilerError."""
    if len(df) == 0:
        raise ProfilerError(ProfileErrorCategory.EMPTY_DATA, "dataset has 0 rows")
    _check_columns(df)

    total_rows = len(df)
    if sample_strategy == "full" and total_rows > max_rows:
        sample_strategy = "reservoir"
    if sample_strategy == "reservoir" and total_rows > max_rows:
        try:
            fraction = max_rows / total_rows
            df = df.sample(n=max_rows, random_state=sample_seed)
        except Exception as exc:
            raise ProfilerError(ProfileErrorCategory.SAMPLING_FAILED, str(exc)) from exc
        sample = {"strategy": "reservoir", "fraction": round(fraction, 6), "seed": sample_seed}
    else:
        sample = {"strategy": "full", "fraction": 1.0, "seed": sample_seed}

    columns = []
    for name in df.columns:
        series = df[name]
        inferred = infer_logical_type(series)
        columns.append(_profile_column(str(name), series, inferred, generated_at))

    dup_pct = round(float(df.duplicated().mean()) * 100, 4) if len(df) else 0.0
    doc = {
        "schema_version": SCHEMA_VERSION,
        "dataset_urn": dataset_urn,
        "version_no": version_no,
        "generated_at": generated_at.isoformat(),
        "profiler_version": profiler_version,
        "sample": sample,
        "table": {
            "row_count": int(total_rows),
            "column_count": int(df.shape[1]),
            "bytes": total_bytes,
            "duplicate_row_pct": dup_pct,
        },
        "columns": columns,
        "correlations": _correlations(df, columns),
    }
    doc["alerts"] = _alerts(columns)
    return doc


def build_summary(doc: dict) -> dict:
    """Headline stats only — the ≤64KB pointer summary stored in Postgres (BR-4)."""
    summary = {
        "table": doc["table"],
        "columns": [
            {
                "name": c["name"],
                "logical_type": c["logical_type"],
                "null_pct": c["null_pct"],
                "distinct_count": c["distinct_count"],
                "quality_flags": c["quality_flags"],
            }
            for c in doc["columns"]
        ],
        "alerts": doc["alerts"],
    }
    encoded = json.dumps(summary).encode()
    if len(encoded) > SUMMARY_MAX_BYTES:
        # Trim alerts first, then columns, until under the cap (no-blob rule).
        summary["alerts"] = summary["alerts"][:50]
        while len(json.dumps(summary).encode()) > SUMMARY_MAX_BYTES and summary["columns"]:
            summary["columns"] = summary["columns"][: max(1, len(summary["columns"]) // 2)]
            summary["columns_truncated"] = True
    return summary


def render_html_report(doc: dict) -> str:
    """Static HTML rendering of the profile document (no pandas-profiling dependency)."""
    esc = html_mod.escape
    rows = "".join(
        f"<tr><td>{esc(c['name'])}</td><td>{esc(c['logical_type'])}</td>"
        f"<td>{esc(str(c.get('inferred_semantic')))}</td><td>{c['null_pct']}%</td>"
        f"<td>{c['distinct_count']}</td>"
        f"<td>{esc(', '.join(c['quality_flags']) or '—')}</td></tr>"
        for c in doc["columns"]
    )
    alerts = "".join(
        f"<li><b>{esc(a['flag'])}</b> [{esc(a['severity'])}] "
        f"{esc(str(a.get('column')))}: {esc(a['detail'])}</li>"
        for a in doc["alerts"]
    )
    t = doc["table"]
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Profile — {esc(doc['dataset_urn'])} v{doc['version_no']}</title>"
        "<style>body{font-family:sans-serif;margin:2rem}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px}</style></head><body>"
        f"<h1>Dataset profile</h1><p>{esc(doc['dataset_urn'])} · v{doc['version_no']} · "
        f"generated {esc(doc['generated_at'])} · {esc(doc['profiler_version'])}</p>"
        f"<p>Rows: {t['row_count']} · Columns: {t['column_count']} · "
        f"Duplicate rows: {t['duplicate_row_pct']}%</p>"
        f"<h2>Columns</h2><table><tr><th>name</th><th>type</th><th>semantic</th>"
        f"<th>null %</th><th>distinct</th><th>flags</th></tr>{rows}</table>"
        f"<h2>Alerts</h2><ul>{alerts or '<li>none</li>'}</ul>"
        "</body></html>"
    )
