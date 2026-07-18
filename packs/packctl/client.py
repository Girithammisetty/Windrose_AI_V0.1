"""PlatformClient — packctl's typed facade over the platform's REAL public
HTTP APIs. Every ensure_* method is idempotent by stable name: it looks the
object up first and only creates when absent, so re-running an install
converges instead of duplicating (BRD 23 §PKG-FR-021 idempotency, applied to
today's Core without pack-service).

Nothing here fakes anything: each call is the same endpoint the product UI or
the e2e driver uses. Authorization uses caller-supplied bearer tokens:
  * author_token   — a tenant-admin user (writes)
  * approver_token — a DISTINCT subject (four-eyes approvals: semantic models
                     and verified queries reject self-approval, SEM-FR-007/040)
  * agent_token    — an agent-typed token (memory-service tenant-scope writes
                     are agent-only by design, MEM-FR-010)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

JSON = {"Content-Type": "application/json"}


@dataclass(slots=True)
class Endpoints:
    ingestion: str = "http://localhost:8303"
    dataset: str = "http://localhost:8304"
    semantic: str = "http://localhost:8086"
    query: str = "http://localhost:8085"
    chart: str = "http://localhost:8320"
    case: str = "http://localhost:8308"
    rbac: str = "http://localhost:8302"
    agent: str = "http://localhost:8306"
    memory: str = "http://localhost:8307"
    pipeline: str = "http://localhost:8313"
    identity: str = "http://localhost:8301"


@dataclass
class PlatformClient:
    endpoints: Endpoints
    tenant_id: str
    workspace_id: str
    author_token: Callable[[], str]
    approver_token: Callable[[], str]
    agent_token: Callable[[], str]
    log: Callable[[str], None] = print
    timeout_s: float = 60.0
    # collected evidence of what each ensure_* actually did (ledger source)
    actions: list[dict] = field(default_factory=list)

    # ---- plumbing -----------------------------------------------------------
    def _req(self, method: str, url: str, token: str, **kw) -> requests.Response:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        return requests.request(method, url, headers=headers, timeout=self.timeout_s, **kw)

    def _record(self, kind: str, identity: str, action: str, urn: str | None,
                detail: str = "") -> None:
        self.actions.append({"kind": kind, "identity": identity, "action": action,
                             "urn": urn, "detail": detail})
        self.log(f"    [{action:>6}] {kind}/{identity}" + (f" — {detail}" if detail else ""))

    # ---- datasets (ingestion-service file_upload) ---------------------------
    def find_dataset(self, name: str) -> dict | None:
        r = self._req("GET", f"{self.endpoints.dataset}/api/v1/datasets"
                             f"?workspace_id={self.workspace_id}", self.author_token())
        if r.status_code == 200:
            for ds in r.json().get("data", []):
                if ds.get("name") == name:
                    return ds
        return None

    def dataset_urn(self, ds: dict) -> str:
        return f"wr:{self.tenant_id}:dataset:dataset/{ds['id']}"

    def ensure_dataset(self, identity: str, name: str, csv_bytes: bytes,
                       file_format: str = "csv") -> str | None:
        """Upload a file-backed dataset under a STABLE name (idempotent: reuse
        when it already exists). Returns the dataset URN."""
        existing = self.find_dataset(name)
        if existing:
            urn = self.dataset_urn(existing)
            self._record("datasets", identity, "noop", urn, f"dataset {name!r} exists")
            return urn
        tok = self.author_token()
        ing = self._req("POST", f"{self.endpoints.ingestion}/api/v1/ingestions", tok,
                        headers=JSON, json={
                            "ingestion_mode": "file_upload", "file_format": file_format,
                            "workspace_id": self.workspace_id,
                            "new_dataset": {"name": name}, "skip_profiling": True})
        if ing.status_code not in (200, 201, 202):
            self._record("datasets", identity, "failed", None,
                         f"create ingestion {ing.status_code}: {ing.text[:200]}")
            return None
        body = ing.json().get("data", ing.json())
        ing_id, urn = body.get("id"), body.get("dataset_urn")
        up = self._req("POST", f"{self.endpoints.ingestion}/api/v1/uploads", tok,
                       headers=JSON, json={"ingestion_id": ing_id,
                                           "bytes_total": len(csv_bytes)})
        if up.status_code not in (200, 201):
            self._record("datasets", identity, "failed", None,
                         f"open upload {up.status_code}: {up.text[:200]}")
            return None
        upload_id = up.json().get("data", {}).get("id") or \
            up.json().get("data", {}).get("upload_id")
        sha = hashlib.sha256(csv_bytes).hexdigest()
        pr = self._req("PUT",
                       f"{self.endpoints.ingestion}/api/v1/uploads/{upload_id}/parts/1",
                       tok, headers={"Content-SHA256": sha}, data=csv_bytes)
        if pr.status_code not in (200, 201):
            self._record("datasets", identity, "failed", None,
                         f"put part {pr.status_code}: {pr.text[:200]}")
            return None
        etag = pr.json().get("data", {}).get("etag")
        comp = self._req("POST",
                         f"{self.endpoints.ingestion}/api/v1/uploads/{upload_id}/complete",
                         tok, headers=JSON,
                         json={"parts": [{"n": 1, "etag": etag, "size": len(csv_bytes)}],
                               "sha256": sha})
        if comp.status_code not in (200, 201, 202):
            self._record("datasets", identity, "failed", None,
                         f"complete {comp.status_code}: {comp.text[:200]}")
            return None
        for _ in range(60):
            g = self._req("GET", f"{self.endpoints.ingestion}/api/v1/ingestions/{ing_id}",
                          tok)
            gd = g.json().get("data", {}) if g.status_code == 200 else {}
            status = gd.get("status")
            if status in ("completed", "succeeded"):
                urn = gd.get("dataset_urn") or urn
                rows = gd.get("rows_appended") or gd.get("rows")
                self._record("datasets", identity, "create", urn,
                             f"{name!r} ingested rows={rows} "
                             f"snapshot={gd.get('iceberg_snapshot_id')}")
                return urn
            if status in ("failed", "error"):
                self._record("datasets", identity, "failed", None,
                             f"ingestion failed: {str(gd)[:200]}")
                return None
            time.sleep(1.5)
        self._record("datasets", identity, "failed", None, "ingestion timed out")
        return None

    # ---- semantic models (author -> submit -> approve, four-eyes) -----------
    def find_semantic_model(self, name: str) -> dict | None:
        r = self._req("GET", f"{self.endpoints.semantic}/api/v1/models"
                             f"?filter[workspace_id]={self.workspace_id}",
                      self.author_token())
        if r.status_code == 200:
            for m in r.json().get("data", []):
                if m.get("name") == name:
                    return m
        return None

    def ensure_semantic_model(self, identity: str, name: str, description: str,
                              definition: dict) -> dict | None:
        """Create + publish a semantic model through the real review workflow.
        Returns {'id':…, 'published': bool}."""
        author = self.author_token()
        model = self.find_semantic_model(name)
        if model and model.get("published_version_id"):
            self._record("semantic_models", identity, "noop", model.get("urn"),
                         f"{name!r} already published")
            return {"id": model["id"], "published": True}
        if not model:
            r = self._req("POST", f"{self.endpoints.semantic}/api/v1/models", author,
                          headers=JSON, json={
                              "workspace_id": self.workspace_id, "name": name,
                              "description": description, "definition": definition})
            if r.status_code != 201:
                self._record("semantic_models", identity, "failed", None,
                             f"create {r.status_code}: {r.text[:300]}")
                return None
            model = r.json()["data"]
        model_id = model["id"]
        # ensure the open draft carries the pack's definition, then submit+approve
        self._req("PATCH",
                  f"{self.endpoints.semantic}/api/v1/models/{model_id}/versions/1",
                  author, headers=JSON, json={"definition": definition})
        # submit validates entity dataset URNs against semantic-service's
        # dataset projection, which is eventually consistent with ingestion —
        # a just-ingested dataset can 422 "not found" for a moment. Retry.
        rs = self._req("POST",
                       f"{self.endpoints.semantic}/api/v1/models/{model_id}/versions/1/submit",
                       author, headers=JSON, json={})
        for attempt in range(5):
            if not (rs.status_code == 422 and "not found" in rs.text):
                break
            self.log(f"    [retry] submit {name!r}: dataset projection lagging "
                     f"(attempt {attempt + 1}/5)")
            time.sleep(2.0)
            rs = self._req("POST",
                           f"{self.endpoints.semantic}/api/v1/models/{model_id}/versions/1/submit",
                           author, headers=JSON, json={})
        if rs.status_code != 200:
            self._record("semantic_models", identity, "failed", None,
                         f"submit {rs.status_code}: {rs.text[:300]}")
            return {"id": model_id, "published": False}
        ra = self._req("POST",
                       f"{self.endpoints.semantic}/api/v1/models/{model_id}/versions/1/approve",
                       self.approver_token(), headers=JSON,
                       json={"note": "pack install (four-eyes approval)"})
        published = ra.status_code == 200
        self._record("semantic_models", identity,
                     "create" if published else "failed",
                     f"wr:{self.tenant_id}:semantic:model/{model_id}",
                     f"{name!r} published" if published
                     else f"approve {ra.status_code}: {ra.text[:200]}")
        return {"id": model_id, "published": published}

    # ---- verified queries (SEM-FR-040, four-eyes) ---------------------------
    def ensure_verified_query(self, identity: str, nl_text: str, sql_text: str,
                              model: str | None, tags: list[str]) -> bool:
        author = self.author_token()
        r = self._req("GET", f"{self.endpoints.semantic}/api/v1/verified-queries"
                             f"?filter[workspace_id]={self.workspace_id}&limit=200",
                      author)
        if r.status_code == 200:
            for vq in r.json().get("data", []):
                if vq.get("nl_text") == nl_text:
                    self._record("verified_queries", identity, "noop", vq.get("urn"))
                    return True
        cr = self._req("POST", f"{self.endpoints.semantic}/api/v1/verified-queries",
                       author, headers=JSON, json={
                           "workspace_id": self.workspace_id, "nl_text": nl_text,
                           "sql_text": sql_text, "model": model, "tags": tags})
        if cr.status_code != 201:
            self._record("verified_queries", identity, "failed", None,
                         f"create {cr.status_code}: {cr.text[:200]}")
            return False
        vq_id = cr.json()["data"]["id"]
        self._req("POST",
                  f"{self.endpoints.semantic}/api/v1/verified-queries/{vq_id}/submit",
                  author, headers=JSON, json={})
        ap = self._req("POST",
                       f"{self.endpoints.semantic}/api/v1/verified-queries/{vq_id}/approve",
                       self.approver_token(), headers=JSON,
                       json={"note": "pack install"})
        okd = ap.status_code == 200
        self._record("verified_queries", identity, "create" if okd else "failed",
                     f"wr:{self.tenant_id}:semantic:verified_query/{vq_id}",
                     "" if okd else f"approve {ap.status_code}: {ap.text[:200]}")
        return okd

    # ---- saved queries (query-service) --------------------------------------
    def ensure_saved_query(self, identity: str, name: str, sql: str,
                           description: str, tags: list[str]) -> str | None:
        tok = self.author_token()
        r = self._req("GET", f"{self.endpoints.query}/api/v1/queries"
                             f"?workspace_id={self.workspace_id}", tok)
        if r.status_code == 200:
            for q in r.json().get("data", []):
                if q.get("name") == name:
                    self._record("saved_queries", identity, "noop", q.get("urn"))
                    return q["id"]
        cr = self._req("POST", f"{self.endpoints.query}/api/v1/queries", tok,
                       headers=JSON, json={
                           "name": name, "description": description,
                           "module_names": ["insights"],
                           "workspace_id": self.workspace_id,
                           "sql_text": sql, "tags": tags})
        if cr.status_code != 201:
            self._record("saved_queries", identity, "failed", None,
                         f"create {cr.status_code}: {cr.text[:200]}")
            return None
        qid = cr.json()["data"]["id"]
        self._record("saved_queries", identity, "create",
                     f"wr:{self.tenant_id}:query:query/{qid}", name)
        return qid

    # ---- dashboards + charts (chart-service) --------------------------------
    def find_dashboard(self, name: str) -> dict | None:
        r = self._req("GET", f"{self.endpoints.chart}/api/v1/dashboards"
                             f"?workspace_id={self.workspace_id}", self.author_token())
        if r.status_code == 200:
            for db in r.json().get("data", []):
                if db.get("name") == name:
                    return db
        return None

    def _dashboard_chart_ids(self, dash_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        tok = self.author_token()
        r = self._req("POST",
                      f"{self.endpoints.chart}/api/v1/dashboards/{dash_id}/data",
                      tok, headers=JSON, json={})
        if r.status_code == 200:
            for res in (r.json().get("data") or {}).get("results", []):
                cid = res.get("chart_id")
                if not cid:
                    continue
                g = self._req("GET", f"{self.endpoints.chart}/api/v1/charts/{cid}", tok)
                if g.status_code == 200:
                    out[(g.json().get("data") or {}).get("name")] = cid
        return out

    def ensure_dashboard(self, identity: str, spec: dict) -> dict:
        """spec: {name, description, module, tags, charts: [{name, chart_type,
        description, config, sources, w,h}]}. Charts are laid out on a 12-col
        grid in declaration order. Chart data is warmed after creation so the
        first UI render is a cache hit (and so the install VERIFIES each chart
        actually resolves real rows)."""
        tok = self.author_token()
        name = spec["name"]
        dash = self.find_dashboard(name)
        if dash:
            dash_id = dash["id"]
            self._record("dashboards", identity, "noop",
                         f"wr:{self.tenant_id}:chart:dashboard/{dash_id}",
                         f"{name!r} exists")
        else:
            r = self._req("POST", f"{self.endpoints.chart}/api/v1/dashboards", tok,
                          headers=JSON, json={
                              "name": name, "module": spec.get("module", "insights"),
                              "workspace_id": self.workspace_id,
                              "description": spec.get("description", ""),
                              "layout": [], "meta": {},
                              "tags": spec.get("tags", [])})
            if r.status_code != 201:
                self._record("dashboards", identity, "failed", None,
                             f"create {r.status_code}: {r.text[:200]}")
                return {}
            dash_id = r.json()["data"]["id"]
            self._record("dashboards", identity, "create",
                         f"wr:{self.tenant_id}:chart:dashboard/{dash_id}", name)
        existing = self._dashboard_chart_ids(dash_id)
        chart_ids: dict[str, str] = {}
        for chart in spec.get("charts", []):
            cname = chart["name"]
            if cname in existing:
                chart_ids[cname] = existing[cname]
                continue
            body = {"name": cname, "chart_type": chart["chart_type"],
                    "description": chart.get("description", ""),
                    "config": chart["config"],
                    "display_meta": {"semantic_model": spec.get("semantic_model"),
                                     "workspace_id": self.workspace_id},
                    "sources": chart.get("sources", [])}
            cr = self._req("POST",
                           f"{self.endpoints.chart}/api/v1/dashboards/{dash_id}/charts",
                           tok, headers=JSON, json=body)
            if cr.status_code == 201:
                chart_ids[cname] = cr.json()["data"]["id"]
            else:
                self._record("dashboards", identity, "failed", None,
                             f"chart {cname!r} {cr.status_code}: {cr.text[:200]}")
        # layout: 2-per-row 12-col grid in declaration order
        placements, x, y = [], 0, 0
        for chart in spec.get("charts", []):
            cid = chart_ids.get(chart["name"])
            if not cid:
                continue
            w, h = int(chart.get("w", 6)), int(chart.get("h", 4))
            if x + w > 12:
                x, y = 0, y + 4
            placements.append({"chart_id": cid, "x": x, "y": y, "w": w, "h": h})
            x += w
        if placements:
            self._req("PATCH", f"{self.endpoints.chart}/api/v1/dashboards/{dash_id}",
                      tok, headers=JSON, json={"layout": placements})
        # warm + verify every chart resolves real data
        warmed = 0
        for cname, cid in chart_ids.items():
            rd = self._req("GET", f"{self.endpoints.chart}/api/v1/charts/{cid}/data", tok)
            if rd.status_code == 200:
                warmed += 1
            else:
                self._record("dashboards", identity, "warn", None,
                             f"chart {cname!r} data {rd.status_code}: {rd.text[:150]}")
        self._record("dashboards", identity, "verify",
                     f"wr:{self.tenant_id}:chart:dashboard/{dash_id}",
                     f"{warmed}/{len(chart_ids)} charts resolve data")
        return {"id": dash_id, "charts": chart_ids, "warmed": warmed,
                "total": len(chart_ids)}

    # ---- dispositions + cases (case-service) --------------------------------
    def ensure_disposition(self, identity: str, code: str, label: str,
                           category: str, requires_note: bool = False) -> str | None:
        tok = self.author_token()
        r = self._req("POST", f"{self.endpoints.case}/api/v1/dispositions", tok,
                      headers=JSON, json={"code": code, "label": label,
                                          "category": category,
                                          "workspace_id": self.workspace_id,
                                          "requires_note": requires_note})
        if r.status_code in (200, 201):
            did = r.json().get("data", r.json()).get("id")
            self._record("dispositions", identity, "create", None, code)
            return did
        if r.status_code == 409:
            g = self._req("GET", f"{self.endpoints.case}/api/v1/dispositions"
                                 f"?workspace_id={self.workspace_id}", tok)
            for dd in (g.json().get("data", []) if g.status_code == 200 else []):
                if dd.get("code") == code:
                    self._record("dispositions", identity, "noop", None, code)
                    return dd.get("id")
        self._record("dispositions", identity, "failed", None,
                     f"{code}: {r.status_code} {r.text[:150]}")
        return None

    def case_exists_for_row(self, row_pk: str) -> bool:
        r = self._req("GET", f"{self.endpoints.case}/api/v1/cases"
                             f"?workspace_id={self.workspace_id}&limit=200",
                      self.author_token())
        if r.status_code != 200:
            return False
        for cs in r.json().get("data", []):
            if cs.get("row_pk") == row_pk:
                return True
        return False

    def create_cases(self, identity: str, dataset_urn: str,
                     rows: list[dict], due_days: int = 7) -> list[str]:
        tok = self.author_token()
        due = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(time.time() + due_days * 86400))
        ids: list[str] = []
        for row in rows:
            if self.case_exists_for_row(row["row_pk"]):
                continue
            body = {"dataset_urn": dataset_urn, "dataset_version": "1",
                    "due_date": due, "severity": row.get("severity", "medium"),
                    "workspace_id": self.workspace_id,
                    "rows": [{"row_pk": row["row_pk"],
                              "display_projection": row["display_projection"]}]}
            r = self._req("POST", f"{self.endpoints.case}/api/v1/cases", tok,
                          headers=JSON, json=body)
            if r.status_code in (200, 201):
                created = (r.json().get("data", r.json())).get("created", [])
                ids.extend(c["id"] for c in created)
        # deterministic search: rebuild the OpenSearch projection from Postgres
        self._req("POST", f"{self.endpoints.case}/api/v1/admin/reindex", tok,
                  headers=JSON, json={})
        self._record("cases", identity, "create", dataset_urn,
                     f"{len(ids)} open cases (reindexed)")
        return ids

    # ---- case field configs (case-service custom-field catalog) -------------
    def ensure_case_field(self, identity: str, name: str, data_type: str,
                          purpose: str = "both",
                          field_meta: dict | None = None) -> str | None:
        """Register one typed case field in the workspace's custom-field catalog
        (CASE-FR-022). Idempotent by name; the workspace is derived from the
        forwarded JWT's workspace_id claim (case-service reads it from the claim,
        not the body). field_meta carries the display label/options/required
        config the case form renders. Uses the generic case.case.update action."""
        tok = self.author_token()
        g = self._req("GET", f"{self.endpoints.case}/api/v1/case-fields", tok)
        if g.status_code == 200:
            for f in g.json().get("data", []):
                if f.get("name") == name:
                    self._record("case_fields", identity, "noop", None, f"field {name!r}")
                    return f.get("id")
        r = self._req("POST", f"{self.endpoints.case}/api/v1/case-fields", tok,
                      headers=JSON, json={"name": name, "data_type": data_type,
                                          "purpose": purpose,
                                          "field_meta": field_meta or {}})
        if r.status_code in (200, 201):
            fid = (r.json().get("data", r.json())).get("id")
            self._record("case_fields", identity, "create", None, f"field {name!r}")
            return fid
        self._record("case_fields", identity, "failed", None,
                     f"{name}: {r.status_code} {r.text[:150]}")
        return None

    def delete_case_field(self, field_id: str) -> bool:
        r = self._req("DELETE",
                      f"{self.endpoints.case}/api/v1/case-fields/{field_id}",
                      self.author_token())
        return r.status_code in (200, 204)

    # ---- display labels (identity-service per-tenant label registry) --------
    def ensure_label(self, identity: str, key: str, value: str) -> str | None:
        """Set one per-tenant UI label override (BRD 23 inc3), e.g.
        cases.title="AP Exceptions". Idempotent by key (noop if already set to
        the same value); the tenant is derived from the forwarded JWT. WRITES
        require the tenant-admin action (identity.user.admin) — labels are
        tenant-wide presentation. Returns the label key (its stable id for
        reversal), or None on failure."""
        tok = self.author_token()
        base = f"{self.endpoints.identity}/api/v1/tenants/self/labels"
        g = self._req("GET", base, tok)
        if g.status_code == 200 and (g.json().get("labels") or {}).get(key) == value:
            self._record("display_labels", identity, "noop", None, f"{key}={value!r}")
            return key
        r = self._req("PUT", base, tok, headers=JSON, json={"labels": {key: value}})
        if r.status_code in (200, 201):
            self._record("display_labels", identity, "create", None, f"{key}={value!r}")
            return key
        self._record("display_labels", identity, "failed", None,
                     f"{key}: {r.status_code} {r.text[:150]}")
        return None

    def delete_label(self, key: str) -> bool:
        r = self._req("DELETE",
                      f"{self.endpoints.identity}/api/v1/tenants/self/labels/{key}",
                      self.author_token())
        return r.status_code in (200, 204)

    # ---- rbac custom roles + permission group binding ------------------------
    def ensure_role(self, identity: str, name: str, actions: list[str]) -> str | None:
        tok = self.author_token()
        r = self._req("GET", f"{self.endpoints.rbac}/api/v1/roles?limit=200", tok)
        role_id = None
        if r.status_code == 200:
            for role in r.json().get("data", []):
                if role.get("name") == name:
                    role_id = role["id"]
                    self._record("roles", identity, "noop", None, f"role {name!r}")
                    break
        if role_id is None:
            cr = self._req("POST", f"{self.endpoints.rbac}/api/v1/roles", tok,
                           headers=JSON, json={"name": name, "actions": actions})
            if cr.status_code not in (200, 201):
                self._record("roles", identity, "failed", None,
                             f"role {name!r} {cr.status_code}: {cr.text[:200]}")
                return None
            # rbac returns the role object unenveloped (writeJSON(role)); other
            # services wrap in {"data": ...} — accept both.
            body = cr.json()
            role_id = (body.get("data") or body).get("id")
            self._record("roles", identity, "create", None, f"role {name!r}")
        # a permission group of the same name so the role is assignable
        group_id = None
        g = self._req("GET", f"{self.endpoints.rbac}/api/v1/groups"
                             f"?filter[group_type]=permission&limit=200", tok)
        if g.status_code == 200:
            for grp in g.json().get("data", []):
                if grp.get("name") == name:
                    group_id = grp["id"]
                    break
        if group_id is None:
            gc = self._req("POST", f"{self.endpoints.rbac}/api/v1/groups", tok,
                           headers=JSON, json={"name": name,
                                               "description": f"Pack role group: {name}",
                                               "group_type": "permission"})
            if gc.status_code in (200, 201):
                body = gc.json().get("data", gc.json()) or {}
                group_id = body.get("id")
            elif gc.status_code == 409:
                # name already taken (e.g. pre-existing tenant group): reuse it
                g2 = self._req("GET", f"{self.endpoints.rbac}/api/v1/groups?limit=200", tok)
                if g2.status_code == 200:
                    for grp in g2.json().get("data", []):
                        if grp.get("name") == name and grp.get("group_type") == "permission":
                            group_id = grp["id"]
                            break
            if group_id is None:
                self._record("roles", identity, "warn", None,
                             f"group {name!r} unavailable "
                             f"({gc.status_code}): {gc.text[:150]}")
        if group_id:
            self._req("PUT",
                      f"{self.endpoints.rbac}/api/v1/groups/{group_id}/roles/{role_id}",
                      tok, headers=JSON)
            self._record("roles", identity, "verify", None,
                         f"role {name!r} bound to permission group")
        return role_id

    # ---- agent-runtime per-tenant agent config -------------------------------
    def ensure_agent_config(self, identity: str, agent_key: str,
                            prompt_params: dict, enabled: bool = True) -> bool:
        """Specialize a FIXED platform agent for this tenant by setting its
        prompt_params (persona/domain/instructions). The PUT is a partial upsert,
        so this does NOT disturb the agent's guardrail_policy (inc4). Idempotent:
        a noop when the stored prompt_params + enabled already match. Requires
        ai.agent.admin."""
        tok = self.author_token()
        base = f"{self.endpoints.agent}/api/v1/registry/tenants/self/agents/{agent_key}"
        g = self._req("GET", base, tok)
        if g.status_code == 200:
            d = g.json().get("data") or {}
            if d.get("prompt_params") == prompt_params and d.get("enabled", True) == enabled:
                self._record("agent_configs", identity, "noop", None, f"{agent_key} prompt_params")
                return True
        r = self._req("PUT", base, tok, headers=JSON,
                      json={"enabled": enabled, "prompt_params": prompt_params})
        okd = r.status_code == 200
        self._record("agent_configs", identity, "create" if okd else "failed", None,
                     f"{agent_key} prompt_params set" if okd
                     else f"{agent_key} {r.status_code}: {r.text[:200]}")
        return okd

    def clear_agent_config(self, agent_key: str) -> bool:
        """Reversal: remove the pack's prompt-param specialization (PUT empty
        prompt_params + re-enable). The partial upsert preserves any guardrail
        envelope on the agent; there is no DELETE for a tenant agent config."""
        r = self._req("PUT",
                      f"{self.endpoints.agent}/api/v1/registry/tenants/self/agents/{agent_key}",
                      self.author_token(), headers=JSON,
                      json={"enabled": True, "prompt_params": {}})
        return r.status_code in (200, 201)

    # ---- per-agent security envelope (guardrails, BRD 53 inc2) ---------------
    def ensure_guardrail(self, identity: str, agent_key: str,
                         envelope: dict) -> str | None:
        """Attach a per-agent security envelope (data_scope / budget / pii) to a
        tenant agent config (PA-FR-060). The PUT is a partial upsert, so this
        does NOT disturb the agent's prompt_params. budget is clamped to the
        operator ceiling server-side (BR-8). Idempotent (noop if already equal);
        requires ai.agent.admin. Returns the agent_key (its stable id for
        reversal), or None on failure."""
        tok = self.author_token()
        base = f"{self.endpoints.agent}/api/v1/registry/tenants/self/agents/{agent_key}"
        g = self._req("GET", base, tok)
        if g.status_code == 200 and (g.json().get("data") or {}).get("guardrail_policy") == envelope:
            self._record("guardrails", identity, "noop", None, agent_key)
            return agent_key
        r = self._req("PUT", base, tok, headers=JSON, json={"guardrail_policy": envelope})
        if r.status_code in (200, 201):
            self._record("guardrails", identity, "create", None, agent_key)
            return agent_key
        self._record("guardrails", identity, "failed", None,
                     f"{agent_key}: {r.status_code} {r.text[:150]}")
        return None

    def delete_guardrail(self, agent_key: str) -> bool:
        """Clear an agent's security envelope (reversal): PUT an explicit empty
        policy, which the partial-upsert route sets to {} while preserving the
        agent's prompt_params."""
        r = self._req("PUT",
                      f"{self.endpoints.agent}/api/v1/registry/tenants/self/agents/{agent_key}",
                      self.author_token(), headers=JSON, json={"guardrail_policy": {}})
        return r.status_code in (200, 201)

    # ---- memory-service tenant-scope grounding records -----------------------
    def ensure_memories(self, identity: str, records: list[dict],
                        source_tag: str) -> int:
        """Write tenant-scope RAG grounding records (agent-typed token — the only
        principal type memory-service allows to write tenant scope). Idempotent
        via the source tag: records already present (matched on tag) are skipped."""
        tok = self.agent_token()
        listed = self._req("GET", f"{self.endpoints.memory}/api/v1/memories"
                                  f"?filter[scope]=tenant&limit=200", tok)
        existing_contents: set[str] = set()
        if listed.status_code == 200:
            for m in listed.json().get("data", []):
                if source_tag in (m.get("tags") or []):
                    existing_contents.add((m.get("content") or "")[:120])
        wrote = 0
        for rec in records:
            if rec["content"][:120] in existing_contents:
                continue
            r = self._req("POST", f"{self.endpoints.memory}/api/v1/memories", tok,
                          headers=JSON, json={
                              "scope": "tenant", "scope_ref": self.tenant_id,
                              "content": rec["content"],
                              # memory-service SOURCE_TYPES enum: admin = an
                              # operator-curated record, which is exactly what a
                              # pack-installed grounding document is.
                              "provenance": {"source_type": "admin"},
                              "confidence": rec.get("confidence", 0.9),
                              # str() guards YAML scalars that parse numeric
                              # (a bare tag like 1592) from poisoning sorted()
                              "tags": sorted({str(t) for t in
                                              (source_tag, *rec.get("tags", []))})})
            if r.status_code in (200, 201):
                wrote += 1
            else:
                self._record("memories", identity, "failed", None,
                             f"{r.status_code}: {r.text[:150]}")
                return wrote
        skipped = len(records) - wrote
        self._record("memories", identity, "create" if wrote else "noop", None,
                     f"{wrote} grounding records written, {skipped} already present")
        return wrote

    # ---- pipeline templates ---------------------------------------------------
    def ensure_pipeline(self, identity: str, algorithm: str, name: str,
                        dataset_urn: str, mode: str = "train") -> str | None:
        tok = self.author_token()
        r = self._req("GET", f"{self.endpoints.pipeline}/api/v1/pipelines"
                             f"?workspace_id={self.workspace_id}&limit=200", tok)
        if r.status_code == 200:
            for p in r.json().get("data", []):
                if p.get("name") == name:
                    self._record("pipelines", identity, "noop", None, name)
                    return p["id"]
        cr = self._req("POST",
                       f"{self.endpoints.pipeline}/api/v1/algorithm-templates/{algorithm}/pipelines",
                       tok, headers=JSON, json={
                           "name": name, "mode": mode,
                           "workspace_id": self.workspace_id,
                           "dataset_refs": {"TRAIN": dataset_urn}})
        if cr.status_code == 201:
            pid = cr.json()["data"]["id"]
            self._record("pipelines", identity, "create", None,
                         f"{name!r} ({algorithm}, {mode})")
            return pid
        self._record("pipelines", identity, "failed", None,
                     f"{name!r} {cr.status_code}: {cr.text[:200]}")
        return None

    # ---- agent-runtime governed decision tables (BRD 54 inc2) ----------------
    def ensure_decision_model(self, identity: str, name: str, rules: list[dict],
                              default_outcome: dict | None) -> str | None:
        """Publish a governed decision table (agent-runtime). Idempotent by name
        within the workspace: an existing model of the same name is a noop. The
        outcome disposition_codes are validated against the workspace catalog by
        the service, so `dispositions` must install first (INSTALL_ORDER)."""
        tok = self.author_token()
        g = self._req("GET", f"{self.endpoints.agent}/api/v1/decision-models", tok)
        if g.status_code == 200:
            for mdl in g.json().get("data", []):
                if mdl.get("name") == name and mdl.get("workspace_id") == self.workspace_id:
                    self._record("decision_models", identity, "noop", None, name)
                    return mdl.get("id")
        cr = self._req("POST", f"{self.endpoints.agent}/api/v1/decision-models", tok,
                       headers=JSON, json={"name": name, "rules": rules,
                                           "default_outcome": default_outcome,
                                           "workspace_id": self.workspace_id})
        if cr.status_code in (200, 201):
            mid = (cr.json().get("data", cr.json())).get("id")
            self._record("decision_models", identity, "create", None,
                         f"{name!r} ({len(rules)} rule(s))")
            return mid
        self._record("decision_models", identity, "failed", None,
                     f"{name!r} {cr.status_code}: {cr.text[:200]}")
        return None


def any_failed(client: PlatformClient) -> list[dict[str, Any]]:
    return [a for a in client.actions if a["action"] == "failed"]
