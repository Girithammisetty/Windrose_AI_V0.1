"""Unit-tier doubles ONLY (never wired from app.main / build_container(real=True)).

These satisfy the same ports as the real adapters so the unit tier can exercise
the proposal/HITL/registry logic without ai-gateway / tool-plane / Kafka.
"""

from __future__ import annotations

from app.domain.ports import LlmResult, ToolResult


class FakeLlm:
    """Deterministic-ish LLM double that returns a valid triage JSON."""

    def __init__(self, content: str | None = None) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def chat(self, *, messages, tenant_id, response_format=None, temperature=None,
                   max_tokens=None) -> LlmResult:
        self.calls.append({"messages": messages, "tenant_id": tenant_id})
        content = self._content or (
            '{"severity":"high","disposition_code":"duplicate_invoice",'
            '"assignee_hint":"u-dana","rationale":"Vendor pattern matches 14 resolved '
            'duplicate-invoice cases; amount variance exceeds threshold."}')
        return LlmResult(content=content, input_tokens=42, output_tokens=21,
                         model="fake-fast-small", deployment="fake")


class FakeToolClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.require_proposal = True

    async def call(self, *, tool_id, arguments, tenant_id, auth_token, version=None,
                   proposal_grant=None) -> ToolResult:
        self.calls.append({"tool_id": tool_id, "arguments": arguments,
                           "grant": proposal_grant, "auth": auth_token})
        if self.require_proposal and not proposal_grant:
            return ToolResult(ok=False, status="proposal_required",
                              tier="write-proposal", side_effects="reversible",
                              code="PROPOSAL_REQUIRED")
        return ToolResult(ok=True, status="ok", output={"applied": True, "tool_id": tool_id})


class FakeMemory:
    def __init__(self, results: list[dict] | None = None) -> None:
        self._results = results or []
        self.calls: list[dict] = []

    async def retrieve(self, *, tenant_id, query, auth_token, top_k=5,
                       snapshot_ver=None) -> list[dict]:
        self.calls.append({"tenant_id": tenant_id, "query": query,
                           "snapshot_ver": snapshot_ver})
        return self._results


class FakeCaseReader:
    def __init__(self, case: dict | None = None) -> None:
        self._case = case or {"id": "c-91", "severity": "medium",
                              "display_projection": {"amount": "1250.50", "merchant": "ACME"}}

    async def get_case(self, *, tenant_id, case_id, auth_token) -> dict:
        return {**self._case, "id": case_id}


class FakeIngestionReader:
    def __init__(self, connector_types: list[dict] | None = None,
                 preview: dict | None = None) -> None:
        self._types = connector_types or [
            {"connector_type": "s3", "display_name": "Amazon S3",
             "category": "object-store", "secret_fields": ["access_key_id",
             "secret_access_key"]},
            {"connector_type": "postgres", "display_name": "PostgreSQL",
             "category": "database", "secret_fields": ["password"]}]
        self._preview = preview or {}
        self.calls: list[dict] = []

    async def connector_types(self, *, tenant_id, auth_token) -> list[dict]:
        self.calls.append({"op": "connector_types", "tenant_id": tenant_id})
        return self._types

    async def preview(self, *, tenant_id, connection_id, auth_token, table=None,
                      path=None, query=None, limit=50) -> dict:
        self.calls.append({"op": "preview", "connection_id": connection_id,
                           "table": table, "path": path})
        return self._preview


class FakeSemanticReader:
    """Governed measures + dimensions double for the dashboard-designer grounding."""

    def __init__(self, metrics: list[dict] | None = None,
                 dimensions: list[dict] | None = None,
                 verified_queries: list[dict] | None = None) -> None:
        self._verified_queries = verified_queries if verified_queries is not None else [
            {"id": "vq-1", "nl_text": "Total paid claims by claim type",
             "sql_text": "SELECT claim_type, SUM(amount) FROM claims GROUP BY 1",
             "variables": [], "tags": ["claims"], "model_id": "m-1", "score": 0.91}]
        self._metrics = metrics if metrics is not None else [
            {"name": "claim_count", "description": "Number of claims", "agg": "count",
             "entity": "claims", "model": "claims_core", "model_version": "claims_core@v1"},
            {"name": "total_amount", "description": "Total claimed amount", "agg": "sum",
             "entity": "claims", "model": "claims_core", "model_version": "claims_core@v1"}]
        self._dimensions = dimensions if dimensions is not None else [
            {"name": "claim_type", "type": "categorical", "entity": "claims",
             "model": "claims_core", "model_version": "claims_core@v1"},
            {"name": "created_at", "type": "time", "entity": "claims",
             "time_grains": ["day", "week", "month"],
             "model": "claims_core", "model_version": "claims_core@v1"}]
        self.calls: list[dict] = []

    async def get_metrics(self, *, tenant_id, auth_token, workspace_id=None,
                          model=None) -> list[dict]:
        self.calls.append({"op": "get_metrics", "workspace_id": workspace_id})
        return self._metrics

    async def get_dimensions(self, *, tenant_id, auth_token, workspace_id=None,
                             model=None) -> list[dict]:
        self.calls.append({"op": "get_dimensions", "workspace_id": workspace_id})
        return self._dimensions

    async def search_verified_queries(self, *, tenant_id, auth_token, query,
                                      workspace_id=None, top_k=5) -> list[dict]:
        self.calls.append({"op": "search_verified_queries",
                           "workspace_id": workspace_id, "query": query})
        return self._verified_queries


