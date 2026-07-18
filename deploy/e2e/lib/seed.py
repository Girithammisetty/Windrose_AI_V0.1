"""Platform seeding steps that must run during boot (before agent-runtime).

Subcommands:
  tenant   provision the acme-claims tenant via identity-service (real engine),
           print TENANT_ID.
  aigw     seed the ai-gateway fast-small -> qwen2.5:0.5b deployment and mint a
           tenant-scoped virtual key for agent-runtime; print the vkey secret.
  evalkey  mint a tenant-scoped, JUDGE-capable virtual key for eval-service's
           LLM-judge calls (reuses the deployment aigw seeded); print the secret.
  inference_tool <tenant_id>
           idempotently register+publish+enable the inference.submit write-
           proposal tool and point tool-plane's mcp_backends at inference-
           service's real facade (POST /internal/v1/mcp/invoke).
  ingestion_tool <tenant_id>
           idempotently register+publish+enable the ingestion.create write-
           proposal tool and point tool-plane's mcp_backends at ingestion-
           service's real facade (POST /internal/v1/mcp/invoke).
  chart_dashboard_tool <tenant_id>
           idempotently register+publish+enable the chart.dashboard.create
           write-proposal tool and point tool-plane's mcp_backends at
           chart-service's real facade (POST /internal/v1/mcp/invoke).
"""
from __future__ import annotations

import os
import sys
import time

import requests

import common as c


def _post(url, token, body, idem=None):
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if idem:
        h["Idempotency-Key"] = idem
    return requests.post(url, json=body, headers=h, timeout=30)


# A STABLE tenant name so re-provisioning reuses one tenant instead of minting a
# fresh `acme-claims-<timestamp>` every boot. The timestamped scheme drifted the
# TENANT_ID under a running UI/BFF (which bake WINDROSE_PERSONAS at startup),
# orphaning the rbac perm:* projection and leaving every persona with 0 caps.
STABLE_TENANT_NAME = os.environ.get("WINDROSE_E2E_TENANT_NAME", "acme-claims-e2e")

# Statuses from which a tenant is still usable for seeding (rbac group membership
# and the perm:* projection are independent of the k8s provisioning workflow, so
# provision_failed/provisioning are fine to reuse — the existing poll below is
# already best-effort). Only terminal-dead states force a fresh tenant.
_DEAD_TENANT_STATUS = {"archived", "deleted", "destroyed", "suspended"}


def _context_env_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "run", "context.env")


def _context_tenant_id() -> str | None:
    """The TENANT_ID recorded by a prior boot (deploy/e2e/run/context.env)."""
    p = _context_env_path()
    if not os.path.exists(p):
        return None
    for line in open(p):
        line = line.strip()
        if line.startswith("export TENANT_ID="):
            return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def _tenant_status(tok: str, tid: str) -> str | None:
    """Current status of a tenant, or None if it no longer resolves."""
    try:
        r = requests.get(f"{c.IDENTITY}/api/v1/tenants/{tid}",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=10)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return (r.json().get("status") or "").lower()


def _find_tenant_by_name(tok: str, name: str) -> str | None:
    """First non-dead tenant with an exact (case-insensitive) name match."""
    cursor = ""
    for _ in range(50):  # bounded page walk
        url = f"{c.IDENTITY}/api/v1/tenants?limit=100"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        body = r.json()
        for t in body.get("data", []) or []:
            if (t.get("name") or "").lower() == name.lower() \
               and (t.get("status") or "").lower() not in _DEAD_TENANT_STATUS:
                return t.get("id")
        page = body.get("page") or {}
        cursor = page.get("next_cursor") or ""
        if not cursor or not page.get("has_more"):
            break
    return None


def _reusable_tenant(tok: str) -> str | None:
    """Return an existing tenant to reuse, or None to create a fresh one.

    Preference order preserves continuity across boots:
      1. the tenant this environment last used (context.env), if still alive;
      2. any tenant already carrying the stable name.
    """
    prior = _context_tenant_id()
    if prior:
        st = _tenant_status(tok, prior)
        if st is not None and st not in _DEAD_TENANT_STATUS:
            print(f"reusing tenant from context.env: {prior} (status={st})", file=sys.stderr)
            return prior
    found = _find_tenant_by_name(tok, STABLE_TENANT_NAME)
    if found:
        print(f"reusing existing tenant by name '{STABLE_TENANT_NAME}': {found}", file=sys.stderr)
    return found


