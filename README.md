# Windrose

Multi-tenant, multi-cloud, agentic-AI-native ML platform. Greenfield rebuild per
[`WINDROSE_PLATFORM_ARCHITECTURE.md`](../WINDROSE_PLATFORM_ARCHITECTURE.md) and the
service BRDs in [`../docs/brd/`](../docs/brd/).

## Repository layout

```
Windrose-ai/
  services/              one directory per service (see docs/brd index)
    identity-service/    Go    — tenants, users, agent principals, OBO tokens, JWKS
    rbac-service/        Go    — workspaces, groups, roles, grants, permissions_flat projection
    ingestion-service/   Py    — connections, streaming ingestion jobs
    dataset-service/     Py    — datasets, versions, profiles, lineage
    ...                        (built in waves; see Build status below)
  libs/                  shared libraries (extracted after wave 1 — see CONVENTIONS.md)
  deploy/
    docker-compose.dev.yml     local dev infrastructure
  Makefile
```

## Local development

```bash
make dev-up        # start Postgres, Redis, Redpanda (Kafka), Keycloak, Temporal, OTel
make dev-down
make test          # run all service test suites
```

Each service is independently runnable: see its own `README.md` + `Makefile`.

## Conventions

Every service implements the shared requirements in
[`../docs/brd/00_MASTER_BRD.md`](../docs/brd/00_MASTER_BRD.md) (tenancy/RLS, JWT claims,
URN scheme `wr:<tenant>:<service>:<type>/<id>`, error envelope, pagination, outbox events,
OTel). Repo-specific rules: [`CONVENTIONS.md`](CONVENTIONS.md).

## Build status

**Canonical status:** [`BUILD_STATUS.md`](BUILD_STATUS.md) — evidence-based, per-service, refreshed at end of every coding push. Read that document, not this summary, before scheduling work.

**Summary (2026-07-12):**
- **All 22 Core services (BRDs 01–22): 🟢 Feature-complete, verification pending.** Each service has 4K–11K LOC of production code, migrations, integration tests, and README FR-checklists.
- **Real end-to-end journey:** [`deploy/e2e/driver.py`](deploy/e2e/driver.py) exercises a claims-triage-and-governance run through the full stack — RS256 JWTs from a harness IdP, real OPA, real MinIO/Iceberg, real OpenSearch, real Postgres RLS, real Redpanda, real Ollama LLM, real Temporal.
- **⚫ Design only — no code yet:** BRD 12 §3.8 (ai-gateway cost mechanisms), BRD 17 §3.8 (usage-service decision-linked cost + ROI), BRD 21 §3 (bff-graphql `display_labels`), BRD 22 §3 (ui-web Simple UX Charter operationalization), Master BRD §2.9 (Simple UX Charter), BRD 23 (pack-service), BRD 24 (`insurance-claims-payer` pack). See BUILD_STATUS.md §3.1. ~40–60 engineer-weeks to close.
- **Nothing is deployed to a customer environment.** First design-partner pilot targeted for Phase 4 (~weeks 21–34 per BUILD_STATUS.md §5).
