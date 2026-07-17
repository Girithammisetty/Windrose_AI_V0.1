# tool-plane (tool-registry + mcp-gateway)

Two Go deployables sharing one DB / bounded context (BRD 13):

- **tool-registry** (`cmd/registry`) — the governed tool **catalog + admin**: registration, semver lifecycle, per-tenant enablement, kill switches, BYO onboarding, and **real semantic discovery** (Ollama `nomic-embed-text` embeddings stored in **pgvector**).
- **mcp-gateway** (`cmd/gateway`) — the **/mcp** data plane hosting/federating backend MCP facades behind the per-call **enforcement pipeline**: authN → kill/enablement → **real OPA** → **real Redis** rate limit → JSON-Schema validation → tier gate → **real HTTP** backend invoke → **real Kafka** audit (`ai.tool_invoked.v1`).

One Go module, two `cmd/` entrypoints over shared `internal/`.

```
services/tool-plane/
  cmd/{registry,gateway}/          two deployables
  internal/
    domain/     tool/version/kill/byo model, JSON-Schema validation, URN + args-digest, semver
    store/      pgx + Postgres RLS; pgvector discovery; outbox
    embed/      REAL Ollama nomic-embed-text client (768-dim) + cosine
    authz/      REAL OPA sidecar client (tool.invoke input) + policy upload
    enforce/    the ordered pipeline: kill (Redis pub/sub), rate limit (Redis), grants (Redis), OPA, audit
    mcp/        pinned MCP spec + REAL HTTP backend federation client
    api/        tool-registry REST + mcp-gateway JSON-RPC (/mcp)
    events/     master envelope, outbox relay, REAL go-common Kafka publisher
  migrations/   forward-only SQL (+ RLS)
  policy/       tool_plane.rego (embedded; uploaded to OPA in dev, bundle-mounted in prod)
  api/openapi.yaml   events/*.avsc   Makefile   Dockerfile.{registry,gateway}
```

## Run

Dev stack: `deploy/docker-compose.dev.yml` (Postgres, Redis, Redpanda, OPA) **+ Ollama** with `nomic-embed-text`.
The runtime DB must have **pgvector** — point `DATABASE_URL` at a `pgvector/pgvector:pg16` (or extension-enabled) Postgres.

```
make build                 # bin/tool-registry, bin/mcp-gateway
make run-registry          # :8090
make run-gateway           # :8091  (uploads policy/tool_plane.rego into the OPA sidecar in dev)
```

Key env: `DATABASE_URL`, `REDIS_ADDR`, `OPA_URL`, `OLLAMA_URL` (default `http://localhost:11434/v1`), `KAFKA_BROKERS`, `SCHEMA_REGISTRY_URL`, `JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`.

## Test

```
make test-unit         # no external deps (in-memory doubles inside *_test.go only)
make test-integration  # Testcontainers pgvector + running Redis/Redpanda/OPA/Ollama; auto-skips if any is down
make test-race         # -race on the enforcement pipeline
```

## Adapter inventory (every adapter is real; no runtime stubs — CONVENTIONS END STATE)

| Capability | Real adapter | Where |
|---|---|---|
| OLTP + tenant isolation | PostgreSQL + **RLS** (non-superuser role in tests) | `internal/store` (pgx), `migrations/000002_rls` |
| Semantic embeddings | **Ollama `nomic-embed-text`** (768-dim), real `/v1/embeddings` | `internal/embed/ollama.go` |
| Vector search | **pgvector** ivfflat cosine | `store/catalog.go` `SearchByEmbedding` |
| Authorization | **OPA sidecar** (`windrose/tool_plane/decision`) | `internal/authz/opa.go`, `policy/tool_plane.rego` |
| Rate limiting | **Redis** atomic token bucket (Lua) | `internal/enforce/ratelimit.go` |
| Kill switch | **Redis** set + pub/sub fan-out, Postgres-durable | `internal/enforce/killswitch.go` |
| OBO grant intersection | **Redis** rbac `permissions_flat` projection | `internal/enforce/grants.go` |
| Backend MCP facades | **real `net/http`** client, SLA-derived timeouts | `internal/mcp/backend.go` |
| Event bus | **Redpanda/Kafka** via go-common producer + outbox | `internal/events`, go-common |
| AuthN | **RS256 JWT** via go-common `authjwt` (JWKS/static) | go-common |

The only fakes are in-memory doubles inside `*_test.go` (unit tier) — never reachable from `cmd/`.

## FR coverage (BRD 13 §3)

