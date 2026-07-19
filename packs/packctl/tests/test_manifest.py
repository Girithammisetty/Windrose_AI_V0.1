"""Offline unit tests for the pack manifest validator (BRD 23 PKG-FR-001/003
subset): structural validation, unknown/deferred kind separation, identity
uniqueness, structured errors with JSON pointers."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packctl.manifest import ManifestError, load_manifest  # noqa: E402


def _write_pack(tmp_path: Path, manifest_yaml: str, files: dict[str, str] | None = None) -> Path:
    (tmp_path / "pack.yaml").write_text(textwrap.dedent(manifest_yaml))
    for rel, content in (files or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


VALID = """
pack_manifest: 1
name: test-pack
version: 1.0.0
publisher: { id: pub-windrose, name: "Windrose Inc." }
description: "A test pack."
categories: [test]
regulatory: []
components:
  dispositions:
    - { file: "cases/dispositions.yaml", identity: "dispositions" }
deferred:
  - { kind: connection_templates, reason: "not in core yet" }
"""


def test_valid_manifest_loads(tmp_path):
    pack = _write_pack(tmp_path, VALID, {"cases/dispositions.yaml": "[]"})
    m = load_manifest(pack)
    assert m.name == "test-pack" and m.version == "1.0.0"
    assert len(m.components) == 1 and m.components[0].kind == "dispositions"
    assert m.deferred[0]["kind"] == "connection_templates"


def test_missing_manifest_file(tmp_path):
    with pytest.raises(ManifestError) as e:
        load_manifest(tmp_path)
    assert e.value.code == "MANIFEST_MISSING"


@pytest.mark.parametrize("field,value,pointer", [
    ("pack_manifest", "2", "/pack_manifest"),
    ("name", "Bad_Name", "/name"),
    ("version", "1.0", "/version"),
])
def test_envelope_validation(tmp_path, field, value, pointer):
    bad = VALID.replace(
        {"pack_manifest": "pack_manifest: 1",
         "name": "name: test-pack",
         "version": "version: 1.0.0"}[field],
        f"{field}: {value}")
    pack = _write_pack(tmp_path, bad, {"cases/dispositions.yaml": "[]"})
    with pytest.raises(ManifestError) as e:
        load_manifest(pack)
    assert e.value.json_pointer == pointer


def test_unknown_kind_rejected_with_deferred_hint(tmp_path):
    bad = VALID.replace("dispositions:", "connection_templates:")
    pack = _write_pack(tmp_path, bad, {"cases/dispositions.yaml": "[]"})
    with pytest.raises(ManifestError) as e:
        load_manifest(pack)
    assert e.value.code == "MATERIALIZATION_TARGET_UNKNOWN"
    assert "deferred" in e.value.message  # known-deferred kinds get the hint


def test_component_file_must_exist(tmp_path):
    pack = _write_pack(tmp_path, VALID)  # no dispositions.yaml written
    with pytest.raises(ManifestError) as e:
        load_manifest(pack)
    assert e.value.code == "VALIDATION_FAILED"
    assert "does not exist" in e.value.message


def test_duplicate_identity_rejected(tmp_path):
    dup = VALID.replace(
        '- { file: "cases/dispositions.yaml", identity: "dispositions" }',
        '- { file: "cases/dispositions.yaml", identity: "dispositions" }\n'
        '    - { file: "cases/other.yaml", identity: "dispositions" }')
    pack = _write_pack(tmp_path, dup, {"cases/dispositions.yaml": "[]",
                                       "cases/other.yaml": "[]"})
    with pytest.raises(ManifestError) as e:
        load_manifest(pack)
    assert "duplicate identity" in e.value.message


def test_installable_kind_cannot_be_deferred(tmp_path):
    bad = VALID.replace("kind: connection_templates", "kind: dashboards")
    pack = _write_pack(tmp_path, bad, {"cases/dispositions.yaml": "[]"})
    with pytest.raises(ManifestError) as e:
        load_manifest(pack)
    assert "must not be deferred" in e.value.message
