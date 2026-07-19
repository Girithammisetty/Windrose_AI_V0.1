# Windrose Repo Conventions

Authoritative requirements: `../docs/brd/00_MASTER_BRD.md` (inherited by every service) and
each service's own BRD. This file adds repo mechanics only.

## Service layout

**Go services** (Go 1.23+, chi router, pgx, golang-migrate):
```
services/<name>/
  cmd/server/main.go
  internal/{api,domain,store,events,authz}/
  migrations/            forward-only SQL
  Makefile               build / test / lint / run
  Dockerfile             distroless
  README.md              run + test instructions, implemented FRs checklist
```

**Python services** (Python 3.12+ compatible, FastAPI, SQLAlchemy 2 async, alembic, pytest):
```
services/<name>/
  app/{api,domain,store,events}/
  migrations/
  pyproject.toml         managed with uv
  Makefile
  Dockerfile
  README.md
```

## Wave-1 rule: self-contained services

To maximize build parallelism, wave-1 services vendor their own copies of the small
cross-cutting helpers (JWT verification, error envelope, cursor pagination, outbox,
tenant-context middleware) inside `internal/`/`app/` following the master BRD contracts
EXACTLY (same claim names, same error codes, same envelope). After wave 1 these are
extracted to `libs/go-common` and `libs/py-common`; identical contracts make that a
mechanical refactor.

## Testing tiers (all services)

1. `make test-unit` — no external dependencies; always runnable.
2. `make test-integration` — requires Docker (Testcontainers or compose services);
   **must auto-skip with a clear message when Docker is unavailable.**
3. Mandatory suites per master BRD §2.8: tenant-isolation tests (cross-tenant → 404) and
   the authz matrix test. Where they need Postgres RLS, they live in the integration tier;
   a unit-tier variant with an in-memory policy fake must also exist.

## Contracts

- OpenAPI spec per service at `api/openapi.yaml`, kept in sync with handlers.
- Event schemas per service at `events/*.avsc` (envelope per master BRD §2.4-031).
- Cross-service calls in tests use fakes generated from these contracts — never live services.

## END STATE (non-negotiable): a real platform, no stubs in the runtime

The finished platform MUST run with **zero mock/fake/stub code in any runtime path** and be
**testable end-to-end on this Mac**. This overrides the wave-1 "stub the adapter" allowance,
which was a temporary scaffolding step only.

**Two kinds of "fake" — one allowed, one forbidden:**
- ALLOWED: in-memory test doubles used **only inside unit tests** (`*_test.go`, `tests/unit/`).
  They must never be reachable from `cmd/server` / `app/main.py` wiring.
- FORBIDDEN at end state: `NotImplementedError` / `ErrNotWired` / "TODO real impl" / hardcoded
  fake responses anywhere a running service can reach them. Every adapter is real.

**Real = against local, protocol-compatible infrastructure** (all in `deploy/docker-compose.dev.yml`).
These are genuinely real integrations, not simulations — the adapter speaks the real wire protocol:

| Capability | Real local implementation |
|---|---|
| OLTP | PostgreSQL 16 |
| Event bus | Redpanda (real Kafka API) + Schema Registry |
| Cache / projection | Redis 7 |
| AuthN (OIDC) | Keycloak |
| AuthZ | OPA sidecar loading the real Rego bundle |
| Durable workflows | Temporal |
| Object storage (S3/GCS/Blob) | MinIO (real S3 API) |
| Lakehouse | local Iceberg REST catalog + MinIO |
| Secrets | Vault (dev mode, real Vault API) |
| Warehouse / query engine | DuckDB (embedded) + Trino; MLflow server for ML tracking |
| **LLM / agents** | **Ollama** running a real local model (e.g. Llama/Qwen) — real inference, no mock |
| Connection drivers (JDBC/SFTP/HTTP) | tested against dockerized Postgres/MySQL/SFTP/HTTP |

**The one honest ceiling (requires YOUR cloud credentials to verify):** cloud-only data
warehouses and SaaS sources with no local-protocol equivalent — BigQuery, Athena, Synapse,
a live Snowflake account, and real AWS/Azure/GCP endpoints (as opposed to MinIO/Redpanda/Vault).
Their adapter CODE is written for real and unit-tested, but end-to-end verification is gated on
credentials. Everything else on the platform is fully real and verifiable here.

**`make e2e` (repo root):** boots the full compose stack and runs cross-service user journeys
with NO fakes in the path (e.g. provision tenant → issue JWT via Keycloak → ingest a file to
MinIO/Iceberg → profile it → run a governed query via the semantic layer → render a chart →
ask the analytics agent a question answered by the real local LLM → raise a case). This is the
acceptance gate for "done."

**CI no-stub gate:** a repo check greps runtime (non-test) source for `NotImplementedError`,
`ErrNotWired`, `panic("TODO")`, and fake-adapter class names; it must return zero at end state.

## Definition of done (per service)

- All Must (M) FRs implemented; Should (S) implemented (not stubbed) unless explicitly deferred
  in writing with sign-off.
- **Every adapter wired to real local infra (table above). No stub reachable from runtime.**
- Unit tests green (may use test doubles); integration tests green against real infra;
  the service participates in `make e2e` with no fakes in its path.
- README maps each implemented FR/AC to code/tests (traceability table) and lists any
  credential-gated cloud adapter as the only exception.
- `make lint` clean (golangci-lint / ruff); no-stub CI gate passes.