| FR | Status | Code / test |
|---|---|---|
| TPL-FR-001 tool record + versioned semver | ✅ | `domain/types.go`, `store/catalog.go`, `api/handlers_tools.go` |
| TPL-FR-002 lifecycle draft→published→deprecated→retired | ✅ | `handlePublish/Deprecate/Retire`, `TestAC7/AC8` |
| TPL-FR-003 registration paths + diff + idempotent | ✅ | `handleRegisterTool`, `handleDiff`, BR-14 SPIFFE `TestAC15` |
| TPL-FR-004 per-tenant enablement matrix | ✅ | `store/tenant.go`, `handleEnablement` |
| TPL-FR-005 write-direct gating / BR-2 destructive | ✅ | `handleAddVersion`, `handleEnablement` |
| TPL-FR-010 single /mcp, pinned spec, stateless | ✅ | `api/gateway.go`, `mcp.SpecVersion` |
| TPL-FR-011 caller-scoped tools/list | ✅ | `handleToolsList`, `TestAC14` |
| TPL-FR-012 backend routing + SLA timeouts | ✅ | `catalogAdapter.BackendFor`, `mcp/backend.go` |
| TPL-FR-013 BYO egress/response caps | ✅ (allowlist+1MB) | `mcp/backend.go`, `store` mcp_backends |
| TPL-FR-020 semantic discovery | ✅ | `handleDiscovery`, `SearchByEmbedding`, `TestAC6` |
| TPL-FR-021 re-embed on publish, model ver stored | ✅ | `handlePublish`, `embedding_model_ver` |
| TPL-FR-022 schema fast-path | ✅ | `handleGetSchema` |
| TPL-FR-030..034 authN/kill/OPA/rate/schema gates | ✅ | `enforce/pipeline.go`, `pipeline_test.go`, AC-1/2/3/11/12/13 |
| TPL-FR-035 write tier + **signed** proposal-execution grant | ✅ | `authz/proposal.go`, `pipeline.go`, `TestSEC_*`, `TestAC4` |
| TPL-FR-036..038 invoke + output validation + audit | ✅ | `pipeline.go`, `store/tenant.go RecordInvocation` |
| TPL-FR-040..042 BYO onboarding + approval | ✅ | `handleBYO*`, `TestAC9` |
| TPL-FR-050 rolling health (Redis, p50/p95/p99) | ✅ | `enforce/health.go`, `handleHealth`, `TestAC10` |
| TPL-FR-051 SLA-breach detect + auto-quarantine | ✅ | `enforce/health.go Quarantiner`, `runSLASweep`, `TestAC10` |
| TPL-FR-052..053 kill switch (Redis pub/sub) + audited | ✅ | `killswitch.go`, `handleCreateKill`, `TestAC5` |
| TPL-FR-060 catalog/admin APIs | ✅ | `api/*` |

## Acceptance criteria → tests

| AC | Test |
|---|---|
| AC-1 allowed read → backend + `ai.tool_invoked{allowed}` | `TestAC1_AllowedRead` |
| AC-2 missing OBO grant → PERMISSION_DENIED (real OPA) | `TestAC2_MissingGrantDenied` |
| AC-3 argument-constraint deny records constraint id | `TestAC3_ArgumentConstraintDenied` |
| AC-4 PROPOSAL_REQUIRED then **signed** proposal execution invokes | `TestAC4_ProposalRequiredThenExecute` |
| SEC forged/unsigned grant rejected | `TestSEC_ForgedGrantRejected`, `TestPipeline_ForgedProposalGrant_Rejected` |
| SEC expired/args-mismatch/wrong-tool/wrong-tenant/wrong-issuer grant rejected | `TestSEC_InvalidSignedGrantsRejected` |
| SEC no verifier → fail closed | `TestPipeline_NoVerifier_FailsClosed` |
| SEC client `_meta` cannot widen toolset | `TestSEC_ToolsetNotWidenableByMeta` |
| AC-5 multi-replica kill switch (Redis pub/sub ≤5s) | `TestAC5_KillSwitch` |
| AC-6 semantic discovery (real nomic-embed-text + pgvector) | `TestAC6_SemanticDiscovery` |
| AC-7 publish schema gate + embedding populated | `TestAC7_PublishGate` |
| AC-8 deprecated version still serves with warning | `TestAC8_DeprecationWarning`; retired → `TestPipeline_Retired` (unit) |
| AC-9 BYO callability lifecycle + isolation | `TestAC9_BYOCallabilityLifecycle` |
| AC-10 SLA breach → timeout health + auto-quarantine → TOOL_KILLED | `TestAC10_SLABreachAutoQuarantine` |
| AC-11 rate limit — real Redis **token bucket** | `TestAC11_RateLimited` |
| AC-12 OPA down → POLICY_UNAVAILABLE (fail closed) | `TestPipeline_PolicyUnavailable` (unit) |
| AC-13 cross-tenant URN → 404-shaped | `TestAC13_CrossTenantDenied` |
| AC-14 caller-scoped tools/list (token-authoritative) | `TestAC14_ToolsListScoped` |
| AC-15 manifest SPIFFE/owner mismatch → 403 | `TestAC15_ManifestIdentityMismatch` |
| AC-16 statelessness — second replica serves same session | `TestAC16_Statelessness` |
| AC-17 eval-mode write tool stubbed (claim-verified) | `TestPipeline_EvalStub` (unit) |
| audit round-trip through REAL Kafka | `TestKafkaRoundTrip_ToolInvoked` |
| RLS isolation (non-superuser role) | `TestRLSIsolation_NonSuperuser` |

## Documented exceptions

- **AC-12** runs as a unit-tier test (`TestPipeline_PolicyUnavailable`): stopping the *shared* dev OPA is disruptive, so fail-closed is proven by pointing the client at an unreachable endpoint and asserting the deny branch directly.
- **SPIFFE mTLS** peer identity is asserted via the `X-Spiffe-Id` header (BR-14 binding + backend attribution); real mesh mTLS is an infra-layer concern (SPIRE), not application code.
- **Vault** BYO secret injection (TPL-FR-013/BR-8): the model + egress allowlist + redaction contract are in place; wiring to a live Vault is credential-gated like the master BRD's honest-ceiling adapters.
