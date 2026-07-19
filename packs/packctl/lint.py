"""Pack-authoring linter (BRD 23 PKG-FR-002) — deeper than the manifest schema.

``manifest.load_manifest`` validates STRUCTURE (envelope, kinds, file existence,
identity uniqueness). The linter validates CONTENT and CROSS-REFERENCES that the
loader can't see, so an author catches mistakes before an install fails halfway:

  * each component entry carries the fields its kind needs to materialize
    (a disposition without a ``category``, an archetype without a ``task_type``);
  * object names are unique within a kind across the pack's component files
    (two dispositions with the same ``code`` would collide on install);
  * a ``dataset:`` reference (from a cases queue, a pipeline template, or a
    semantic model's entity) resolves to a dataset the pack actually ships.

Findings carry a stable ``code`` + ``severity`` (``error`` blocks a publish;
``warning`` advises) + a location (component file + a json-pointer-ish path).
Pure and offline — no Core, no network — so it runs in CI and at author time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .manifest import ManifestError, load_component_file, load_manifest

# Per-ENTRY required fields, by kind. A missing/empty value is a blocking error
# because the matching ensure_* call would reject it (or send a broken object).
REQUIRED_FIELDS = {
    "dispositions": ["code", "label", "category"],
    "case_fields": ["name", "data_type"],
    "case_schemas": ["schema_key", "name"],
    "display_labels": ["key", "value"],
    "guardrails": ["agent_key"],
    "agent_configs": ["agent_key"],
    "eval_sets": ["dataset_key", "agent_key"],
    "model_archetypes": ["archetype_key", "name", "task_type"],
    "ontology": ["entity_key", "name"],
    "write_adapters": ["name", "connector_type"],
    "connection_templates": ["name", "connector_type"],
    "roles": ["name", "actions"],
    "decision_models": ["name", "rules"],
    "saved_queries": ["name", "sql"],
    "datasets": ["identity", "name", "file"],
    "pipelines": ["name", "algorithm", "dataset"],
    "verified_queries": ["nl_text", "sql_text"],
}

# The object-name field per kind — for duplicate detection + the dataset xref.
NAME_FIELD = {
    "dispositions": "code", "case_fields": "name", "case_schemas": "schema_key",
    "display_labels": "key", "guardrails": "agent_key", "agent_configs": "agent_key",
    "eval_sets": "dataset_key", "model_archetypes": "archetype_key", "ontology": "entity_key",
    "write_adapters": "name", "connection_templates": "name", "roles": "name",
    "decision_models": "name", "saved_queries": "name", "datasets": "identity",
    "pipelines": "name",
}

# Kinds whose component file is a single mapping, not a list of entries.
MAPPING_KINDS = {"semantic_models", "cases"}
# Kinds whose entries are freeform (no per-entry required-field check).
FREEFORM_KINDS = {"memories"}

DISPOSITION_CATEGORIES = {"true_positive", "false_positive", "benign", "inconclusive", "other"}


@dataclass(slots=True)
class Finding:
    code: str
    severity: str  # "error" | "warning"
    kind: str
    file: str
    pointer: str
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "kind": self.kind,
                "file": self.file, "pointer": self.pointer, "message": self.message}


@dataclass(slots=True)
class LintReport:
    pack: str | None
    version: str | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"pack": self.pack, "version": self.version, "ok": self.ok,
                "errors": len(self.errors), "warnings": len(self.warnings),
                "findings": [f.as_dict() for f in self.findings]}


def lint_pack(pack_dir: str | Path) -> LintReport:
    """Lint one pack directory. Returns a report; ``report.ok`` is False iff any
    error finding was raised. A malformed manifest yields a single error finding
    (the deeper content checks can't run without a valid component list)."""
    report = LintReport(pack=None, version=None)

    try:
        manifest = load_manifest(pack_dir)
    except ManifestError as exc:
        report.findings.append(Finding(exc.code, "error", "", exc.json_pointer, "", exc.message))
        return report
    report.pack, report.version = manifest.name, manifest.version

    dataset_ids: set[str] = set()
    dataset_refs: list[tuple[str, str, str]] = []  # (kind, file, ref)

    # ---- pass 1: per-file content + duplicates, collecting dataset ids/refs ----
    for comp in manifest.components:
        try:
            doc = load_component_file(manifest, comp)
        except yaml.YAMLError as exc:
            report.findings.append(Finding("COMPONENT_UNPARSEABLE", "error", comp.kind,
                                           comp.file, "/", f"YAML parse error: {exc}"))
            continue

        entries = _entries(comp.kind, doc)
        if entries is None:
            report.findings.append(Finding("WRONG_SHAPE", "error", comp.kind, comp.file, "/",
                                           f"{comp.kind} component must be a "
                                           f"{'mapping' if comp.kind in MAPPING_KINDS else 'list'}"))
            continue
        if not entries and comp.kind not in FREEFORM_KINDS:
            report.findings.append(Finding("EMPTY_COMPONENT", "warning", comp.kind, comp.file, "/",
                                           "component file is empty — it materializes nothing"))

        seen: set[str] = set()
        req = REQUIRED_FIELDS.get(comp.kind, [])
        for ptr, entry in entries:
            if not isinstance(entry, dict):
                if comp.kind not in FREEFORM_KINDS:
                    report.findings.append(Finding("ENTRY_NOT_MAPPING", "error", comp.kind,
                                                   comp.file, ptr, "entry must be a mapping"))
                continue
            for f in req:
                if entry.get(f) in (None, "", [], {}):
                    report.findings.append(Finding("MISSING_FIELD", "error", comp.kind, comp.file,
                                                   f"{ptr}/{f}", f"required field {f!r} is missing or empty"))
            nf = NAME_FIELD.get(comp.kind)
            name = entry.get(nf) if nf else None
            if name:
                if name in seen:
                    report.findings.append(Finding("DUPLICATE_NAME", "error", comp.kind, comp.file,
                                                   ptr, f"duplicate {nf} {name!r} in this kind"))
                seen.add(str(name))
            _kind_specific(comp.kind, entry, ptr, comp.file, report)

            # collect dataset ids + refs for the cross-ref pass
            if comp.kind == "datasets" and entry.get("identity"):
                dataset_ids.add(str(entry["identity"]))
            if comp.kind == "pipelines" and entry.get("dataset"):
                dataset_refs.append((comp.kind, comp.file, str(entry["dataset"])))
            if comp.kind == "cases" and entry.get("dataset"):
                dataset_refs.append((comp.kind, comp.file, str(entry["dataset"])))
            if comp.kind == "semantic_models":
                for e in (entry.get("definition") or {}).get("entities", []) or []:
                    if isinstance(e, dict) and e.get("dataset"):
                        dataset_refs.append((comp.kind, comp.file, str(e["dataset"])))

    # ---- pass 2: cross-references resolve within the pack ----------------------
    for kind, file, ref in dataset_refs:
        if ref not in dataset_ids:
            report.findings.append(Finding("DATASET_REF_UNRESOLVED", "error", kind, file, "/",
                                           f"references dataset {ref!r}, which the pack does not "
                                           f"declare (declared: {sorted(dataset_ids) or 'none'})"))

    # ---- deferred hygiene ------------------------------------------------------
    for i, d in enumerate(manifest.deferred):
        if len((d.get("reason") or "").strip()) < 10:
            report.findings.append(Finding("WEAK_DEFERRED_REASON", "warning", d.get("kind", ""),
                                           "pack.yaml", f"/deferred/{i}/reason",
                                           "deferred entry should explain WHY the kind is deferred"))

    return report


def _entries(kind: str, doc) -> list[tuple[str, object]] | None:
    """Normalize a component doc to a list of (pointer, entry). Returns None if
    the doc's top-level shape is wrong for the kind."""
    if kind in MAPPING_KINDS:
        if doc is None:
            return []
        if not isinstance(doc, dict):
            return None
        return [("/", doc)]
    # list kinds (some accept a single mapping too — normalize it to a 1-list)
    if doc is None:
        return []
    if isinstance(doc, dict):
        doc = [doc]
    if not isinstance(doc, list):
        return None
    return [(f"/{i}", e) for i, e in enumerate(doc)]


def _kind_specific(kind: str, entry: dict, ptr: str, file: str, report: LintReport) -> None:
    """Kind-specific best-practice checks (warnings — Core is the enum authority)."""
    if kind == "dispositions":
        cat = entry.get("category")
        if cat and cat not in DISPOSITION_CATEGORIES:
            report.findings.append(Finding("UNKNOWN_CATEGORY", "warning", kind, file, f"{ptr}/category",
                                           f"category {cat!r} is not in the Core closed set "
                                           f"{sorted(DISPOSITION_CATEGORIES)}"))
    if kind == "cases":
        rows = entry.get("rows")
        if not rows:
            report.findings.append(Finding("MISSING_FIELD", "error", kind, file, f"{ptr}/rows",
                                           "cases component needs a non-empty 'rows' list"))
        else:
            for j, row in enumerate(rows if isinstance(rows, list) else []):
                if not isinstance(row, dict) or not row.get("row_pk"):
                    report.findings.append(Finding("MISSING_FIELD", "error", kind, file,
                                                   f"{ptr}/rows/{j}/row_pk", "each seed case row needs a row_pk"))
        if not entry.get("dataset"):
            report.findings.append(Finding("MISSING_FIELD", "error", kind, file, f"{ptr}/dataset",
                                           "cases component needs a 'dataset' reference"))
    if kind == "semantic_models" and not entry.get("name"):
        report.findings.append(Finding("MISSING_FIELD", "error", kind, file, f"{ptr}/name",
                                       "semantic model needs a 'name'"))
