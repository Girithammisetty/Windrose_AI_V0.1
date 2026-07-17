# ai-gateway

The single choke point for **every** LLM/embedding call on the Windrose
platform: OpenAI-compatible proxy with per-cloud affinity routing, model
ladders, hierarchical hard budgets, virtual keys, tenant-scoped semantic
cache, gateway-tier guardrails, token metering, and SSE streaming.

Spec: `docs/brd/12_ai_gateway_BRD.md` inheriting `docs/brd/00_MASTER_BRD.md`.
Layout per `Windrose-ai/CONVENTIONS.md` (Python service, wave-1 self-contained).

**Architecture decision.** The gateway is built as a FastAPI application with
an in-process enforcement pipeline (`app/domain/pipeline.py`, the normative §7
stage order) over a pluggable provider-adapter port. The **runtime provider is
real**: `OllamaProvider` (`app/adapters/providers.py`) makes genuine inference
calls to a local Ollama server over its OpenAI-compatible API
(`http://localhost:11434/v1`) for chat, completions, streaming and embeddings —
`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings` all produce real
model output (e.g. `qwen2.5:0.5b` for chat, `nomic-embed-text` for embeddings).
`InProcessProvider` is a deterministic in-process test double reachable only
from the unit tier and dev. Cloud LLM providers (Azure OpenAI / Bedrock /
Vertex / Anthropic API via Vault credentials) are the credential-gated "honest
ceiling" per `CONVENTIONS.md` and are not needed for local end-to-end
verification. Keeping the provider behind the port lets every enforcement stage
(budgets, guardrails, cache, routing) stay first-party, testable, and
provider-independent while still wrapping a real model.

## Run

```bash
export PATH="/opt/homebrew/bin:$PATH"   # uv
make install          # uv sync (Python 3.12)
make test-unit        # unit tier — no external dependencies (120 tests)
make test-integration # Testcontainers Postgres(pgvector) + Redis; auto-skips if Docker is down
make test             # both tiers
make lint             # ruff
make run              # uvicorn on :8092 (memory-mode container by default)
make migrate          # alembic upgrade head (AIG_DATABASE_URL / alembic.ini)
```

Database bootstrap: migrations create the non-privileged `ai_gateway_app` role
and enable RLS on every tenant table. Create the runtime login per environment
with `CREATE USER <user> LOGIN PASSWORD '…' IN ROLE ai_gateway_app` and point
`AIG_DATABASE_URL` (asyncpg) at it — RLS only binds to non-superusers.
Postgres needs the `pgvector` extension (created by migration 0001).

Settings via `AIG_*` env vars (`app/config.py`): JWT PEM/JWKS, Redis URL,
default budgets, price version, admission caps, cache thresholds, timeouts.

## Architecture

```
app/
  api/        data plane (/v1/chat/completions, /v1/completions, /v1/embeddings),
              admin plane (/api/v1/admin/*), dual auth middleware (virtual key +
              X-Windrose-JWT), error envelope, Idempotency-Key
  domain/     pipeline (enforcement stages, §7 order), budgets (stacked windows,
              reserve/settle, thresholds), ladders (resolve/escalate/degrade),
              routing (cloud affinity, circuit breaker, failover plan, health),
              guardrails (PII redact ∥ injection classify, schema validation),
              cache (exact + semantic tiers), keys, admission, pricing, windows,
              reconciliation (drift + anomaly), providers_admin, ports
  store/      memory (unit tier, tenant-policy fake) + sql (SQLAlchemy 2 async,
              RLS-bound UoW, keyauth/worker GUC policies, outbox dispatcher)
  events/     envelope, in-memory bus + dedup, identity/usage consumers
  adapters/   providers (InProcessProvider + LiteLLM stub), guardrail models
              (regex PII + heuristic injection; Presidio/ML stubs), embeddings
              (hash embedder + ladder-embedder stub), kv (memory/Redis),
              ledger (memory/Redis/Postgres/fallback chain)
migrations/   forward-only alembic (0001: schema + RLS + pgvector + grants)
api/openapi.yaml, events/*.avsc (ai.token_usage.v1 owned here)
```

### Enforcement pipeline (normative order, BRD §7)

authN (key + JWT) → attribution → admission (streams/RPM/TPM) → guardrails-in
(PII ∥ injection) → semantic cache lookup → budget pre-flight (stacked
reserve, top-down) → ladder resolve (class → rung → cloud-affinity deployment)
→ provider call (retry/failover, ≤3 attempts / ≤2 providers) → guardrails-out
(schema validation → retry → escalate; de-redaction) → cache write → budget
settle (+ threshold events, exactly-once) → metering event → response. The
rejecting stage is stamped on the span as `windrose.rejected_stage`.

## Adapter / stub inventory

