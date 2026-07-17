"""Pack installer: turns a validated manifest into ordered ensure_* calls
against the real platform (client.py) and writes an install ledger.

Install order (dependencies first — BRD 23 §PKG-FR-021 sequencing applied to
today's Core):
  datasets → semantic_models → verified_queries → saved_queries →
  dashboards → dispositions → cases → roles → agent_configs → memories →
  pipelines

Cross-component references inside component files:
  * `dataset: <identity>`   — resolved to the ingested dataset's URN
  * `{{dataset('<name>')}}` — left verbatim (query-service resolves the macro)
  * measure URNs in chart sources use `measure: <name>` and are expanded to
    `wr:<tenant>:semantic:measure/<name>`

The ledger (JSON) is the factual record of what happened: every action is
`create | noop | verify | warn | failed` — a failed action fails the install
(exit code 1) unless `--keep-going`. Deferred manifest entries are copied into
the ledger verbatim so the "what awaits future Core services" record is
auditable, never lost, and never faked as installed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .client import PlatformClient, any_failed
from .manifest import Manifest, load_component_file

INSTALL_ORDER = (
    "datasets", "semantic_models", "verified_queries", "saved_queries",
    "dashboards", "dispositions", "decision_models", "cases", "roles",
    "agent_configs", "memories", "pipelines",
)


@dataclass(slots=True)
class InstallResult:
    ok: bool
    ledger_path: Path
    actions: list[dict]
    failed: list[dict]


def _expand_sources(client: PlatformClient, sources: list[dict],
                    saved_query_ids: dict[str, str]) -> list[dict]:
    out = []
    for i, s in enumerate(sources or []):
        if "measure" in s:
            out.append({"position": i, "source_type": "semantic_measure",
                        "source_urn":
                            f"wr:{client.tenant_id}:semantic:measure/{s['measure']}"})
        elif "saved_query" in s:
            qid = saved_query_ids.get(s["saved_query"])
            out.append({"position": i, "source_type": "saved_query",
                        "source_urn": f"wr:{client.tenant_id}:query:query/{qid}"})
        else:
            out.append({"position": i, **s})
    return out


def install(manifest: Manifest, client: PlatformClient,
            ledger_dir: str | Path | None = None,
            keep_going: bool = False) -> InstallResult:
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    client.log(f"==> installing pack {manifest.name}@{manifest.version} "
               f"into workspace {client.workspace_id}")

    dataset_urns: dict[str, str] = {}     # identity -> urn
    saved_query_ids: dict[str, str] = {}  # identity -> id

    def resolve_dataset(ref: str) -> str | None:
        return dataset_urns.get(ref)

    for kind in INSTALL_ORDER:
        comps = manifest.components_of(kind)
        if not comps:
            continue
        client.log(f"--> {kind} ({len(comps)} component file(s))")
        for comp in comps:
            doc = load_component_file(manifest, comp)

            if kind == "datasets":
                for ds in doc if isinstance(doc, list) else [doc]:
                    data_file = Path(manifest.pack_dir) / ds["file"]
                    urn = client.ensure_dataset(
                        comp.identity if len(doc) == 1 else ds["identity"],
                        ds["name"], data_file.read_bytes(),
                        ds.get("format", "csv"))
                    if urn:
                        dataset_urns[ds["identity"]] = urn

            elif kind == "semantic_models":
                definition = dict(doc["definition"])
                # rebind entity dataset_urns from identities to live URNs
                for entity in definition.get("entities", []):
                    ref = entity.pop("dataset", None)
                    if ref:
                        urn = resolve_dataset(ref)
                        if not urn:
                            client._record(kind, comp.identity, "failed", None,
                                           f"unknown dataset ref {ref!r}")
                            continue
                        entity["dataset_urn"] = urn
                client.ensure_semantic_model(
                    comp.identity, doc["name"], doc.get("description", ""),
                    definition)

            elif kind == "verified_queries":
                for i, vq in enumerate(doc):
                    client.ensure_verified_query(
                        f"{comp.identity}_{i}", vq["nl_text"], vq["sql_text"],
                        vq.get("model"), vq.get("tags", []))

            elif kind == "saved_queries":
                for q in doc if isinstance(doc, list) else [doc]:
                    qid = client.ensure_saved_query(
                        q["identity"], q["name"], q["sql"],
                        q.get("description", ""), q.get("tags", []))
                    if qid:
                        saved_query_ids[q["identity"]] = qid

            elif kind == "dashboards":
                spec = dict(doc)
                for chart in spec.get("charts", []):
                    chart["sources"] = _expand_sources(
                        client, chart.get("sources", []), saved_query_ids)
                client.ensure_dashboard(comp.identity, spec)

            elif kind == "dispositions":
                for d in doc:
                    client.ensure_disposition(
                        comp.identity, d["code"], d["label"], d["category"],
                        d.get("requires_note", False))

            elif kind == "decision_models":
                for dm in doc if isinstance(doc, list) else [doc]:
                    client.ensure_decision_model(
                        dm["identity"] if isinstance(doc, list) else comp.identity,
                        dm["name"], dm["rules"], dm.get("default_outcome"))

            elif kind == "cases":
                urn = resolve_dataset(doc["dataset"])
                if not urn:
                    client._record(kind, comp.identity, "failed", None,
                                   f"unknown dataset ref {doc['dataset']!r}")
                else:
                    client.create_cases(comp.identity, urn, doc["rows"],
                                        doc.get("due_days", 7))

            elif kind == "roles":
                for role in doc:
                    client.ensure_role(comp.identity, role["name"],
                                       role["actions"])

            elif kind == "agent_configs":
                for cfg in doc:
                    client.ensure_agent_config(
                        comp.identity, cfg["agent_key"],
                        cfg.get("prompt_params", {}),
                        cfg.get("enabled", True))

            elif kind == "memories":
                client.ensure_memories(
                    comp.identity, doc,
                    source_tag=f"pack:{manifest.name}")

            elif kind == "pipelines":
                for p in doc:
                    urn = resolve_dataset(p["dataset"])
                    if not urn:
                        client._record(kind, comp.identity, "failed", None,
                                       f"unknown dataset ref {p['dataset']!r}")
                        continue
                    client.ensure_pipeline(comp.identity, p["algorithm"],
                                           p["name"], urn,
                                           p.get("mode", "train"))

            failed_now = any_failed(client)
            if failed_now and not keep_going:
                break
        if any_failed(client) and not keep_going:
            break

    failed = any_failed(client)
    ledger = {
        "pack": manifest.name, "version": manifest.version,
        "workspace_id": client.workspace_id, "tenant_id": client.tenant_id,
        "started_at": started,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actions": client.actions,
        "deferred": manifest.deferred,
        "result": "failed" if failed else "installed",
    }
    ledger_dir = Path(ledger_dir or Path(manifest.pack_dir) / ".ledgers")
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / (
        f"{manifest.name}-{manifest.version}-{time.strftime('%Y%m%d%H%M%S')}.json")
    ledger_path.write_text(json.dumps(ledger, indent=2))
    client.log(f"==> {ledger['result']}: {len(client.actions)} actions, "
               f"{len(failed)} failed, {len(manifest.deferred)} deferred — "
               f"ledger {ledger_path}")
    return InstallResult(ok=not failed, ledger_path=ledger_path,
                         actions=client.actions, failed=failed)
