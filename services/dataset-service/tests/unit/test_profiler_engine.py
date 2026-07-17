"""Unit: profile generation on synthetic dataframes (DST-FR-021/022/025, §4.4).

Covers V1-parity type inference edge cases, semantics, quality flags, failure
taxonomy, and the 64KB summary cap.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.domain.profiling.engine import (
    SUMMARY_MAX_BYTES,
    ProfilerError,
    build_summary,
    profile_dataframe,
    render_html_report,
)
from app.domain.profiling.types import infer_logical_type

GEN_AT = datetime(2026, 7, 9, tzinfo=UTC)


def profile(df: pd.DataFrame, **kwargs) -> dict:
    return profile_dataframe(
        df,
        dataset_urn="wr:t1:dataset:dataset/d1",
        version_no=1,
        profiler_version="test/1",
        generated_at=GEN_AT,
        **kwargs,
    )


def col(doc: dict, name: str) -> dict:
    return next(c for c in doc["columns"] if c["name"] == name)


class TestTypeInference:
    def test_native_dtypes(self):
        df = pd.DataFrame(
            {
                "flag": [True, False, True],
                "small": pd.Series([1, 2, 3], dtype="int64"),
                "big": pd.Series([2**40, 2**41, 2**42], dtype="int64"),
                "ratio": [0.5, 1.5, 2.5],
                "ts": pd.to_datetime(["2026-01-01 10:00", "2026-01-02 11:30",
                                      "2026-01-03 09:15"]),
                "day": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            }
        )
        doc = profile(df)
        assert col(doc, "flag")["logical_type"] == "boolean"
        assert col(doc, "small")["logical_type"] == "int"
        assert col(doc, "big")["logical_type"] == "long"
        assert col(doc, "ratio")["logical_type"] == "double"
        assert col(doc, "ts")["logical_type"] == "timestamp"
        assert col(doc, "day")["logical_type"] == "date"

    @pytest.mark.parametrize(
        ("values", "hint"),
        [
            (["Y", "N", "y", "N"], "Y/N"),
            (["T", "F", "t", "f"], "T/F"),
            (["true", "FALSE", "True", "false"], "true/false"),
        ],
    )
    def test_boolean_like_strings_with_coercion_hint(self, values, hint):
        """DST-FR-025: boolean-like strings report boolean + coercion_hint."""
        doc = profile(pd.DataFrame({"active": values}))
        c = col(doc, "active")
        assert c["logical_type"] == "boolean"
        assert c["coercion_hint"] == hint
        assert c["true_count"] + c["false_count"] == len(values)

    def test_numeric_strings(self):
        doc = profile(pd.DataFrame({"n": ["1", "2", "3"], "d": ["1.5", "2.5", "0.5"]}))
        assert col(doc, "n")["logical_type"] == "int"
        assert col(doc, "d")["logical_type"] == "double"

    def test_decimal_objects(self):
        doc = profile(pd.DataFrame({"amt": [Decimal("12.30"), Decimal("100.05")]}))
        assert col(doc, "amt")["logical_type"] == "decimal(5,2)"

    def test_datetime_strings(self):
        doc = profile(
            pd.DataFrame({"when": ["2026-01-01T10:00:00", "2026-02-01T11:00:00",
                                   "2026-03-01T12:00:00"]})
        )
        assert col(doc, "when")["logical_type"] == "timestamp"

    def test_categorical_vs_string(self):
        n = 1000
        doc = profile(
            pd.DataFrame(
                {
                    "cat": (["red", "green", "blue"] * (n // 3) + ["red"])[:n],
                    "free": [f"value-{i}" for i in range(n)],
                }
            )
        )
        assert col(doc, "cat")["logical_type"] == "categorical"
        assert col(doc, "free")["logical_type"] == "string"

    def test_mixed_types_flagged(self):
        """>1% parse failures against the inferred type -> MIXED_TYPES."""
        values = [str(i) for i in range(97)] + ["oops", "bad", "nan?"]
        doc = profile(pd.DataFrame({"mostly_num": values}))
        c = col(doc, "mostly_num")
        assert c["logical_type"] in ("int", "long")
        assert "MIXED_TYPES" in c["quality_flags"]


class TestSemantics:
    def test_semantics(self):
        n = 20
        df = pd.DataFrame(
            {
                "user_id": range(n),
                "email": [f"user{i}@example.com" for i in range(n)],
                "homepage": [f"https://example.com/{i}" for i in range(n)],
                "phone_number": [f"+1 (555) 010-{1000 + i}" for i in range(n)],
                "country": ["US", "GB", "DE", "FR"] * (n // 4),
                "order_total": [10.5 + i for i in range(n)],
                "notes": [
                    f"Long free text description number {i} with plenty of words "
                    f"to push average length up beyond the threshold {i}"
                    for i in range(n)
                ],
            }
        )
        doc = profile(df)
        assert col(doc, "user_id")["inferred_semantic"] == "id"
        assert col(doc, "email")["inferred_semantic"] == "email"
        assert col(doc, "homepage")["inferred_semantic"] == "url"
        assert col(doc, "phone_number")["inferred_semantic"] == "phone"
        assert col(doc, "country")["inferred_semantic"] == "country"
        assert col(doc, "order_total")["inferred_semantic"] == "currency"
        assert col(doc, "notes")["inferred_semantic"] == "free_text"


class TestQualityFlags:
    def test_high_nulls_and_constant_and_mostly_unique(self):
        n = 100
        df = pd.DataFrame(
            {
                "sparse": [None] * 30 + list(range(70)),
                "fixed": ["same"] * n,
                "nearly_unique": [f"tok-{i}" for i in range(n)],
            }
        )
        doc = profile(df)
        assert "HIGH_NULLS" in col(doc, "sparse")["quality_flags"]
        assert col(doc, "sparse")["null_pct"] == 30.0
        assert "CONSTANT" in col(doc, "fixed")["quality_flags"]
        assert "MOSTLY_UNIQUE" in col(doc, "nearly_unique")["quality_flags"]

    def test_id_column_not_mostly_unique(self):
        doc = profile(pd.DataFrame({"customer_id": range(100)}))
        assert "MOSTLY_UNIQUE" not in col(doc, "customer_id")["quality_flags"]

    def test_outliers_and_skew(self):
        rng = np.random.default_rng(7)
        base = rng.normal(100, 5, 990).tolist()
        outliers = [10_000.0] * 10  # 1% > 0.5% threshold
        skewed = np.concatenate([rng.uniform(0, 1, 990), [10_000] * 10])
        doc = profile(pd.DataFrame({"with_outliers": base + outliers, "skewed": skewed}))
        assert "OUTLIERS_IQR" in col(doc, "with_outliers")["quality_flags"]
        assert "SKEWED" in col(doc, "skewed")["quality_flags"]

    def test_future_dates(self):
        doc = profile(
            pd.DataFrame({"seen_at": pd.to_datetime(["2026-01-01", "2027-06-01"])})
        )
        assert "FUTURE_DATES" in col(doc, "seen_at")["quality_flags"]

    def test_negative_in_amount(self):
        doc = profile(pd.DataFrame({"order_total": [10.0, -3.5, 22.0]}))
        c = col(doc, "order_total")
        assert c["inferred_semantic"] == "currency"
        assert "NEGATIVE_IN_AMOUNT" in c["quality_flags"]

    def test_alerts_generated_with_severity(self):
        doc = profile(pd.DataFrame({"sparse": [None] * 60 + list(range(40))}))
        alert = next(a for a in doc["alerts"] if a["flag"] == "HIGH_NULLS")
        assert alert["severity"] == "warn"
        assert alert["column"] == "sparse"
        assert "60.0% null" in alert["detail"]


class TestFailureTaxonomy:
    def test_empty_data(self):
        """AC-4 (engine tier): 0 rows -> EMPTY_DATA."""
        with pytest.raises(ProfilerError) as err:
            profile(pd.DataFrame({"a": pd.Series([], dtype="float64")}))
        assert err.value.category == "EMPTY_DATA"

    def test_unnamed_columns(self):
        with pytest.raises(ProfilerError) as err:
            profile(pd.DataFrame({"Unnamed: 0": [1, 2], "ok": [3, 4]}))
        assert err.value.category == "UNNAMED_COLUMNS"


class TestDocumentShape:
    def test_stats_histogram_topvalues(self):
        n = 500
        df = pd.DataFrame(
            {
                "value": np.arange(n, dtype="float64"),
                "word": (["alpha", "beta"] * (n // 2)),
                "long_text": ["x" * 300] * n,
            }
        )
        doc = profile(df)
        v = col(doc, "value")
        for stat in ("min", "max", "mean", "stddev", "median", "p5", "p25", "p75", "p95"):
            assert v[stat] is not None
        assert len(v["histogram"]["bins"]) <= 50
        assert sum(b["count"] for b in v["histogram"]["bins"]) == n
        w = col(doc, "word")
        assert len(w["top_values"]) <= 20
        assert all(len(t["value"]) <= 128 for t in col(doc, "long_text")["top_values"])
        assert doc["table"]["row_count"] == n
        assert doc["table"]["column_count"] == 3
        assert doc["sample"] == {"strategy": "full", "fraction": 1.0, "seed": 42}
        assert json.dumps(doc)  # JSON-serializable (no numpy scalars)

    def test_duplicate_row_pct(self):
        doc = profile(pd.DataFrame({"a": [1, 1, 2, 3], "b": ["x", "x", "y", "z"]}))
        assert doc["table"]["duplicate_row_pct"] == 25.0

    def test_correlations_spearman(self):
        n = 200
        x = np.arange(n, dtype="float64")
        rng = np.random.default_rng(3)
        doc = profile(
            pd.DataFrame({"x": x, "y": x * 2 + 1, "noise": rng.normal(size=n)})
        )
        assert doc["correlations"]["method"] == "spearman"
        pair = next(p for p in doc["correlations"]["pairs"] if {p[0], p[1]} == {"x", "y"})
        assert pair[2] == pytest.approx(1.0)

    def test_sampling_deterministic(self):
        df = pd.DataFrame({"v": np.arange(1000, dtype="float64")})
        d1 = profile(df, max_rows=100)
        d2 = profile(df, max_rows=100)
        assert d1["sample"]["strategy"] == "reservoir"
        assert d1["sample"]["fraction"] == 0.1
        assert d1["columns"] == d2["columns"]  # same seed -> same sample

    def test_summary_capped_at_64kb(self):
        """BR-4 / MASTER-FR-061: pointer summary stays under 64KB."""
        cols = {f"column_with_a_rather_long_name_{i:04d}": ["v"] * 2 for i in range(3000)}
        doc = profile(pd.DataFrame(cols))
        summary = build_summary(doc)
        assert len(json.dumps(summary).encode()) <= SUMMARY_MAX_BYTES

    def test_html_report_renders(self):
        doc = profile(pd.DataFrame({"a": [1, 2, 3]}))
        html = render_html_report(doc)
        assert "<html" in html and "wr:t1:dataset:dataset/d1" in html


def test_infer_handles_all_null_column():
    inferred = infer_logical_type(pd.Series([None, None], dtype="object"))
    assert inferred.logical_type == "string"
