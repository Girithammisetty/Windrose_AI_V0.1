# eval-service — the agent quality flywheel (BRD 16)

Manages **versioned golden datasets** per agent, runs a **scorer framework**
(deterministic first, LLM-judge second — judge-only verdicts never gate alone),
executes **eval runs**, exposes the **CI gate API** (pass/fail vs baseline with
regression thresholds), performs **canary comparison**, stores scores with trend
APIs, and computes **agent SLOs**. This is how a retrained/new agent version is
gated before promotion (the learning-loop tie-in).

Python 3.12 · FastAPI · SQLAlchemy 2 async · Postgres RLS · alembic · uv.

## Run

```bash
make install          # uv sync
make migrate          # alembic upgrade head  (EVAL_MIGRATE_URL / EVAL_DATABASE_URL)
make run              # uvicorn app.main:app  (port 8313)
make test-unit        # no external deps
make test-integration # real infra (Postgres/Redis/Redpanda/OPA/ai-gateway/Ollama); auto-skips
make lint             # ruff
```

**Real adapters are the DEFAULT** (`EVAL_USE_REAL_ADAPTERS` defaults `true`; only
the test suite sets it false to reach the unit/dev doubles). `app.main` wires the
Postgres+RLS store, Redpanda `KafkaEventBus`, Redis `RedisDedupStore`, real
`OpaAuthzClient`, the `AiGatewayJudgeClient` (judge request class → ai-gateway →
Ollama) and the DuckDB fixture warehouse. Confirmed booting with **default env**:
`authz=OpaAuthzClient, bus=KafkaEventBus, dedup=RedisDedupStore,
judge=AiGatewayJudgeClient, warehouse=DuckDbFixtureWarehouse, SqlUnitOfWork`,
5 flywheel consumer groups on Redpanda, DSN = the non-owner `eval_app_rt` role.

The default DSN uses **`eval_app_rt`** — a non-owner, non-superuser DML role
(member of `eval_app`). Every tenant table has `ENABLE` **and** `FORCE ROW LEVEL
SECURITY`, so RLS is enforced even against the table owner (superusers bypass RLS,
so the runtime role must never be one). Migrations run under a privileged role via
`EVAL_MIGRATE_URL`.

## LLM-judge path (EVL-FR-012)

Judges call the **real ai-gateway `judge` request class** (temperature 0, pinned
ladder) via a virtual key + a self-minted platform JWT — never direct to Ollama.
`tests/integration/test_real_judge_aigateway.py` boots a real ai-gateway against
the compose infra, seeds a `balanced → qwen2.5:0.5b` deployment + virtual key,
and proves a real groundedness score with real tokens.

## FR / AC traceability

| FR / AC | Where |
|---|---|
| EVL-FR-001/002 dataset + case model | `app/domain/entities.py`, `migrations/0001` |
| EVL-FR-003 case sourcing (verified query / rejection / edit-diff) | `domain/services.py:CaseService.from_*`, `events/consumer.py`; tests `unit/test_datasets_cases.py` |
| EVL-FR-004 curation (promote/attest/reject/edit/retire) | `CaseService`, `api/routes/cases.py` |
| EVL-FR-010 scorer registry + gate-eligibility | `domain/scorers/registry.py` |
| EVL-FR-011 deterministic scorers (sql equivalence, tool selection, schema, cost, proposal, latency) | `domain/scorers/deterministic.py`, `adapters/fixture_warehouse.py` (DuckDB); tests `unit/test_scorers.py` |
| EVL-FR-012 LLM-judge (groundedness, helpfulness) via ai-gateway | `domain/scorers/judge.py`, `adapters/judge_client.py`; `integration/test_real_judge_aigateway.py` |
| EVL-FR-014 judge calibration gate (agreement < 0.8 blocks) | `ScorerService.activate`; test `unit/test_slo_canary.py` |
| EVL-FR-020..024 eval runs (fan-out 20, cost cap, pins) | `domain/runner.py`, `RunService`; `unit/test_runs_gate.py` |
| EVL-FR-021 triggers (CI webhook, publish gate, online, canary, manual) | `api/routes/ci.py`, `RunService`, `events/consumer.py` |
| EVL-FR-022 suite + gate rule validation (≥1 deterministic term) | `domain/gate_rule.py`, `SuiteService.create` |
| EVL-FR-030/031 CI gate API vs baseline, immutable addressable result | `GateService`, `api/routes/gates.py` |
| EVL-FR-040..042 canary (bootstrap CIs, early stop) | `domain/canary.py`, `CanaryService` |
| EVL-FR-050 score storage + trends | `TrendService`, `api/routes/trends.py` |
| EVL-FR-051/052 agent SLO computation + budget burn | `domain/slo.py`, `SloService`, `api/routes/trends.py` |
| Events (gate.completed, canary.*, case.promoted, slo.budget_burn, eval_run.*) | `events/envelope.py`, outbox → `store/sql.py:OutboxDispatcher`, `events/eval_event_envelope.avsc` |
| BR-1 judge-never-gates-alone (incl. OR-gate bypass) | `gate_rule.validate` rejects OR rules mixing a judge term (save + gate time); tests `unit/test_scorers.py`, `unit/test_api_isolation_authz.py` |
| EVL-FR-021c / AC-9 online sampling (production-safe, no re-execution, per-tenant caps) | `domain/online.py`; test `unit/test_real_default_and_online.py` |
| AC-15 frozen-row immutability (DB trigger) | `migrations/0001` `trg_block_frozen_case`; test `integration/test_rls_isolation.py` |
| MASTER-FR-001 FORCE RLS + non-owner default DSN | `migrations/0001` (FORCE + `eval_app_rt`); test `integration/test_rls_isolation.py::test_shipped_default_role_rls_isolation` |
| BR-2 baseline integrity → BASELINE_INCOMPARABLE | `GateService.evaluate_from_run` |
| BR-3 anonymization gate on production-sourced cases | `CaseService.promote` |
| BR-9 SQL timeout / row cap | `adapters/fixture_warehouse.py`, `SqlResultEquivalenceScorer` |
| MASTER-FR-001 RLS tenant isolation | `migrations/0001`, `store/sql.py`; `integration/test_rls_isolation.py` |
| MASTER-FR-012 real OPA authz | `api/auth.py:OpaAuthzClient`; `integration/test_real_adapters.py` |
| RBC-FR-022 action registration | `app/registration.py` |
| AC-1..AC-15 | named tests in `tests/unit/` + `tests/integration/` |

## Deferred / infra-gated exceptions (no runtime stubs)

- **Temporal orchestration of eval runs** (EVL-FR-020 mentions Temporal workflows):
  the scoring engine is real and in-process (`domain/runner.py`); wrapping it in
  Temporal activities is a deferred orchestration layer, not a stub — no fake
  reachable at runtime.
- **Langfuse score write-back / trace sampling** (EVL-FR-060/061): contract
  settings exist (`langfuse_*`); enrichment only, never gates (BR-13). Live
  write-back is credential/deployment-gated.
- **Containerized custom scorer plugins** (EVL-FR-013): registry + config-schema
  supported; sandbox-pool execution is deployment-gated.

The in-memory bus/dedup/store are **test-only doubles**, reachable exclusively
when the test suite sets `EVAL_USE_REAL_ADAPTERS=false`; the default runtime
(`app.main`) never wires them.
