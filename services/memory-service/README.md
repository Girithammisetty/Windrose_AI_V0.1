# memory-service

Governed store for everything agents remember and retrieve (BRD 15): **scoped
memories** (session → user → workspace → tenant) and **RAG corpora** (CDC-fed
collections of schemas, dashboards, resolved cases, docs). Owns the write path
(injection screening, PII policy, dedup/merge), retrieval (scope-filtered, **hard
tenant filter**, top-k + recency blend over real pgvector ANN), corpus
chunking/embedding pipelines, **right-to-erasure** cascades with verification
reports, retention/expiry/re-validation jobs, and browsing/admin APIs.

Python 3.12 · FastAPI · SQLAlchemy 2 async · pgvector · alembic · uv.

## Run

```bash
make install                    # uv sync
make test-unit                  # unit tier — no network, hash embeddings + in-memory doubles
make test-integration           # integration tier — Testcontainers pgvector + live infra
make test                       # both tiers
make lint                       # ruff
make run                        # uvicorn on :8087
```

Integration tier needs Docker (a `pgvector/pgvector:pg16` Testcontainer) plus the
dev compose stack for the live-infra tests; each such test **auto-skips with a
reason** when its dependency is down:

```bash
docker compose -f ../../deploy/docker-compose.dev.yml up -d postgres redis redpanda opa
ollama serve && ollama pull nomic-embed-text     # real 768-dim embeddings
```

## Architecture

- **Schema-per-tenant + RLS.** Tenant memory/chunk rows live in `mem_t_<tenant>`
  schemas created by the idempotent `mem_provision_tenant()` SQL primitive
  (BR-14). Control tables (`corpora`, `tenant_policies`, `erasure_requests`,
  `write_audit`, `outbox`, `processed_events`, `idempotency_keys`) live in
  `public` with RLS. Every request pins `search_path` to the tenant schema **and**
  sets `app.tenant_id` for RLS, and retrieval SQL carries an explicit `tenant_id`
  predicate — two independent isolation layers (MEM-FR-021).
- **Store-agnostic port** (`app/domain/ports.py::MemoryStore`) — the pgvector
  `SqlMemoryStore` (runtime) and the in-memory unit double implement the same
  interface, keeping the Qdrant scale-tier upgrade path open (MEM §8/§11).
- **Write pipeline** (normative order, `WriteService.write`): authz → injection
  screening (block→quarantine) → PII policy → embed → dedup search (sim ≥ 0.92)
  → merge|insert → cap eviction → persist + outbox → `memory.written`. On an
  embeddings outage the already-screened+PII-checked write is parked in the
  `mem:pend` queue (≤1h) and drained when the backend recovers — never persisted
  unembedded (BR-2 / AC-11).
- **Ranking**: `w_sim·cos + w_rec·recency_decay(half_life) + w_conf·conf`
  (0.65/0.20/0.15), quarantined/expired excluded (MEM-FR-022).

## Runtime wiring (the shipped binary is fully real)

`app.main:app` / `make run` / the Docker entrypoint build a **real** container by
default: `store_mode=sql` (Postgres + pgvector, schema-per-tenant + RLS) with all
cross-cutting adapters real (`use_real_adapters=True` is the runtime default). The
async engine + session factories are built from `settings.database_url` /
`admin_database_url` in `build_container` (lazily — no connection at import; the
store connects on first request) and disposed in the app lifespan. The in-memory
doubles are reachable **only** from tests (`tests/conftest.make_settings` sets
`use_real_adapters=False`). Set `MEM_USE_REAL_ADAPTERS=false` to run fully
self-contained. `/readyz` performs a real `SELECT 1` (and, with `?tenant=`, a
tenant-schema check) and returns 503 when the store is unreachable. AuthN defaults
to the identity-service JWKS URL; set `MEM_JWT_PUBLIC_KEY_PEM` for a static-key
dev/probe run.

## Adapter inventory (no stub in the runtime path — CONVENTIONS.md END STATE)

| Capability | Runtime adapter (real) | Unit-tier double (tests only) |
|---|---|---|
| Embeddings | `OpenAIEmbeddingClient` → Ollama `nomic-embed-text` (768-dim, real `/v1/embeddings`) | `LocalHashEmbedding` |
| Injection screening | `PatternInjectionScreener` (co-packaged deterministic classifier over the input) | `UnavailableScreener` (BR-1 outage path) |
| PII scan / anonymize | `RegexPiiScanner` + `RegexAnonymizer` (Presidio-equivalent, real) | same (deterministic) |
| Vector store / OLTP | `SqlMemoryStore` — Postgres 16 + pgvector, schema-per-tenant + RLS | `store/memory.py` in-memory |
| Session scope | `RedisSessionStore` — real Redis hash, TTL-managed | `InMemorySessionStore` |
| Event bus | `KafkaEventBus` — Redpanda idempotent producer (windrose_common) | `InMemoryEventBus` |
| Consumers | `KafkaMemoryConsumer` — real consumer groups, DLQ, retry (windrose_common) | in-process bus dispatch |
| Consumer dedup | `RedisDedupStore` (24h TTL) / durable `SqlDedupStore` | `InMemoryDedupStore` |
| AuthN | `TokenVerifier` — RS256 JWKS (`alg=none` rejected by construction) | static PEM |
| AuthZ | `OpaAuthzClient` — real OPA sidecar + Redis projection (windrose_common) | `LocalScopeAuthz` |
| Workspace membership | `RedisMembershipChecker` — rbac Redis projection (BR-10) | `InMemoryMembership` |
| Embedding-outage queue | `RedisPendingQueue` — `mem:pend` (BR-2, ≤1h) | `InMemoryPendingQueue` |
| Outbox relay | `store/sql.py::OutboxDispatcher` — poll + publish (MASTER-FR-034) | — |
| Erasure workflow | in-process idempotent orchestrator running the real activities (Temporal drives it in prod; the activities hit real stores) | same activities |

