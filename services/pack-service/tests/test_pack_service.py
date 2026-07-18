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
    assert "guardrails" in cd["deferred_kinds"]  # honest deferral surfaced


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
    # inc1 materializes only self-contained kinds (no dataset/four-eyes chain).
    assert set(installer.INC1_KINDS) == {"dispositions", "roles", "decision_models"}
    assert "saved_queries" not in installer.INC1_KINDS  # needs its datasets first
    # Roles carry a real Core delete verb → reversible; dispositions/decision
    # tables do not (tombstoned honestly on uninstall).
    assert "roles" in installer.REVERSIBLE_KINDS
    assert "dispositions" not in installer.REVERSIBLE_KINDS
    assert "decision_models" not in installer.REVERSIBLE_KINDS


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
            chart="ch", dataset="d", ingestion="i", memory="m", pipeline="p")

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
