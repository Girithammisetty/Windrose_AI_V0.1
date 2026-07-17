"""Real adapters are the DEFAULT (CONVENTIONS END STATE) + online-sampling safety
(EVL-FR-021c / AC-9, BR-6). No live infra needed — adapters connect lazily."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.domain.errors import ValidationFailed
from app.domain.online import fair_sample
from app.main import create_app


def test_real_adapters_are_default_in_app_main():
    # Default Settings() (no EVAL_USE_REAL_ADAPTERS in env) must be real.
    assert Settings().use_real_adapters is True
    # create_app() with no args wires the real runtime container by default.
    app = create_app()
    c = app.state.container
    assert type(c.authz).__name__ == "OpaAuthzClient"
    assert type(c.bus).__name__ == "KafkaEventBus"
    assert type(c.dedup).__name__ == "RedisDedupStore"
    assert type(c.judge_client).__name__ == "AiGatewayJudgeClient"
    assert type(c.warehouse).__name__ == "DuckDbFixtureWarehouse"
    uow = c.uow_factory("00000000-0000-0000-0000-000000000000")
    assert type(uow).__name__ == "SqlUnitOfWork"
    # the shipped default DSN uses the non-owner eval_app_rt role
    assert "://eval_app_rt:" in c.settings.database_url


def test_default_dsn_is_non_owner_role():
    assert Settings().database_url.startswith("postgresql+asyncpg://eval_app_rt:")


def test_online_fair_sampling_per_tenant_caps():  # AC-9 / BR-6
    traces = {
        "tenant-hot": [{"trace_id": f"h{i}"} for i in range(1000)],
        "tenant-cold": [{"trace_id": f"c{i}"} for i in range(20)],
    }
    sampled = fair_sample(traces, sample_pct=0.05, per_tenant_cap=10)
    # a high-volume tenant is capped, so it cannot dominate the signal
    assert len(sampled["tenant-hot"]) == 10
    assert len(sampled["tenant-cold"]) == 1  # 5% of 20 = 1


async def test_online_scoring_rejects_reexecution_scorer(container):  # AC-9
    # sql_result_equivalence re-executes SQL -> forbidden online.
    with pytest.raises(ValidationFailed):
        await container.online_service.score_traces(
            "t", [{"trace_id": "x", "output": {}}], ["sql_result_equivalence"])


async def test_online_scoring_is_post_hoc_no_agent_invocation(container):  # AC-9
    # Online scoring never calls a candidate provider — it scores the trace's own
    # output. Prove runtime-call absence by asserting the container's default
    # provider is never touched (schema_validity scores the emitted output only).
    class ExplodingProvider:
        async def candidate_output(self, **kw):
            raise AssertionError("online scoring must NOT re-invoke the agent (AC-9)")

    container.default_provider = ExplodingProvider()
    trace = {"trace_id": "tr-1",
             "output": {"structured": {"severity": "high"}},
             "expected": {"kind": "structured",
                          "value": {"schema": {"type": "object", "required": ["severity"]}}}}
    results = await container.online_service.score_traces("t", [trace], ["schema_validity"])
    assert results and results[0]["scorer_key"] == "schema_validity"
    assert results[0]["passed"] is True
