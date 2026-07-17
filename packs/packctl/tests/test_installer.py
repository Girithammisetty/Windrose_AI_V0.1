"""Offline installer tests: install ordering, cross-component dataset-URN
resolution, ledger content, and failure stop-behavior — against a recording
fake of PlatformClient's surface (no network; the REAL client is exercised by
the live pack installs, whose ledgers are the e2e evidence)."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packctl.installer import INSTALL_ORDER, install  # noqa: E402
from packctl.manifest import load_manifest  # noqa: E402


class RecordingClient:
    """Duck-types the PlatformClient surface installer.py drives."""

    def __init__(self, fail_kind: str | None = None):
        self.tenant_id = "t-1"
        self.workspace_id = "ws-1"
        self.actions: list[dict] = []
        self.calls: list[tuple] = []
        self.fail_kind = fail_kind
        self.log = lambda *_: None

    def _record(self, kind, identity, action, urn, detail=""):
        self.actions.append({"kind": kind, "identity": identity,
                             "action": action, "urn": urn, "detail": detail})

    def ensure_dataset(self, identity, name, data, file_format="csv"):
        self.calls.append(("datasets", name))
        if self.fail_kind == "datasets":
            self._record("datasets", identity, "failed", None, "boom")
            return None
        urn = f"wr:t-1:dataset:dataset/{name}"
        self._record("datasets", identity, "create", urn)
        return urn

    def ensure_semantic_model(self, identity, name, description, definition):
        self.calls.append(("semantic_models", name, definition))
        self._record("semantic_models", identity, "create", None)
        return {"id": "m-1", "published": True}

    def ensure_disposition(self, identity, code, label, category, requires_note=False):
        self.calls.append(("dispositions", code))
        self._record("dispositions", identity, "create", None, code)
        return "d-1"

    def create_cases(self, identity, dataset_urn, rows, due_days=7):
        self.calls.append(("cases", dataset_urn, len(rows)))
        self._record("cases", identity, "create", dataset_urn, f"{len(rows)}")
        return ["c-1"]


def _write_pack(tmp_path: Path) -> Path:
    (tmp_path / "pack.yaml").write_text(textwrap.dedent("""
        pack_manifest: 1
        name: order-test
        version: 1.0.0
        publisher: { id: pub-t, name: T }
        description: "ordering test"
        components:
          cases:
            - { file: "queue.yaml", identity: "queue" }
          datasets:
            - { file: "datasets.yaml", identity: "datasets" }
          semantic_models:
            - { file: "model.yaml", identity: "model" }
          dispositions:
            - { file: "dispositions.yaml", identity: "dispositions" }
        deferred:
          - { kind: guardrails, reason: "not in core" }
    """))
    (tmp_path / "datasets.yaml").write_text(
        "- {identity: main_ds, name: main-ds, file: rows.csv}\n")
    (tmp_path / "rows.csv").write_text("id,v\n1,2\n")
    (tmp_path / "model.yaml").write_text(textwrap.dedent("""
        name: m
        description: d
        definition:
          entities:
            - name: e
              dataset: main_ds
              table: main.main_ds
              primary_key: [id]
              dataset_version_policy: { policy: latest }
          dimensions: []
          measures: [{ name: n, entity: e, agg: count }]
    """))
    (tmp_path / "dispositions.yaml").write_text(
        "- {code: ok, label: OK, category: benign}\n")
    (tmp_path / "queue.yaml").write_text(textwrap.dedent("""
        dataset: main_ds
        rows:
          - { row_pk: "1", severity: low, display_projection: { id: "1" } }
    """))
    return tmp_path


def test_install_order_and_urn_resolution(tmp_path):
    manifest = load_manifest(_write_pack(tmp_path))
    client = RecordingClient()
    result = install(manifest, client, ledger_dir=tmp_path / "ledgers")
    assert result.ok
    kinds_in_call_order = [c[0] for c in client.calls]
    # datasets run before semantic models before dispositions before cases,
    # regardless of manifest declaration order.
    assert kinds_in_call_order == ["datasets", "semantic_models",
                                   "dispositions", "cases"]
    # the semantic entity's `dataset:` ref was rebound to the live URN
    _, _, definition = client.calls[1]
    assert definition["entities"][0]["dataset_urn"] == "wr:t-1:dataset:dataset/main-ds"
    assert "dataset" not in definition["entities"][0]
    # cases resolved the same URN
    assert client.calls[3][1] == "wr:t-1:dataset:dataset/main-ds"


def test_ledger_records_actions_and_deferred(tmp_path):
    manifest = load_manifest(_write_pack(tmp_path))
    client = RecordingClient()
    result = install(manifest, client, ledger_dir=tmp_path / "ledgers")
    ledger = json.loads(result.ledger_path.read_text())
    assert ledger["result"] == "installed"
    assert ledger["pack"] == "order-test"
    assert ledger["deferred"][0]["kind"] == "guardrails"
    assert any(a["kind"] == "datasets" and a["action"] == "create"
               for a in ledger["actions"])


def test_failure_stops_install_and_marks_ledger_failed(tmp_path):
    manifest = load_manifest(_write_pack(tmp_path))
    client = RecordingClient(fail_kind="datasets")
    result = install(manifest, client, ledger_dir=tmp_path / "ledgers")
    assert not result.ok
    ledger = json.loads(result.ledger_path.read_text())
    assert ledger["result"] == "failed"
    # nothing after the failing kind ran
    assert [c[0] for c in client.calls] == ["datasets"]


def test_install_order_constant_is_complete():
    from packctl.manifest import SUPPORTED_KINDS
    assert set(INSTALL_ORDER) == set(SUPPORTED_KINDS)
