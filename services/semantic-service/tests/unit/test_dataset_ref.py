"""Compiler._dataset_ref (QRY-FR-005 / BR-2 fix): FROM/JOIN clauses must emit a
resolvable ``{{dataset(...)}}`` macro, never a literal physical table — a bare
qualified identifier is rejected by query-service's tenant-namespace guard,
whose allowlist is built ONLY from macros it resolves itself (see
services/query-service/internal/sqlsafe/guard.go). This broke ALL Insights
dashboard data before the fix (every chart's compiled SQL was rejected
403 STATEMENT_NOT_ALLOWED)."""

from __future__ import annotations

from app.compiler.compiler import Compiler, normalize_request
from app.domain.definition import parse_definition
from tests.conftest import make_settings

_URN = "wr:t42:dataset:dataset/018f0000-0000-7000-8000-0000000000ff"

_LATEST_DEFINITION = {
    "entities": [
        {"name": "widgets", "dataset_urn": _URN, "table": "bronze.t42.ds_widgets_2026",
         "primary_key": ["id"], "dataset_version_policy": {"policy": "latest"}},
    ],
    "dimensions": [],
    "measures": [{"name": "widget_count", "entity": "widgets", "agg": "count"}],
    "join_paths": [],
}

_PINNED_DEFINITION = {
    "entities": [
        {"name": "widgets", "dataset_urn": _URN, "table": "bronze.t42.ds_widgets_2026",
         "primary_key": ["id"],
         "dataset_version_policy": {"policy": "pinned", "version_no": 7}},
    ],
    "dimensions": [],
    "measures": [{"name": "widget_count", "entity": "widgets", "agg": "count"}],
    "join_paths": [],
}


def _compile_from_clause(defn_dict: dict) -> str:
    settings = make_settings()
    defn = parse_definition(defn_dict, settings=settings)
    compiler = Compiler(defn, model_version_label="t@v1", settings=settings)
    req = normalize_request({"metrics": ["widget_count"]}, settings)
    return compiler.compile(req, "duckdb").sql


def test_from_clause_emits_dataset_macro_not_raw_physical_table():
    sql = _compile_from_clause(_LATEST_DEFINITION)
    assert "{{dataset('ds_widgets_2026')}}" in sql
    # never the bare qualified physical table — that's exactly what
    # query-service's namespace guard rejects.
    assert '"bronze"."t42"."ds_widgets_2026"' not in sql
    assert "ds_widgets_2026" not in sql.replace("{{dataset('ds_widgets_2026')}}", "")


def test_pinned_version_policy_threads_version_into_the_macro():
    sql = _compile_from_clause(_PINNED_DEFINITION)
    assert "{{dataset('ds_widgets_2026', version=7)}}" in sql