The **one documented substitution**: the erasure *workflow engine* is an
in-process orchestrator here (Temporal is the deployment driver). Every activity
is real and idempotent and hits real Postgres/Redis — verified end-to-end in
`test_ac7_erasure_cascade_end_to_end`. No `NotImplementedError`/fake adapter is
reachable from `app.main` when `MEM_USE_REAL_ADAPTERS=true`.

## FR coverage

**Must (implemented):** MEM-FR-001,002,003,004 · 010,011,012 · 020,021,022,023 ·
030,031,032,033 · 040,041,042 · 050,051. **Should (implemented):** MEM-FR-013,
024, 034, 052. **Business rules:** BR-1..BR-17 honored in the write/retrieve/
retention/erasure paths (see traceability). Deferred (documented): the Qdrant
scale tier (adapter interface present, MEM §11); semantic contradiction
resolution beyond BR-6 (Future).

## AC → test traceability

| AC | Behaviour | Test |
|---|---|---|
| AC-1 | write embeds, persists in `mem_t_<tenant>`, retrievable, emits `memory.written` | `unit/test_write_pipeline.py::test_ac1_*`, `integration/test_real_adapters.py::test_ac1_ac4_*` (real vector + pgvector ANN) |
| AC-2 | injection payload → quarantined, never retrieved, emits `memory.quarantined` | `unit/test_write_pipeline.py::test_ac2_*` |
| AC-3 | verbatim/near-dup merge into one, provenance appended, `merged_from` set | `unit/test_write_pipeline.py::test_ac3_*` |
| AC-4 | cross-tenant retrieval returns nothing (hard tenant filter) | `integration/test_real_adapters.py::test_ac1_ac4_*`, `unit/test_api_isolation_authz.py::test_ac4_*` |
| AC-5 | blended ranking, excludes quarantined/expired, respects top_k | `unit/test_retrieval.py::test_ranking_blend_and_debug` |
| AC-6 | case.resolved → anonymized chunk in resolved_cases, replaced-not-duplicated on re-ingest | `unit/test_corpus.py::test_ac6_*`, `integration/test_kafka_corpus.py` (real Kafka + Ollama) |
| AC-7 | erasure cascade across 3 scopes + chunks + session, verified report | `unit/test_erasure.py::test_ac7_*`, `integration/test_real_adapters.py::test_ac7_*` |
| AC-8 | re-validation decays unretrieved, expires < 0.3, extends on recent retrieval | `unit/test_retention_policy.py::test_ac8_*` |
| AC-9 | workspace removal → SCOPE_DENIED at retrieval | `unit/test_retrieval.py::test_ac9_*` |
| AC-10 | corpus rebuild atomic version switch, no mixed versions, old dropped | `unit/test_corpus.py::test_ac10_*` |
| AC-11 | embeddings outage → retrieval degrades AND write queues in `mem:pend` (drained on recovery, fails past ≤1h, never persisted unembedded); screening outage → write fails closed | `unit/test_write_pipeline.py::test_ac11_*` (queue + drain + window + degrade + fail-closed) |
| AC-12 | session sanitization hook wipe, idempotent 204 | `unit/test_retention_policy.py::test_ac12_*`, `unit/test_api_isolation_authz.py::test_session_hook_*` |
| AC-13 | TTL override bounds (999d→422, 90d accepted) | `unit/test_retention_policy.py::test_ac13_*` |
| AC-14 | snapshot pin returns only chunks ≤ snapshot | `unit/test_corpus.py::test_ac14_*` |
| AC-15 | cap eviction of lowest conf×recency, emits `memory.expired{reason:cap}` | `unit/test_write_pipeline.py::test_ac15_*` |
| AC-16 | contradiction (sim 0.85) both persist, newer ranks first | `unit/test_write_pipeline.py::test_ac16_*` |
| AC-17 | `run.flagged` quarantines memories by run_id | `unit/test_retention_policy.py::test_ac17_*` |

Cross-cutting: isolation + authz matrix in `unit/test_api_isolation_authz.py`;
RLS via the non-privileged `memory_rt` role in
`integration/test_real_adapters.py::test_rls_isolation_non_privileged_role`;
real OPA authz in `integration/test_opa_authz.py`.

## Endpoints

`POST /api/v1/memories` · `/memories/batch` · `POST /api/v1/retrieve` ·
`GET/PATCH/DELETE /api/v1/memories[/:id]` · `POST /memories/:id/unquarantine` ·
`POST/GET /api/v1/erasure[/:id]` · `GET/PUT /api/v1/policies/self` ·
`POST/PATCH /api/v1/corpora[/:key]` · `POST /corpora/:key/rebuild` ·
`GET /corpora/:key/status` · `POST /corpora/docs/documents` · `GET /api/v1/stats` ·
`DELETE /internal/v1/sessions/:id/memory` (mTLS/SPIFFE, BRD 14 hook) ·
`GET /healthz` · `GET /readyz?tenant=` (BR-14). Full schema: `api/openapi.yaml`.

## Events

Emitted to `memory.events.v1` via the transactional outbox (schemas in
`events/*.avsc`): `memory.written`, `memory.quarantined`, `memory.deleted`,
`memory.expired`, `memory.edited`, `erasure.completed`. Consumed:
`identity.events.v1` (`tenant.provisioned`→provision schema+policy+corpora;
`user.deleted`→erasure; `tenant.deleted`→schema drop), `case/chart/dataset/
semantic.events.v1` (corpus mappers), `agent.events.v1` (session wipe),
`security.events.v1` (`run.flagged`→quarantine).
