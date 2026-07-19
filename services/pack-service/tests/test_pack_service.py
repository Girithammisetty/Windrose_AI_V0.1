"""Unit tests for the pack catalog + install planner (no live stack needed)."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from app.domain import catalog, installer

REPO_PACKS = Path(__file__).resolve().parents[3] / "packs"


@pytest.fixture(autouse=True)
def _configure_catalog():
    catalog.configure(str(REPO_PACKS))


def test_catalog_lists_real_packs():
    packs = catalog.list_packs()
    names = {p["name"] for p in packs}
    assert "card-disputes" in names
    assert len(packs) >= 10  # the repo ships 28 authored packs
    cd = next(p for p in packs if p["name"] == "card-disputes")
    assert cd["version"]  # semver present
    assert cd["components"].get("dispositions", 0) >= 1
    assert "agent_recipes" in cd["deferred_kinds"]  # honest deferral surfaced


def test_get_pack_detail_and_missing():
    detail = catalog.get_pack("card-disputes")
    assert detail is not None
    assert detail["deferred"]  # list of {kind, reason}
    assert all("reason" in d for d in detail["deferred"])
    assert catalog.get_pack("no-such-pack") is None


def test_origin_tag_and_urn_id():
    of = installer.origin_tag("card-disputes", "1.0.0")
    assert of("dispositions", "dispositions") == "pack:card-disputes@1.0.0:dispositions/dispositions"
    assert installer._urn_id("wr:t:query:query/abc-123") == "abc-123"
    assert installer._urn_id(None) is None


def test_inc1_kinds_and_reversibility_contract():
    # inc1 materializes self-contained kinds (no dataset/four-eyes chain).
    # inc3 adds case_fields (case-service custom-field catalog) here.
    assert set(installer.INC1_KINDS) == {"dispositions", "case_fields", "case_schemas",
                                         "display_labels", "guardrails", "agent_configs",
                                         "eval_sets", "model_archetypes", "ontology",
                                         "roles", "decision_models"}
    assert "model_archetypes" in installer.REVERSIBLE_KINDS  # DELETE /archetypes/{key}
    assert "case_schemas" in installer.REVERSIBLE_KINDS  # DELETE /case-schemas/{key}
    assert "ontology" in installer.REVERSIBLE_KINDS  # DELETE /ontology/entities/{key}
    assert "saved_queries" not in installer.INC1_KINDS  # needs its datasets first
    # Roles/case_fields carry a real Core delete verb → reversible; dispositions/
    # decision tables do not (tombstoned honestly on uninstall).
    assert "roles" in installer.REVERSIBLE_KINDS
    assert "case_fields" in installer.REVERSIBLE_KINDS  # DELETE /case-fields/{id}
    assert "display_labels" in installer.REVERSIBLE_KINDS  # DELETE /tenants/self/labels/{key}
    assert "guardrails" in installer.REVERSIBLE_KINDS  # PUT empty envelope clears it
    assert "agent_configs" in installer.REVERSIBLE_KINDS  # PUT empty prompt_params clears it
    assert "dispositions" not in installer.REVERSIBLE_KINDS
    assert "decision_models" not in installer.REVERSIBLE_KINDS


def test_guardrail_envelope_binds_workspace_and_clamps_shape():
    ws = "019f62c1-0f5e-7af0-b9be-cbe343ea0ad4"
    env = installer._guardrail_envelope(
        {"budget": {"max_tokens_per_session": 60000},
         "pii": {"block_pii_egress": True, "redact": True},
         "bind_workspace": True}, ws)
    assert env["budget"]["max_tokens_per_session"] == 60000
    assert env["pii"]["block_pii_egress"] is True
    assert env["data_scope"]["workspaces"] == [ws]  # install workspace injected
    # no bind_workspace, no data_scope key at all
    env2 = installer._guardrail_envelope({"pii": {"redact": True}}, ws)
    assert "data_scope" not in env2 and env2["pii"]["redact"] is True


def test_plan_marks_inc1_kinds_create_and_others_deferred():
    manifest = catalog.load_manifest("card-disputes")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": []}  # nothing exists yet → everything is a create

    class _FakeClient:
        workspace_id = "ws-1"
        endpoints = types.SimpleNamespace(
            case="c", rbac="r", query="q", agent="a", semantic="s",
            chart="ch", dataset="d", ingestion="i", memory="m", pipeline="p", identity="id")

        @staticmethod
        def author_token():
            return "tok"

        @staticmethod
        def _req(method, url, tok):
            return _Resp()

    ops = installer.plan(_FakeClient(), manifest)
    kinds = {o["kind"]: o["action"] for o in ops}
    # inc1 kinds present in card-disputes are planned as create
    assert kinds.get("dispositions") == "create"
    assert kinds.get("decision_models") == "create"
    # inc2 data chain is now materializable (create), not faked
    assert kinds.get("datasets") == "create"
    assert kinds.get("semantic_models") == "create"
    # dashboards wait for the steward to approve the semantic model (phase 2)
    assert any(o["kind"] == "dashboards" and o["action"] == "after_approval" for o in ops)


def test_plan_materializes_case_fields(tmp_path):
    # ap-invoice-audit ships a case_fields component (inc3) — it must plan as a
    # real create (case-service custom-field catalog), never `deferred`/faked.
    manifest = catalog.load_manifest("ap-invoice-audit")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": []}

    class _FakeClient:
        workspace_id = "ws-1"
        endpoints = types.SimpleNamespace(
            case="c", rbac="r", query="q", agent="a", semantic="s",
            chart="ch", dataset="d", ingestion="i", memory="m", pipeline="p", identity="id")

        @staticmethod
        def author_token():
            return "tok"

        @staticmethod
        def _req(method, url, tok):
            return _Resp()

    ops = installer.plan(_FakeClient(), manifest)
    field_ops = [o for o in ops if o["kind"] == "case_fields"]
    assert field_ops, "case_fields must appear in the plan"
    assert all(o["action"] == "create" for o in field_ops)
    names = {o["name"] for o in field_ops}
    assert {"root_cause", "oob_verified", "recovered_amount"} <= names
    # display_labels (inc3) also materialize as real creates (identity registry),
    # never deferred — the AP "Cases -> AP Exceptions" vocabulary.
    label_ops = [o for o in ops if o["kind"] == "display_labels"]
    assert label_ops and all(o["action"] == "create" for o in label_ops)
    assert {"cases.title", "nav.cases"} <= {o["name"] for o in label_ops}
    # guardrails (inc4) materialize as real creates onto the fixed agents the pack
    # specializes — never deferred.
    guard_ops = [o for o in ops if o["kind"] == "guardrails"]
    assert guard_ops and all(o["action"] == "create" for o in guard_ops)
    assert {"case-triage", "analytics"} <= {o["name"] for o in guard_ops}
    # agent_configs (inc5) — prompt-param specialization of the same fixed agents,
    # now materialized (was installer-deferred).
    cfg_ops = [o for o in ops if o["kind"] == "agent_configs"]
    assert cfg_ops and all(o["action"] == "create" for o in cfg_ops)
    assert {"case-triage", "analytics"} <= {o["name"] for o in cfg_ops}
    # cases (inc6) — the seeded worklist, one op per row_pk, materializable (was
    # deferred). Depends on the dataset URN, so it runs in the data chain.
    case_ops = [o for o in ops if o["kind"] == "cases"]
    assert case_ops and all(o["action"] == "create" for o in case_ops)
    assert any(o["name"] == "EX-7001" for o in case_ops)  # a real seed row_pk
    # pipelines (inc7) — algorithm-template seeds, data-chain (need the dataset),
    # materializable (was deferred).
    pipe_ops = [o for o in ops if o["kind"] == "pipelines"]
    assert pipe_ops and all(o["action"] == "create" for o in pipe_ops)
    assert {"Invoice anomaly detector (isolation forest)",
            "Exception outcome scorer (xgboost)"} <= {o["name"] for o in pipe_ops}
    # memories (inc7) — tenant grounding via the governed memory.corpus.admin
    # path, materializable (was deferred behind the agent-token barrier).
    mem_ops = [o for o in ops if o["kind"] == "memories"]
    assert mem_ops and all(o["action"] == "create" for o in mem_ops)
    # eval_sets (inc8) — golden eval dataset, materializable (was deferred behind
    # the eval-service unregisterable-verb barrier, now reconciled).
    eval_ops = [o for o in ops if o["kind"] == "eval_sets"]
    assert eval_ops and all(o["action"] == "create" for o in eval_ops)
    assert any(o["name"] == "ap_exception_triage_gold" for o in eval_ops)
    # model_archetypes (inc9) — governed model blueprints, materializable (new
    # experiment-service archetype registry).
    arch_ops = [o for o in ops if o["kind"] == "model_archetypes"]
    assert arch_ops and all(o["action"] == "create" for o in arch_ops)
    assert {"duplicate_pair_confidence", "vendor_fraud_risk_score"} <= {o["name"] for o in arch_ops}
    # case_schemas (inc10) — typed case types, materializable (new case-service
    # case-schema registry).
    schema_ops = [o for o in ops if o["kind"] == "case_schemas"]
    assert schema_ops and all(o["action"] == "create" for o in schema_ops)
    assert {"banking_change_verification", "duplicate_review",
            "shell_vendor_investigation"} <= {o["name"] for o in schema_ops}
    # ontology (inc11) — governed entity-type registry, materializable (new
    # dataset-service ontology surface).
    onto_ops = [o for o in ops if o["kind"] == "ontology"]
    assert onto_ops and all(o["action"] == "create" for o in onto_ops)
    assert {"vendor", "invoice", "payment_run", "exception"} <= {o["name"] for o in onto_ops}