class FakeChartCatalog:
    """Chart-type catalog double for the dashboard-designer grounding."""

    def __init__(self, chart_types: list[dict] | None = None) -> None:
        self._types = chart_types if chart_types is not None else [
            {"name": "vertical_bar_chart", "family": "axis", "data_class": "query"},
            {"name": "line_chart", "family": "axis", "data_class": "query"},
            {"name": "grid_chart", "family": "grid", "data_class": "query"},
            {"name": "big_number", "family": "single", "data_class": "query"}]
        self.calls: list[dict] = []

    async def list_chart_types(self, *, auth_token) -> list[dict]:
        self.calls.append({"op": "list_chart_types"})
        return self._types


class FakeExperimentReader:
    """Registered-model + versions double for the inference agent's grounding."""

    def __init__(self, models: list[dict] | None = None,
                 model: dict | None = None, runs: list[dict] | None = None) -> None:
        self._runs = runs or []
        self._models = models if models is not None else [
            {"id": "m-claims", "name": "claims-fraud",
             "urn": "wr:t:experiment:model/m-claims", "model_type": "classification"}]
        self._model = model if model is not None else {
            "model": {"id": "m-claims", "name": "claims-fraud",
                      "urn": "wr:t:experiment:model/m-claims"},
            "versions": [
                {"model_id": "m-claims", "version": 1, "stage": "archived",
                 "input_schema": None},
                {"model_id": "m-claims", "version": 2, "stage": "production",
                 "input_schema": [{"name": "amount", "type": "double", "required": True}]}]}
        self.calls: list[dict] = []

    async def list_models(self, *, tenant_id, auth_token, limit=200) -> list[dict]:
        self.calls.append({"op": "list_models", "tenant_id": tenant_id})
        return self._models

    async def get_model(self, *, tenant_id, model_id, auth_token) -> dict:
        self.calls.append({"op": "get_model", "model_id": model_id})
        return self._model

    async def best_runs(self, *, tenant_id, algorithm, auth_token, limit=5) -> list[dict]:
        self.calls.append({"op": "best_runs", "algorithm": algorithm})
        return self._runs


class FakeDatasetReader:
    """Dataset catalog + schema double for the inference agent's grounding."""

    def __init__(self, datasets: list[dict] | None = None,
                 schema: dict | None = None) -> None:
        self._datasets = datasets if datasets is not None else [
            {"id": "ds-claims", "name": "auto-claims-latest",
             "urn": "wr:t:dataset:dataset/ds-claims", "status": "ready",
             "created_at": "2026-07-11T18:00:00+00:00"}]
        self._schema = schema if schema is not None else {
            "version_no": 1, "row_count": 14,
            "schema": {"amount": {"type": "double", "nullable": False}}}
        self.calls: list[dict] = []

    async def list_datasets(self, *, tenant_id, auth_token, q=None, limit=200) -> list[dict]:
        self.calls.append({"op": "list_datasets", "q": q})
        return self._datasets

    async def get_schema(self, *, tenant_id, dataset_id, auth_token) -> dict:
        self.calls.append({"op": "get_schema", "dataset_id": dataset_id})
        return self._schema


class FakePipelineReader:
    """Algorithm-template catalog + parameter-schema double for the model-training
    agent's grounding."""

    def __init__(self, algorithms: list[dict] | None = None,
                 algorithm: dict | None = None) -> None:
        self._algorithms = algorithms if algorithms is not None else [
            {"name": "xgboost", "label": "XGBoost", "model_type": "classification",
             "runnable": True},
            {"name": "random_forest", "label": "Random Forest",
             "model_type": "classification", "runnable": True}]
        self._algorithm = algorithm if algorithm is not None else {
            "name": "xgboost", "label": "XGBoost",
            "input_type": {"training": ["TRAIN"]},
            "parameters": {
                "n_estimators": {"type": "int", "minimum": 1, "maximum": 2000,
                                 "default": 200},
                "max_depth": {"type": "int", "minimum": 1, "maximum": 32, "default": 6},
                "learning_rate": {"type": "number", "minimum": 0.0001, "maximum": 1.0,
                                  "default": 0.1}},
            "runnable": True}
        self.calls: list[dict] = []

    async def list_algorithms(self, *, tenant_id, auth_token) -> list[dict]:
        self.calls.append({"op": "list_algorithms", "tenant_id": tenant_id})
        return self._algorithms

    async def get_algorithm(self, *, tenant_id, algorithm, auth_token) -> dict:
        self.calls.append({"op": "get_algorithm", "algorithm": algorithm})
        return {**self._algorithm, "name": algorithm}

    async def get_run(self, *, tenant_id, run_id, auth_token) -> dict:
        self.calls.append({"op": "get_run", "run_id": run_id})
        return {"id": run_id, "status": "succeeded",
                "mlflow_run_id": f"mlrun-{run_id}",
                "model_uri": f"models:/fake/{run_id}",
                "metrics": {"accuracy": 0.91, "f1": 0.88}}


class FakePipelineWriter:
    """Training-launch double for the ml-engineer agent: records every
    instantiate() and returns a deterministic run id (the paired
    FakePipelineReader.get_run reports it succeeded with fixed metrics)."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._fail = fail_with
        self._n = 0

    async def instantiate(self, *, tenant_id, algorithm, auth_token, dataset_refs,
                          params, workspace_id=None, name=None, mode="train") -> dict:
        if self._fail is not None:
            raise self._fail
        self._n += 1
        self.calls.append({"op": "instantiate", "algorithm": algorithm,
                           "dataset_refs": dataset_refs, "params": params,
                           "mode": mode, "workspace_id": workspace_id, "name": name})
        return {"id": f"run-{self._n}", "status": "queued"}


class NoopRealtime:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, *, topic, event, data, tenant_id=None,
                      ttl_seconds=None) -> None:
        self.events.append({"topic": topic, "event": event, "data": data,
                            "tenant_id": tenant_id})
