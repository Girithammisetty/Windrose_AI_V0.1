# bff-graphql

The single GraphQL endpoint for **ui-web** on the Windrose insurance-claims
platform (BRD 21). It is a **UI-shaped aggregation layer** over the domain
services' REST APIs: it composes and reshapes page-shaped data so the browser
issues one query instead of a dozen REST calls. It contains **no business logic
and no authorization** — it forwards the caller's JWT and lets the domain
services enforce authz.

TypeScript (strict) · Node 20 · pnpm · Apollo Server 4 · real `fetch` (undici).

## Run

```bash
export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$PATH"
pnpm install
pnpm dev            # watch-mode on :4000 (introspection on, ad-hoc queries allowed)
pnpm start          # run once
pnpm typecheck      # strict tsc --noEmit
pnpm lint           # eslint (also forbids importing pg / kafkajs / ioredis)
pnpm test           # unit + the real integration test
```

Endpoints: `POST/GET /graphql`, `GET /healthz`, `GET /readyz`, `GET /metrics`.

### Configuration (env)

The BFF holds **no credentials**. It is configured only with downstream base
URLs, the identity-service JWKS URL, and the realtime-hub URL.

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `4000` | listen port |
| `NODE_ENV` | `development` | `production` turns on the persisted-query allowlist and turns off introspection |
| `VERIFY_JWT` | `true` | verify the inbound JWT signature at the edge (fail-fast) |
| `JWKS_URL` | `<IDENTITY_URL>/.well-known/jwks.json` | identity-service JWKS (RS256 public keys) |
| `JWT_ISSUER` / `JWT_AUDIENCE` | — | optional `iss`/`aud` checks |
| `REALTIME_HUB_URL` | `http://localhost:9020` | surfaced inside `StreamHandle` fields |
| `IDENTITY_URL`, `DATASET_URL`, `CASE_URL`, `CHART_URL`, `USAGE_URL`, `EXPERIMENT_URL`, `AGENT_RUNTIME_URL` | localhost defaults | downstream service roots |
| `DOWNSTREAM_TIMEOUT_MS` | `10000` | per-downstream timeout (BR-4) |
| `MAX_DEPTH` / `MAX_ALIASES` / `MAX_ROOT_FIELDS` / `MAX_COST` | `10` / `20` / `5` / `5000` | static query limits (BFF-FR-041) |

## Schema module map — which service backs each area

The SDL (`src/schema/typeDefs.ts`) is organised by module. Every type's SDL
description names the downstream service + endpoint that backs it (BR-12).

| Module | GraphQL types | Backed by (REST) |
|---|---|---|
| **platform** | `Viewer`, `User` | identity-service `/users` |
| **data** | `Dataset`, `Profile` | dataset-service `/datasets`, `/datasets/{id}/profile` |
| **insights** | `Dashboard`, `Chart`, `ChartData` | chart-service `/dashboards`, `/charts`, `/dashboards/{id}/data` (batch) |
| **cases** | `Case` | case-service `/cases` (assignee → identity, sourceDataset → dataset, proposals → agent-runtime, resolved via dataloaders) |
| **agentic** | `Proposal`, `AgentRun`, `TokenUsage` | agent-runtime `/proposals`, `/runs`, `/runs/{id}/trace` |
| **ml** | `Experiment`, `Run`, `RegisteredModel` | experiment-service `/experiments`, `/runs`, `/models` |
| **usage** | `CostPanel`, `UsageRow`, `BudgetState` | usage-service `/reports/usage`, `/budget-states` |

Root operations: `me`, `user`, `dataset(s)`, `dashboard(s)`, `case`,
`caseSearch`, `proposalsInbox`, `proposal`, `agentRun`, `experiment(s)`, `run`,
`workspaceCostPanel`; mutations `updateCase`, `decideProposal`.

## How JWT passthrough + downstream authz works

1. **Edge verification (fail-fast only).** On each request the BFF verifies the
   inbound `Authorization: Bearer <JWT>` signature/exp against the
   identity-service JWKS (`src/auth/jwt.ts`, cached ≤ 5 min, `alg=none` refused).
   A missing/invalid token → `401 UNAUTHENTICATED`. This is *only* to reject
   junk early.
2. **Verbatim forwarding.** The **original** token is forwarded unchanged on
   every downstream call (`src/clients/base.ts`). The BFF mints no tokens and
   holds no service credentials. `tenant_id` is read from the token for log
   correlation only — it is never a query argument (BFF-FR-011).
3. **The domain services decide.** All authorization + tenant isolation happen
   downstream. A downstream `403` becomes `PERMISSION_DENIED`; a tenant-masked
   `404` on a nullable field becomes `null` with no error (BR-3). The BFF makes
   **zero** authz decisions — verified by the integration test, which asserts a
   real downstream 403 surfaces as `PERMISSION_DENIED` and that the downstream
   received the caller's exact token.

## Design highlights (map to BRD)

- **Dataloaders** (`src/loaders/`) — one per (service, resource); a page of N
  cases costs one batched `?filter[id]=…` per nested type, not N (BFF-FR-030/031).
- **Error mapping** (`src/errors/`) — downstream `{error:{code,message,trace_id}}`
  → GraphQL `extensions {code, details, traceId, service, httpStatus}`; codes
  preserved verbatim; `INTERNAL` leaks nothing (BFF-FR-050/051).
- **Static limits** (`src/validation/limits.ts`) — depth ≤ 10, aliases ≤ 20,
  root fields ≤ 5, cost ≤ 5000 → `QUERY_TOO_COMPLEX` before any downstream call.
- **Persisted queries** (`src/plugins/persistedQueries.ts`) — production accepts
  only allowlisted operation hashes → `PERSISTED_QUERY_REQUIRED`; dev/test allow
  ad-hoc (BFF-FR-040).
- **Streaming delegated** — no `Subscription` root; `AgentRun.tokenStream`
  returns a `StreamHandle { hubUrl, topics }` pointing the client at
  realtime-hub directly (BFF-FR-060). The BFF never proxies SSE/WebSocket.
- **Pagination** (`src/pagination.ts`) — `(first ≤ 200, after)` ↔ REST
  `(limit, cursor)`; `first > 200` → `VALIDATION_FAILED` (AC-13).

## Tests

- **Unit** (`tests/unit/`) — schema, resolvers, error mapping, dataloaders,
  pagination, query limits, persisted-query gate. Downstream HTTP is mocked at
  the boundary *only in tests* (`tests/helpers/mockFetch.ts`); the client code
  under test is the real one.
- **Integration** (`tests/integration/realDownstream.test.ts`) — **real** RS256
  keypair, a **real** signed user JWT, a **real** JWKS served over HTTP and
  verified at the edge, the **real** BFF booted over HTTP, and resolvers calling
  **real** local HTTP servers that return the domain services' OpenAPI-shaped
  bodies. Asserts composed cross-service data (`case → assignee → sourceDataset`),
  JWT passthrough (downstream receives the exact token), and 403 → `PERMISSION_DENIED`.

## Boundaries (out of scope by design)

No database, no Kafka, no tenant data at rest (per-request dataloader
memoization only), no SSE/WebSocket proxying, no write orchestration (one
mutation = one downstream write), no authorization logic. ESLint forbids
importing `pg` / `kafkajs` / `ioredis` to keep this honest.
