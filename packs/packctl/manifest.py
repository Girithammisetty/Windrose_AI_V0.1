"""Pack manifest loading + validation (BRD 23 §PKG-FR-001/002/003 — the subset
today's Core can materialize).

A pack is a directory containing `pack.yaml` plus component files. The manifest
schema follows BRD 23's envelope so these bundles stay forward-compatible with
the future pack-service (BRD 23); packctl is the Core-neutral installer that
drives ONLY the platform's existing public HTTP APIs (the seed_claims_demo.py
precedent, generalized).

Honesty contract (Rule 1: nothing faked): `components` may ONLY contain kinds
packctl actually materializes against today's Core. Every BRD-required
capability the Core cannot materialize yet (signed OCI artifacts, guardrail
policies, bespoke agent recipes, eval sets, live SoR connectors) belongs in the
manifest's `deferred` section — carried as documentation + a ledger entry,
never silently dropped and never faked.

Errors are structured {code, json_pointer, message} per PKG-FR-001.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
IDENTITY_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Kinds packctl materializes against today's Core, in install (dependency)
# order. Each maps to a real platform API — see client.py.
SUPPORTED_KINDS = (
    "datasets",           # ingestion-service file_upload -> registered dataset
    "semantic_models",    # semantic-service author -> submit -> approve (4-eyes)
    "verified_queries",   # semantic-service verified-queries + approve (4-eyes)
    "saved_queries",      # query-service saved queries ({{dataset()}} macros)
    "dashboards",         # chart-service dashboards + charts + layout
    "dispositions",       # case-service disposition taxonomy
    "cases",              # case-service seeded queue rows (+ reindex)
    "roles",              # rbac-service tenant custom roles + permission group
    "agent_configs",      # agent-runtime TenantAgentConfig prompt_params
    "memories",           # memory-service tenant-scope RAG grounding records
    "pipelines",          # pipeline-orchestrator algorithm-template pipelines
)

# Deferred kinds we RECOGNIZE (from BRD 23/24..31) so packs can declare them
# for the future pack-service without packctl pretending to install them.
KNOWN_DEFERRED_KINDS = (
    "ontology", "guardrails", "eval_sets", "agent_recipes",
    "connection_templates", "model_archetypes", "display_labels",
    "case_schemas", "write_adapters",
)


@dataclass(slots=True)
class ManifestError(Exception):
    code: str
    json_pointer: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - repr convenience
        return f"{self.code} at {self.json_pointer}: {self.message}"


@dataclass(slots=True)
class Component:
    kind: str
    file: str
    identity: str


@dataclass(slots=True)
class Manifest:
    name: str
    version: str
    description: str
    publisher: dict
    categories: list[str]
    regulatory: list[str]
    components: list[Component]
    deferred: list[dict] = field(default_factory=list)
    pack_dir: Path | None = None

    def components_of(self, kind: str) -> list[Component]:
        return [c for c in self.components if c.kind == kind]


def _err(code: str, pointer: str, message: str) -> ManifestError:
    return ManifestError(code=code, json_pointer=pointer, message=message)


def load_manifest(pack_dir: str | Path) -> Manifest:
    """Load + validate `<pack_dir>/pack.yaml`. Raises ManifestError on the
    first structural problem (structured code + json pointer)."""
    pack_dir = Path(pack_dir)
    path = pack_dir / "pack.yaml"
    if not path.is_file():
        raise _err("MANIFEST_MISSING", "/", f"{path} does not exist")
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise _err("MANIFEST_UNPARSEABLE", "/", str(exc)) from exc

    if doc.get("pack_manifest") != 1:
        raise _err("VALIDATION_FAILED", "/pack_manifest", "pack_manifest must be 1")
    name = doc.get("name") or ""
    if not NAME_RE.match(name):
        raise _err("VALIDATION_FAILED", "/name",
                   f"name {name!r} must match {NAME_RE.pattern}")
    version = str(doc.get("version") or "")
    if not SEMVER_RE.match(version):
        raise _err("VALIDATION_FAILED", "/version",
                   f"version {version!r} must be strict semver MAJOR.MINOR.PATCH")
    publisher = doc.get("publisher") or {}
    if not isinstance(publisher, dict) or not publisher.get("id"):
        raise _err("VALIDATION_FAILED", "/publisher", "publisher.id is required")
    description = doc.get("description") or ""
    if not description:
        raise _err("VALIDATION_FAILED", "/description", "description is required")

    raw_components = doc.get("components") or {}
    if not isinstance(raw_components, dict):
        raise _err("VALIDATION_FAILED", "/components", "components must be a map")
    components: list[Component] = []
    for kind, entries in raw_components.items():
        if kind not in SUPPORTED_KINDS:
            hint = (" (declare it under `deferred` — packctl never fakes an "
                    "install)" if kind in KNOWN_DEFERRED_KINDS else "")
            raise _err("MATERIALIZATION_TARGET_UNKNOWN", f"/components/{kind}",
                       f"kind {kind!r} is not materializable by packctl{hint}")
        if not isinstance(entries, list):
            raise _err("VALIDATION_FAILED", f"/components/{kind}",
                       "component entries must be a list")
        for i, entry in enumerate(entries):
            pointer = f"/components/{kind}/{i}"
            file_ref = (entry or {}).get("file") or ""
            identity = (entry or {}).get("identity") or ""
            if not file_ref:
                raise _err("VALIDATION_FAILED", pointer + "/file", "file is required")
            if not IDENTITY_RE.match(identity):
                raise _err("VALIDATION_FAILED", pointer + "/identity",
                           f"identity {identity!r} must match {IDENTITY_RE.pattern}")
            target = pack_dir / file_ref
            if not target.is_file():
                raise _err("VALIDATION_FAILED", pointer + "/file",
                           f"referenced file {file_ref!r} does not exist in the pack")
            components.append(Component(kind=kind, file=file_ref, identity=identity))
    # identity uniqueness within a kind (PKG-FR-003: upgrades match on identity)
    seen: set[tuple[str, str]] = set()
    for comp in components:
        key = (comp.kind, comp.identity)
        if key in seen:
            raise _err("VALIDATION_FAILED", f"/components/{comp.kind}",
                       f"duplicate identity {comp.identity!r}")
        seen.add(key)

    deferred = doc.get("deferred") or []
    if not isinstance(deferred, list):
        raise _err("VALIDATION_FAILED", "/deferred", "deferred must be a list")
    for i, item in enumerate(deferred):
        pointer = f"/deferred/{i}"
        if not isinstance(item, dict) or not item.get("kind") or not item.get("reason"):
            raise _err("VALIDATION_FAILED", pointer,
                       "each deferred entry needs kind + reason")
        if item["kind"] in SUPPORTED_KINDS:
            raise _err("VALIDATION_FAILED", pointer + "/kind",
                       f"kind {item['kind']!r} is installable — it must not be deferred")

    return Manifest(
        name=name, version=version, description=description, publisher=publisher,
        categories=list(doc.get("categories") or []),
        regulatory=list(doc.get("regulatory") or []),
        components=components, deferred=list(deferred), pack_dir=pack_dir,
    )


def load_component_file(manifest: Manifest, comp: Component) -> dict | list:
    """Parse one component's YAML file (already existence-checked)."""
    path = Path(manifest.pack_dir) / comp.file
    return yaml.safe_load(path.read_text())