| Port | Dev/test implementation (real behavior) | Production adapter (stub, `NotImplementedError` + TODO) |
|---|---|---|
| `ProviderClient` | `InProcessProvider` — deterministic echo + scriptable outcomes, streaming, token billing | `LiteLLMProvider` (Azure OpenAI / Bedrock / Vertex / Anthropic API, Vault creds) |
| `PIIAnalyzer` | `RegexPIIAnalyzer` — EMAIL/PHONE/CREDIT_CARD(Luhn)/SSN/IBAN | `PresidioPIIAnalyzer` (adds PERSON via NER) |
| `InjectionClassifier` | `HeuristicInjectionClassifier` — deterministic pattern scorer | `MLInjectionClassifier` |
| `Embedder` | `HashEmbedder` — deterministic bag-of-words (semantic tier stays real) | `LadderEmbedder` (embeds via the gateway's own embed ladder) |
| `LedgerStore` | `InMemoryLedger` (unit) / `RedisLedger` + `PgLedger` + `FallbackLedger` (integration & prod path) | — (the prod chain is implemented; BR-14 fail-closed proven in tests) |
| `KV` | `InMemoryKV` / `RedisKV` | — |
| `InvalidationChannel` | `InMemoryInvalidationChannel` / `RedisInvalidationChannel` (`keyrev` pub/sub) | — |
| Event bus | `InMemoryEventBus` | `KafkaEventBus` (aiokafka, Avro, DLQ) |
| Consumer dedup | `InMemoryDedupStore` / `SqlDedupStore` | `RedisDedupStore` (implemented, SETNX 24h) |
| AuthZ | `LocalScopeAuthz` (JWT scopes) | `OpaAuthzClient` (sidecar, MASTER-FR-012) |
| Tracing | in-memory `Tracer` implementing the §3 span-attribute contract | OTel SDK exporter + Langfuse forwarding (MASTER-FR-052) |

Other deliberate deviations (documented, non-blocking):

- **Monthly partitioning** of `budget_spend` / `request_log` deferred (TODO in
  migration): the partition key would join every unique constraint; retention
  jobs enforce the 24-month / 90-day windows.
- **Ledger tables** (`budget_spend`, `budget_reservations`,
  `budget_threshold_flags`) carry no `tenant_id`: they are keyed by
  `budget_ref` (already tenant-scoped through `budgets`) and gated by the
  worker GUC policy only.
- Health probes / outbox dispatch are exposed as invokable methods
  (`prober.probe_once()`, `outbox_dispatcher.run_once()`); the 60s scheduler
  loop is deployment wiring.
- Config export/import (AIG-FR-071, Could) not implemented.

## FR / AC traceability

| Requirement | Code | Tests |
|---|---|---|
| AIG-FR-001/002 (endpoints, dual auth, attribution) | `api/routes/data_plane.py`, `api/middleware.py` | `test_data_plane.py` (AC-1), `test_isolation_authz.py` |
| AIG-FR-003 provider registry + state machine | `domain/providers_admin.py` | `test_admin_api.py` |
| AIG-FR-004 cloud affinity | `domain/routing.py::Router.candidates` | `test_routing.py` (AC-10) |
| AIG-FR-005/006 ladders + escalation, LADDER_CAP | `domain/ladders.py` | `test_routing.py`, `test_admin_api.py` |
| AIG-FR-007 degradation | `domain/pipeline.py` (degrade scan), `ladders.select_rung` | `test_budgets.py` |
| AIG-FR-008 failover/retry | `domain/routing.py::AttemptPlan`, `pipeline._call_with_failover` | `test_routing.py` (AC-7), `test_streaming.py` |
| AIG-FR-009/009a/009b breaker, probes, routing trace | `domain/routing.py`, `providers_admin.HealthProber` | `test_routing.py` |
| AIG-FR-010 streaming + usage chunk | `pipeline._serve_stream` | `test_streaming.py` (AC-11) |
| AIG-FR-011 admission | `domain/admission.py` | `test_admission.py` (AC-15) |
| AIG-FR-020..023 stacked budgets, pre/post-flight, thresholds, fail-closed | `domain/budgets.py`, `adapters/ledger.py` | `test_budgets.py` (AC-2/3), `integration/test_ledger_and_cache.py` (AC-13) |
| AIG-FR-024/025 budget CRUD + anomaly | `api/routes/admin.py`, `domain/reconciliation.py` | `test_admin_api.py`, `test_reconciliation.py` |
| AIG-FR-030..032 virtual keys | `domain/keys.py` | `test_data_plane.py` (AC-9), `test_admin_api.py`, `integration` pub/sub |
| AIG-FR-040..043 semantic cache | `domain/cache.py` | `test_cache.py` (AC-6), `integration` pgvector |
| AIG-FR-050..054 guardrails | `domain/guardrails.py`, `adapters/guardrail_models.py` | `test_guardrails.py` (AC-4/5/8) |
| AIG-FR-060..062 metering, spans, metrics | `pipeline._record_and_meter`, `ports.Tracer/Metrics` | `test_data_plane.py`, `test_reconciliation.py` (AC-16) |
| AIG-FR-070 admin APIs | `api/routes/admin.py` | `test_admin_api.py`, `test_isolation_authz.py` (AC-12) |
| MASTER-FR-001..004 RLS + isolation | `migrations/0001`, `store/sql.py` | `integration/test_rls_isolation.py`, `test_isolation_authz.py` |
| MASTER-FR-025 idempotency | `api/idempotency.py` | `test_admin_api.py` |
| MASTER-FR-030..034 events + outbox | `events/`, `store/sql.OutboxDispatcher` | `test_events_consumer.py`, `integration` outbox |
| BR-1..BR-18 | see inline references in `domain/*` | pipeline/budget/cache/stream suites |

AC coverage: AC-1..AC-16 all have at least one test (AC-11's latency target is
functional-only here; the perf suite is a deployment concern; AC-13 has both a
unit fake variant and the real Redis/Postgres integration variant).