def provision_tenant() -> str:
    tok = c.superadmin_token()
    reuse = _reusable_tenant(tok)
    if reuse:
        print(reuse)
        return reuse
    body = {"name": STABLE_TENANT_NAME, "display_name": "Acme Claims Co",
            "owner_email": "admin@acme.test", "tier": "pool", "cloud": "aws", "publish": True}
    r = _post(f"{c.IDENTITY}/api/v1/tenants", tok, body, idem=f"e2e-{STABLE_TENANT_NAME}")
    if r.status_code not in (200, 201, 202):
        print(f"tenant create failed {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    tid = r.json()["tenant"]["id"]
    print(f"provisioned fresh tenant '{STABLE_TENANT_NAME}': {tid}", file=sys.stderr)
    # poll provisioning to active (best-effort; downstream does not require active)
    for _ in range(15):
        s = requests.get(f"{c.IDENTITY}/api/v1/tenants/{tid}/provisioning",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        steps = s.json().get("steps", []) if s.status_code == 200 else []
        states = {x["step_name"]: x["status"] for x in steps}
        if states and all(v == "succeeded" for v in states.values()):
            print(f"provisioning complete: {states}", file=sys.stderr)
            break
        if any(v == "failed" for v in states.values()):
            print(f"provisioning step failed: {states}", file=sys.stderr)
            break
        time.sleep(1)
    print(tid)
    return tid


def seed_aigw(tenant_id: str) -> str:
    """Operator bootstrap of the model registry: register the fast-small ->
    llama3.2:latest Ollama deployment and mint the agent's tenant-scoped virtual
    key. Seeded directly in ai-gateway's Postgres because the admin HTTP plane
    is gated on the *platform* action `ai.platform.admin` (not a tenant action),
    which real OPA only grants a platform operator. The RUNTIME chat path
    (agent-runtime -> ai-gateway -> Ollama) is unchanged and fully real/OPA-gated.
    key_hash uses the service's own scheme: sha256_hex("nk-<token>")."""
    import datetime as _dt
    import hashlib
    import secrets
    import uuid as _uuid

    import psycopg

    now = _dt.datetime.now(_dt.timezone.utc)
    vkey = f"nk-{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(vkey.encode()).hexdigest()
    dsn = "postgresql://windrose:windrose_dev@localhost:5432/ai_gateway"
    # provider_deployments are PLATFORM-shared infrastructure (which Ollama/Bedrock
    # endpoint serves each model-alias rung), read by the gateway under
    # settings.platform_tenant_id (pipeline._active_deployments). They MUST be
    # seeded under that platform tenant — not the per-request tenant. Pre-RLS-
    # hardening the gateway ran as the BYPASSRLS superuser so a per-tenant row was
    # still visible; as the non-superuser ai_gateway_app role under FORCE RLS the
    # deployment query is correctly scoped to the platform tenant, so a per-tenant
    # row is invisible → rung=-1 → UPSTREAM_UNAVAILABLE. Seed once at the platform
    # tenant, idempotently across per-tenant calls.
    platform_tenant = "00000000-0000-7000-8000-000000000001"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO provider_deployments
               (id, tenant_id, provider, model_family, deployment_name, region, cloud,
                endpoint_vault_ref, tpm_limit, rpm_limit, priority, status, created_at, updated_at)
               SELECT gen_random_uuid(), %s, 'ollama', v.fam, v.model, 'local', 'aws',
                      '', 1000000, 6000, 1, 'active', %s, %s
               FROM (VALUES ('fast-small','llama3.2:latest'),
                            ('balanced','qwen2.5:0.5b'),
                            ('frontier','qwen2.5:0.5b')) AS v(fam, model)
               WHERE NOT EXISTS (
                   SELECT 1 FROM provider_deployments d
                   WHERE d.tenant_id = %s AND d.model_family = v.fam)""",
            (platform_tenant, now, now, platform_tenant))
        conn.execute(
            """INSERT INTO virtual_keys
               (id, tenant_id, key_hash, principal_type, principal_id,
                allowed_request_classes, max_rung, status, created_at, updated_at)
               VALUES (%s,%s,%s,'agent',%s, %s, 3, 'active', %s, %s)
               ON CONFLICT (key_hash) DO NOTHING""",
            (str(_uuid.uuid4()), tenant_id, key_hash, f"{c.AGENT_ID}@{c.AGENT_VERSION}",
             ["chat", "embed"], now, now))
    print(vkey)
    return vkey


def register_inference_tool(tenant_id: str) -> str:
    """Idempotently register the ``inference.submit`` write-proposal tool in
    tool-plane and point it at inference-service's real MCP backend facade
    (POST /internal/v1/mcp/invoke), so an approved agent-runtime proposal is
    federated to a real batch-inference job instead of stopping at the gateway.
    Mirrors deploy/e2e/driver.py's register_apply_tool() (the case-service
    recipe) exactly: register tool -> register+publish version -> per-tenant
    enable (under a TENANT-scoped token, not the nil-tenant superadmin) ->
    upsert the mcp_backends row (platform-scoped, RLS via app.role='platform').
    Safe to call on every boot: registry POSTs are idempotent (id/version
    conflicts no-op or reuse), the tenant-enable PUT is a pure upsert, and the
    mcp_backends INSERT carries ON CONFLICT DO UPDATE."""
    import psycopg

    su = c.superadmin_token()
    tid = "inference.submit"
    ver = "1.0.0"
    inference_url = os.environ.get("INFERENCE_URL", c.INFERENCE)

    def _post(url, token, body):
        return requests.post(url, json=body,
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"}, timeout=15)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools", su,
             {"tool_id": tid, "display_name": "Submit batch inference job",
              "owner_service": "inference-service", "owner_team": "ml-platform",
              "enabled_by_default": True, "side_effects": "reversible",
              "tags": ["inference"]})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register inference.submit tool: {r.status_code} {r.text[:150]}", file=sys.stderr)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions", su,
             {"version": ver,
              "semantic_description": "Submit a batch inference job scoring an input "
              "dataset with a promoted model version. Use when a human has approved a "
              "copilot proposal to run batch inference.",
              "input_schema": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "model_id": {"type": "string"},
                                   "model_version": {"type": "integer"},
                                   "model_version_urn": {
                                       "type": "string",
                                       "x-windrose-urn":
                                           "wr:{tenant}:experiment:model_version/{value}",
                                       # Role-governed resource (see promote):
                                       # cross-tenant guarded, but not part of the
                                       # per-user obo-grant intersection.
                                       "x-windrose-urn-obo": False},
                                   "input_dataset_urn": {
                                       "type": "string",
                                       "x-windrose-urn": "wr:{tenant}:dataset:dataset/{value}",
                                       "x-windrose-urn-obo": False},
                                   "output_dataset_name": {"type": "string"},
                                   "workspace_id": {"type": "string"}},
                               "required": ["model_version_urn", "input_dataset_urn"]},
              "output_schema": {"type": "object", "additionalProperties": True},
              "permission_tier": "write-proposal", "cost_weight": 1,
              "declared_sla": {"p95_ms": 5000}, "side_effects": "reversible",
              "examples": []})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register inference.submit version: {r.status_code} {r.text[:150]}", file=sys.stderr)

    # Deprecate any other currently-published version so 1.0.0 resolves (the
    # registry allows a single published version; the tool_plane DB persists
    # across `make up` runs).
    try:
        with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane") as cn:
            pubs = [row[0] for row in cn.execute(
                "SELECT version FROM tool_versions WHERE tool_id=%s AND status='published' "
                "AND version<>%s", (tid, ver)).fetchall()]
        for vv in pubs:
            requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{vv}/deprecate",
                          headers={"Authorization": f"Bearer {su}"}, timeout=15)
    except Exception as e:
        print(f"deprecate prior published inference.submit versions: {e}", file=sys.stderr)

    pubr = requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{ver}/publish",
                         headers={"Authorization": f"Bearer {su}"}, timeout=20)
    if pubr.status_code not in (200, 201) and "only draft" not in pubr.text:
        print(f"publish inference.submit {ver}: {pubr.status_code} {pubr.text[:150]}",
             file=sys.stderr)

    # Per-tenant enablement MUST be under a token whose tenant == the caller
    # tenant (self == token tenant) -- the nil-tenant superadmin cannot see the
    # tool as enabled for TENANT.
    tenant_tok = c.service_token("svc:seed", tenant_id, ["*"])
    requests.put(f"{c.TOOL_REGISTRY}/api/v1/tenants/self/tools/{tid}",
                headers={"Authorization": f"Bearer {tenant_tok}",
                         "Content-Type": "application/json"},
                json={"enabled": True}, timeout=15)

    # Register inference-service as the MCP backend for this tool (platform-
    # scoped row, tenant 0…0, resolved by owner_service=inference-service) so
    # the gateway federates the approved write to the real facade.
    facade_url = f"{inference_url}/internal/v1/mcp/invoke"
    with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane",
                         autocommit=True) as cn:
        cn.execute("SELECT set_config('app.role','platform', false)")
        cn.execute(
            """INSERT INTO mcp_backends (name, tenant_id, internal_url, spiffe_id, kind, status)
               VALUES ('inference-service','00000000-0000-0000-0000-000000000000',%s,
                       'spiffe://windrose/ns/tools/sa/mcp-gateway','internal','active')
               ON CONFLICT (name) DO UPDATE SET internal_url=EXCLUDED.internal_url,
                   spiffe_id=EXCLUDED.spiffe_id, status='active'""",
            (facade_url,))
    print(f"inference.submit registered + enabled; mcp_backends -> {facade_url}", file=sys.stderr)
    return tid


def register_ingestion_tool(tenant_id: str) -> str:
    """Idempotently register the ``ingestion.create`` write-proposal tool in
    tool-plane and point it at ingestion-service's real MCP backend facade
    (POST /internal/v1/mcp/invoke), so an approved agent-runtime onboarding
    proposal is federated to a real ingestion job instead of stopping at the
    gateway. Mirrors register_inference_tool() (which mirrors
    deploy/e2e/driver.py's register_apply_tool(), the case-service recipe)
    exactly: register tool -> register+publish version -> per-tenant enable
    (under a TENANT-scoped token, not the nil-tenant superadmin) -> upsert the
    mcp_backends row (platform-scoped, RLS via app.role='platform'). Safe to
    call on every boot: registry POSTs are idempotent (id/version conflicts
    no-op or reuse), the tenant-enable PUT is a pure upsert, and the
    mcp_backends INSERT carries ON CONFLICT DO UPDATE."""
    import psycopg

    su = c.superadmin_token()
    tid = "ingestion.create"
    ver = "1.0.0"
    ingestion_url = os.environ.get("INGESTION_URL", c.INGESTION)

    def _post(url, token, body):
        return requests.post(url, json=body,
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"}, timeout=15)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools", su,
             {"tool_id": tid, "display_name": "Create an ingestion job",
              "owner_service": "ingestion-service", "owner_team": "data-platform",
              "enabled_by_default": True, "side_effects": "reversible",
              "tags": ["ingestion", "onboarding"]})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register ingestion.create tool: {r.status_code} {r.text[:150]}", file=sys.stderr)

    # input_schema mirrors the onboarding agent's WriteIntent.args exactly
    # (services/agent-runtime/app/graphs/onboarding.py `propose()`).
    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions", su,
             {"version": ver,
              "semantic_description": "Create an ingestion job that registers a new "
              "dataset from a source connector. Use when a human has approved a "
              "copilot proposal to onboard a data source.",
              "input_schema": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "connector_type": {"type": "string"},
                                   "ingestion_mode": {"type": "string",
                                       "enum": ["file_upload", "query",
                                                "scheduled_run", "webhook_batch"]},
                                   "file_format": {"type": ["string", "null"],
                                       "enum": ["csv", "tsv", "json", "jsonl",
                                                "parquet", "avro", None]},
                                   "new_dataset": {
                                       "type": "object",
                                       "properties": {
                                           "name": {"type": "string"},
                                           "description": {"type": "string"}},
                                       "required": ["name"]},
                                   "column_mapping": {
                                       "type": "array",
                                       "items": {"type": "object",
                                                "properties": {
                                                    "source": {"type": "string"},
                                                    "target": {"type": "string"}},
                                                "required": ["source", "target"]}},
                                   "connection_id": {
                                       "type": "string",
                                       "x-windrose-urn":
                                           "wr:{tenant}:ingestion:connection/{value}"},
                                   "workspace_id": {"type": "string"}},
                               "required": ["ingestion_mode", "connector_type",
                                            "column_mapping", "workspace_id"]},
              "output_schema": {"type": "object", "additionalProperties": True},
              "permission_tier": "write-proposal", "cost_weight": 1,
              "declared_sla": {"p95_ms": 15000}, "side_effects": "reversible",
              "examples": []})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register ingestion.create version: {r.status_code} {r.text[:150]}", file=sys.stderr)

    # Deprecate any other currently-published version so 1.0.0 resolves (the
    # registry allows a single published version; the tool_plane DB persists
    # across `make up` runs).
    try:
        with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane") as cn:
            pubs = [row[0] for row in cn.execute(
                "SELECT version FROM tool_versions WHERE tool_id=%s AND status='published' "
                "AND version<>%s", (tid, ver)).fetchall()]
        for vv in pubs:
            requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{vv}/deprecate",
                          headers={"Authorization": f"Bearer {su}"}, timeout=15)
    except Exception as e:
        print(f"deprecate prior published ingestion.create versions: {e}", file=sys.stderr)

    pubr = requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{ver}/publish",
                         headers={"Authorization": f"Bearer {su}"}, timeout=20)
    if pubr.status_code not in (200, 201) and "only draft" not in pubr.text:
        print(f"publish ingestion.create {ver}: {pubr.status_code} {pubr.text[:150]}",
             file=sys.stderr)

    # Per-tenant enablement MUST be under a token whose tenant == the caller
    # tenant (self == token tenant) -- the nil-tenant superadmin cannot see the
    # tool as enabled for TENANT.
    tenant_tok = c.service_token("svc:seed", tenant_id, ["*"])
    requests.put(f"{c.TOOL_REGISTRY}/api/v1/tenants/self/tools/{tid}",
                headers={"Authorization": f"Bearer {tenant_tok}",
                         "Content-Type": "application/json"},
                json={"enabled": True}, timeout=15)

    # Register ingestion-service as the MCP backend for this tool (platform-
    # scoped row, tenant 0…0, resolved by owner_service=ingestion-service) so
    # the gateway federates the approved write to the real facade.
    facade_url = f"{ingestion_url}/internal/v1/mcp/invoke"
    with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane",
                         autocommit=True) as cn:
        cn.execute("SELECT set_config('app.role','platform', false)")
        cn.execute(
            """INSERT INTO mcp_backends (name, tenant_id, internal_url, spiffe_id, kind, status)
               VALUES ('ingestion-service','00000000-0000-0000-0000-000000000000',%s,
                       'spiffe://windrose/ns/tools/sa/mcp-gateway','internal','active')
               ON CONFLICT (name) DO UPDATE SET internal_url=EXCLUDED.internal_url,
                   spiffe_id=EXCLUDED.spiffe_id, status='active'""",
            (facade_url,))
    print(f"ingestion.create registered + enabled; mcp_backends -> {facade_url}", file=sys.stderr)
    return tid


def register_chart_dashboard_tool(tenant_id: str) -> str:
    """Idempotently register the ``chart.dashboard.create`` write-proposal tool
    in tool-plane and point it at chart-service's real MCP backend facade
    (POST /internal/v1/mcp/invoke), so an approved dashboard-designer proposal
    is federated to a real dashboard+charts create instead of stopping at the
    gateway. Mirrors register_ingestion_tool() (which mirrors
    register_inference_tool(), which mirrors deploy/e2e/driver.py's
    register_apply_tool(), the case-service recipe) exactly: register tool ->
    register+publish version -> per-tenant enable (under a TENANT-scoped
    token, not the nil-tenant superadmin) -> upsert the mcp_backends row
    (platform-scoped, RLS via app.role='platform'). Safe to call on every
    boot: registry POSTs are idempotent (id/version conflicts no-op or
    reuse), the tenant-enable PUT is a pure upsert, and the mcp_backends
    INSERT carries ON CONFLICT DO UPDATE."""
    import psycopg

    su = c.superadmin_token()
    tid = "chart.dashboard.create"
    ver = "1.0.0"
    chart_url = os.environ.get("CHART_URL", "http://localhost:8320")

    def _post(url, token, body):
        return requests.post(url, json=body,
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"}, timeout=15)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools", su,
             {"tool_id": tid, "display_name": "Create a dashboard",
              "owner_service": "chart-service", "owner_team": "insights",
              "enabled_by_default": True, "side_effects": "reversible",
              "tags": ["chart", "dashboard", "insights"]})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register chart.dashboard.create tool: {r.status_code} {r.text[:150]}", file=sys.stderr)

    # input_schema mirrors the dashboard-designer agent's WriteIntent.args
    # exactly (services/agent-runtime/app/graphs/dashboard_designer.py
    # `propose()` / `_normalise_spec()`).
    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions", su,
             {"version": ver,
              "semantic_description": "Create a dashboard with one or more charts "
              "grounded in the governed semantic layer (published measures and "
              "dimensions). Use when a human has approved a copilot proposal to "
              "build a new dashboard.",
              "input_schema": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "name": {"type": "string"},
                                   "module": {"type": "string",
                                       "enum": ["insights", "case_management",
                                                "inspector"]},
                                   "description": {"type": "string"},
                                   "charts": {
                                       "type": "array",
                                       "items": {
                                           "type": "object",
                                           "properties": {
                                               "name": {"type": "string"},
                                               "chart_type": {"type": "string"},
                                               "measures": {
                                                   "type": "array",
                                                   "items": {"type": "string"}},
                                               "dimensions": {
                                                   "type": "array",
                                                   "items": {"type": "string"}},
                                               "filters": {
                                                   "type": "array",
                                                   "items": {"type": "string"}}},
                                           "required": ["name", "chart_type"]}},
                                   "workspace_id": {"type": "string"}},
                               "required": ["name", "module", "charts", "workspace_id"]},
              "output_schema": {"type": "object", "additionalProperties": True},
              "permission_tier": "write-proposal", "cost_weight": 1,
              "declared_sla": {"p95_ms": 10000}, "side_effects": "reversible",
              "examples": []})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register chart.dashboard.create version: {r.status_code} {r.text[:150]}", file=sys.stderr)

    # Deprecate any other currently-published version so 1.0.0 resolves (the
    # registry allows a single published version; the tool_plane DB persists
    # across `make up` runs).
    try:
        with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane") as cn:
            pubs = [row[0] for row in cn.execute(
                "SELECT version FROM tool_versions WHERE tool_id=%s AND status='published' "
                "AND version<>%s", (tid, ver)).fetchall()]
        for vv in pubs:
            requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{vv}/deprecate",
                          headers={"Authorization": f"Bearer {su}"}, timeout=15)
    except Exception as e:
        print(f"deprecate prior published chart.dashboard.create versions: {e}", file=sys.stderr)

    pubr = requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{ver}/publish",
                         headers={"Authorization": f"Bearer {su}"}, timeout=20)
    if pubr.status_code not in (200, 201) and "only draft" not in pubr.text:
        print(f"publish chart.dashboard.create {ver}: {pubr.status_code} {pubr.text[:150]}",
             file=sys.stderr)

    # Per-tenant enablement MUST be under a token whose tenant == the caller
    # tenant (self == token tenant) -- the nil-tenant superadmin cannot see the
    # tool as enabled for TENANT.
    tenant_tok = c.service_token("svc:seed", tenant_id, ["*"])
    requests.put(f"{c.TOOL_REGISTRY}/api/v1/tenants/self/tools/{tid}",
                headers={"Authorization": f"Bearer {tenant_tok}",
                         "Content-Type": "application/json"},
                json={"enabled": True}, timeout=15)

    # Register chart-service as the MCP backend for this tool (platform-
    # scoped row, tenant 0…0, resolved by owner_service=chart-service) so the
    # gateway federates the approved write to the real facade.
    facade_url = f"{chart_url}/internal/v1/mcp/invoke"
    with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane",
                         autocommit=True) as cn:
        cn.execute("SELECT set_config('app.role','platform', false)")
        cn.execute(
            """INSERT INTO mcp_backends (name, tenant_id, internal_url, spiffe_id, kind, status)
               VALUES ('chart-service','00000000-0000-0000-0000-000000000000',%s,
                       'spiffe://windrose/ns/tools/sa/mcp-gateway','internal','active')
               ON CONFLICT (name) DO UPDATE SET internal_url=EXCLUDED.internal_url,
                   spiffe_id=EXCLUDED.spiffe_id, status='active'""",
            (facade_url,))
    print(f"chart.dashboard.create registered + enabled; mcp_backends -> {facade_url}", file=sys.stderr)
    return tid


def register_entity_merge_tool(tenant_id: str) -> str:
    """BRD 56 inc2: idempotently register the ``dataset.entity.merge`` write-
    proposal tool and point tool-plane's mcp_backends at dataset-service's real
    MCP backend facade (POST /internal/v1/mcp/invoke), so an approved steward
    merge proposal is federated to a real confirm-merge instead of stopping at
    the gateway. Mirrors register_chart_dashboard_tool() exactly."""
    import psycopg

    su = c.superadmin_token()
    tid = "dataset.entity.merge"
    ver = "1.0.0"
    dataset_url = os.environ.get("DATASET_URL", "http://localhost:8304")

    def _post(url, token, body):
        return requests.post(url, json=body,
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"}, timeout=15)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools", su,
             {"tool_id": tid, "display_name": "Confirm entity merge",
              "owner_service": "dataset-service", "owner_team": "data",
              "enabled_by_default": True, "side_effects": "reversible",
              "tags": ["dataset", "entity-resolution", "merge"]})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register dataset.entity.merge tool: {r.status_code} {r.text[:150]}", file=sys.stderr)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions", su,
             {"version": ver,
              "semantic_description": "Confirm a below-auto entity-resolution merge "
              "candidate a steward reviewed. Use when a human has approved a proposal "
              "to merge two records into one resolved entity (link layer only; the "
              "source of record is never mutated).",
              "input_schema": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "candidate_id": {"type": "string"},
                                   "dataset_id": {"type": "string"},
                                   "run_id": {"type": "string"},
                                   "left_pk": {"type": "string"},
                                   "right_pk": {"type": "string"},
                                   "approve": {"type": "boolean"},
                                   "workspace_id": {"type": "string"}},
                               "required": ["candidate_id", "dataset_id"]},
              "output_schema": {"type": "object", "additionalProperties": True},
              "permission_tier": "write-proposal", "cost_weight": 1,
              "declared_sla": {"p95_ms": 8000}, "side_effects": "reversible",
              "examples": []})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register dataset.entity.merge version: {r.status_code} {r.text[:150]}", file=sys.stderr)

    try:
        with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane") as cn:
            pubs = [row[0] for row in cn.execute(
                "SELECT version FROM tool_versions WHERE tool_id=%s AND status='published' "
                "AND version<>%s", (tid, ver)).fetchall()]
        for vv in pubs:
            requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{vv}/deprecate",
                          headers={"Authorization": f"Bearer {su}"}, timeout=15)
    except Exception as e:
        print(f"deprecate prior published dataset.entity.merge versions: {e}", file=sys.stderr)

    pubr = requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tid}/versions/{ver}/publish",
                         headers={"Authorization": f"Bearer {su}"}, timeout=20)
    if pubr.status_code not in (200, 201) and "only draft" not in pubr.text:
        print(f"publish dataset.entity.merge {ver}: {pubr.status_code} {pubr.text[:150]}",
             file=sys.stderr)

    tenant_tok = c.service_token("svc:seed", tenant_id, ["*"])
    requests.put(f"{c.TOOL_REGISTRY}/api/v1/tenants/self/tools/{tid}",
                headers={"Authorization": f"Bearer {tenant_tok}",
                         "Content-Type": "application/json"},
                json={"enabled": True}, timeout=15)

    # Register dataset-service as the MCP backend for this tool. NOTE: mcp_backends
    # is keyed by `name` (=owner_service); dataset-service already hosts the same
    # /internal/v1/mcp/invoke facade, so this row serves the merge tool.
    facade_url = f"{dataset_url}/internal/v1/mcp/invoke"
    with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane",
                         autocommit=True) as cn:
        cn.execute("SELECT set_config('app.role','platform', false)")
        cn.execute(
            """INSERT INTO mcp_backends (name, tenant_id, internal_url, spiffe_id, kind, status)
               VALUES ('dataset-service','00000000-0000-0000-0000-000000000000',%s,
                       'spiffe://windrose/ns/tools/sa/mcp-gateway','internal','active')
               ON CONFLICT (name) DO UPDATE SET internal_url=EXCLUDED.internal_url,
                   spiffe_id=EXCLUDED.spiffe_id, status='active'""",
            (facade_url,))
    print(f"dataset.entity.merge registered + enabled; mcp_backends -> {facade_url}", file=sys.stderr)
    return tid


def seed_evalkey(tenant_id: str) -> str:
    """Mint a tenant-scoped virtual key that ALLOWS the ``judge`` request class so
    eval-service's LLM-judge calls (x-windrose-request-class: judge) pass
    ai-gateway's per-key class check (a chat/embed-only key is rejected 403). The
    deployment the judge routes to (fast-small -> qwen2.5:0.5b) is the one `aigw`
    already seeded for this tenant. Seeded directly in ai-gateway's Postgres, same
    as the agent key. key_hash = sha256_hex("nk-<token>")."""
    import datetime as _dt
    import hashlib
    import secrets
    import uuid as _uuid

    import psycopg

    now = _dt.datetime.now(_dt.timezone.utc)
    vkey = f"nk-{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(vkey.encode()).hexdigest()
    dsn = "postgresql://windrose:windrose_dev@localhost:5432/ai_gateway"
    with psycopg.connect(dsn, autocommit=True) as conn:
        # The judge ladder's rungs are the 'balanced'/'frontier' model aliases
        # (DEFAULT_LADDERS["judge"]), NOT 'fast-small' which aigw seeded for chat.
        # Without a deployment serving the judge ladder, ai-gateway 503s
        # "no active deployment serves any rung of the judge ladder". Seed a
        # 'balanced' (judge rung-0) deployment pointing at the same local Ollama
        # model so judge calls actually complete on local infra.
        conn.execute(
            """INSERT INTO provider_deployments
               (id, tenant_id, provider, model_family, deployment_name, region, cloud,
                endpoint_vault_ref, tpm_limit, rpm_limit, priority, status, created_at, updated_at)
               VALUES (%s,%s,'bedrock','balanced','qwen2.5:0.5b','local','aws',
                       '', 1000000, 6000, 1, 'active', %s, %s)
               ON CONFLICT DO NOTHING""",
            (str(_uuid.uuid4()), tenant_id, now, now))
        conn.execute(
            """INSERT INTO virtual_keys
               (id, tenant_id, key_hash, principal_type, principal_id,
                allowed_request_classes, max_rung, status, created_at, updated_at)
               VALUES (%s,%s,%s,'service','eval-service', %s, 3, 'active', %s, %s)
               ON CONFLICT (key_hash) DO NOTHING""",
            (str(_uuid.uuid4()), tenant_id, key_hash, ["judge", "chat", "embed"], now, now))
    print(vkey)
    return vkey


def _register_tool(tenant_id: str, *, tool_id: str, version: str, display: str,
                   owner_service: str, backend_url: str, semantic_description: str,
                   input_schema: dict, tags: list[str]) -> str:
    """Shared idempotent register->publish->tenant-enable->backend recipe
    (the register_inference_tool pattern, §TPL-FR-012), parameterized so the
    ML-lifecycle tools don't copy 100 lines each."""
    import psycopg

    su = c.superadmin_token()

    def _post(url, token, body):
        return requests.post(url, json=body,
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"}, timeout=15)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools", su,
             {"tool_id": tool_id, "display_name": display,
              "owner_service": owner_service, "owner_team": "ml-platform",
              "enabled_by_default": True, "side_effects": "reversible", "tags": tags})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register {tool_id} tool: {r.status_code} {r.text[:150]}", file=sys.stderr)

    r = _post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tool_id}/versions", su,
             {"version": version, "semantic_description": semantic_description,
              "input_schema": input_schema,
              "output_schema": {"type": "object", "additionalProperties": True},
              "permission_tier": "write-proposal", "cost_weight": 2,
              "declared_sla": {"p95_ms": 10000}, "side_effects": "reversible",
              "examples": []})
    if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
        print(f"register {tool_id} version: {r.status_code} {r.text[:150]}", file=sys.stderr)

    try:
        with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane") as cn:
            pubs = [row[0] for row in cn.execute(
                "SELECT version FROM tool_versions WHERE tool_id=%s AND status='published' "
                "AND version<>%s", (tool_id, version)).fetchall()]
        for vv in pubs:
            requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tool_id}/versions/{vv}/deprecate",
                          headers={"Authorization": f"Bearer {su}"}, timeout=15)
    except Exception as e:
        print(f"deprecate prior published {tool_id} versions: {e}", file=sys.stderr)

    pubr = requests.post(f"{c.TOOL_REGISTRY}/api/v1/tools/{tool_id}/versions/{version}/publish",
                         headers={"Authorization": f"Bearer {su}"}, timeout=20)
    if pubr.status_code not in (200, 201) and "only draft" not in pubr.text:
        print(f"publish {tool_id} {version}: {pubr.status_code} {pubr.text[:150]}", file=sys.stderr)

    tenant_tok = c.service_token("svc:seed", tenant_id, ["*"])
    requests.put(f"{c.TOOL_REGISTRY}/api/v1/tenants/self/tools/{tool_id}",
                headers={"Authorization": f"Bearer {tenant_tok}",
                         "Content-Type": "application/json"},
                json={"enabled": True}, timeout=15)

    with psycopg.connect("postgresql://windrose:windrose_dev@localhost:5432/tool_plane",
                         autocommit=True) as cn:
        cn.execute("SELECT set_config('app.role','platform', false)")
        cn.execute(
            """INSERT INTO mcp_backends (name, tenant_id, internal_url, spiffe_id, kind, status)
               VALUES (%s,'00000000-0000-0000-0000-000000000000',%s,
                       'spiffe://windrose/ns/tools/sa/mcp-gateway','internal','active')
               ON CONFLICT (name) DO UPDATE SET internal_url=EXCLUDED.internal_url,
                   spiffe_id=EXCLUDED.spiffe_id, status='active'""",
            (owner_service, backend_url))
    print(f"{tool_id} registered + enabled; mcp_backends[{owner_service}] -> {backend_url}",
          file=sys.stderr)
    return tool_id


def register_ml_lifecycle_tools(tenant_id: str) -> list[str]:
    """BRD 52 (ml-engineer agent): register the two write-proposal tools the
    autonomous train->evaluate->propose loop needs. Closes two pre-existing
    gaps: pipeline.template.create_from_algorithm was referenced by the
    model-training agent but never registered; experiment.model.promote had a
    facade but no reachable backend route until experiment-service grew
    /internal/v1/mcp/invoke."""
    pipeline_url = os.environ.get("PIPELINE_URL", c.PIPELINE)
    experiment_url = os.environ.get("EXPERIMENT_URL", c.EXPERIMENT)
    tools = []
    tools.append(_register_tool(
        tenant_id,
        tool_id="pipeline.template.create_from_algorithm", version="1.0.0",
        display="Create + launch a training pipeline from an algorithm template",
        owner_service="pipeline-orchestrator",
        backend_url=f"{pipeline_url}/internal/v1/mcp/invoke",
        semantic_description=(
            "Instantiate and launch a training pipeline run from a catalog "
            "algorithm template against a governed dataset. Use when a human "
            "has approved (or tenant policy auto-approves) an agent plan to "
            "train a model candidate."),
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {
                          "algorithm": {"type": "string"},
                          "mode": {"type": "string"},
                          "dataset_refs": {"type": "object",
                                           "additionalProperties": {"type": "string"}},
                          "params": {"type": "object", "additionalProperties": True},
                          "workspace_id": {"type": "string"},
                          "name": {"type": "string"}},
                      "required": ["algorithm", "dataset_refs"]},
        tags=["pipeline", "training", "ml-engineer"]))
    tools.append(_register_tool(
        tenant_id,
        tool_id="experiment.model.promote", version="1.0.0",
        display="Request a model promotion (four-eyes)",
        owner_service="experiment-service",
        backend_url=f"{experiment_url}/internal/v1/mcp/invoke",
        semantic_description=(
            "Create a PENDING promotion request for a registered model version "
            "toward a target lifecycle stage; a second human must decide it "
            "(four-eyes). Use when an agent's evaluated candidate is worth "
            "promoting and a human approved the proposal."),
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {
                          "model_id": {"type": "string"},
                          "version": {"type": "integer"},
                          "model_version_urn": {
                              "type": "string",
                              "x-windrose-urn":
                                  "wr:{tenant}:experiment:model_version/{value}",
                              # A model version is ROLE-governed (the deciding
                              # human's experiment.model.update capability, checked
                              # at experiment-service's facade), not per-user ABAC-
                              # assigned like a case. Opt out of tool-plane's per-
                              # resource obo-grant intersection (which would demand
                              # a perm:{tenant}:{user}:res:* grant that is never
                              # minted for model versions -> deny-forever) while
                              # keeping the cross-tenant URN guard.
                              "x-windrose-urn-obo": False},
                          "target_stage": {"type": "string"},
                          "rationale": {"type": "string"},
                          "workspace_id": {"type": "string"}},
                      "required": ["model_id", "version", "target_stage"]},
        tags=["experiment", "promotion", "ml-engineer"]))
    return tools


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "tenant":
        provision_tenant()
    elif cmd == "aigw":
        seed_aigw(sys.argv[2])
    elif cmd == "evalkey":
        seed_evalkey(sys.argv[2])
    elif cmd == "inference_tool":
        register_inference_tool(sys.argv[2])
    elif cmd == "ingestion_tool":
        register_ingestion_tool(sys.argv[2])
    elif cmd == "chart_dashboard_tool":
        register_chart_dashboard_tool(sys.argv[2])
    elif cmd == "entity_merge_tool":
        register_entity_merge_tool(sys.argv[2])
    elif cmd == "ml_lifecycle_tools":
        register_ml_lifecycle_tools(sys.argv[2])
    else:
        print("usage: seed.py {tenant|aigw <tenant_id>|evalkey <tenant_id>|"
             "inference_tool <tenant_id>|ingestion_tool <tenant_id>|"
             "chart_dashboard_tool <tenant_id>|entity_merge_tool <tenant_id>|"
             "ml_lifecycle_tools <tenant_id>}",
             file=sys.stderr)
        sys.exit(2)
