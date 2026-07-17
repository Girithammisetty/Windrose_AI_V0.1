# Live-stack E2E regression suite (`tests-live/`)

Browser end-to-end tests that drive the **real running platform** — nothing mocked.

```
Playwright (Chromium)
  → real Next.js UI (:3000)
    → real bff-graphql (:4000)
      → all ~22 services
        → real Postgres (RLS) / Redpanda / Redis / MinIO / Iceberg / Temporal
```

This is deliberately **separate** from `../tests-e2e/` (the `playwright.config.ts` suite),
which boots the UI against a **contract server**. That contract suite is fast and
hermetic and good for UI-contract regressions; it does **not** prove the real
services compose. `tests-live/` is the suite that does.

## Prerequisite: the stack must be up

The suite does **not** boot or seed anything — it reuses the running stack and the
real seeded persona map (`deploy/local/run/personas.json`). Bring the platform up first:

```bash
cd deploy/local && ./up.sh      # infra + all services + BFF + UI, and writes personas.json
```

`global-setup.ts` fail-closes with an actionable message if the UI/BFF/persona map
is not reachable, so you never chase 40 opaque per-spec timeouts.

## Run

```bash
cd services/ui-web
export PATH=/opt/homebrew/opt/node@20/bin:$PATH   # node@20 for this repo

pnpm e2e:live                    # headless, full suite
pnpm e2e:live:headed             # watch it drive the browser
pnpm e2e:live -- tests-live/smoke.spec.ts          # one spec
pnpm e2e:live:report             # open the last HTML report
```

## How auth works here (real, not mocked)

`up.sh` boots the UI with `AUTH_MODE=dev` and `WINDROSE_PERSONAS` bound to the real
provisioned tenant. Each spec logs in through the **real** `/api/auth/login`, which
mints a **real RS256 JWT** for a seeded persona; the BFF verifies it against the UI's
JWKS and every downstream service enforces real RBAC/OPA. So a passing test exercises
the true production auth posture — including fail-closed redirects and per-persona
capability differences.

`loginAs(page, PERSONAS().admin | .adjuster | .manager | .datascientist)` — the four
real seeded personas (`*@demo.windrose`), resolved from `.live-context.json` (written
by global-setup). `admin` is the most-privileged; `adjuster` is a differentiated
non-admin used to prove RBAC gating.

## Design rules for specs

- **Self-contained + idempotent.** Write journeys create their own fixtures through
  the real UI with unique names, so specs are order-independent and re-runnable
  against a shared RLS tenant. Never assume a specific pre-existing row.
- **Assert on real responses.** Use `page.waitForResponse(/\/api\/graphql/)` and check
  the returned data for mutations, not just DOM text.
- **Serial by default.** `workers: 1` — journeys mutate shared tenant state; keep them
  deterministic. (Reads could parallelize later behind a separate project.)
- **Structural smoke, deep journeys.** `smoke.spec.ts` asserts every module renders
  (shell + header, no error boundary, no auth bounce) across 23 service-owned routes;
  the journey/per-module specs do the deep write→read→verify assertions.

## Files

| File | Role |
|---|---|
| `../playwright.live.config.ts` | Live config: baseURL :3000, no webServer (reuses running UI), retries, HTML report |
| `global-setup.ts` | Health-gate + real-persona login check; writes `.live-context.json` |
| `fixtures.ts` | `loginAs` / `logout` / `expectPageHealthy` / `PERSONAS` / `liveContext` |
| `smoke.spec.ts` | 23 module-render smokes + fail-closed guard + non-admin persona |
| `hero-learning-loop.spec.ts` | The differentiator journey: AI proposal → human correction → learning-signal capture |
| `*.spec.ts` (per-module) | Cases, dataset/pipeline, charts, RBAC gating, eval, create→update edits |

## CI

Wired into `.github/workflows/ci.yml` (the repo's one GitHub Actions CI workflow — see
`deploy/CONFIG.md` for the registry/tag conventions it shares with the `cd-*.yml` deploy
workflows) as two jobs, added alongside the existing `test-go`/`test-python`/`test-node`/
`no-stub-gate`/`build-push` lanes:

- **`live-e2e-paths`** — a `dorny/paths-filter@v3` gate. `ci.yml` otherwise runs its full
  matrix on every push/PR with no path filtering; this job scopes the (expensive) live
  suite to changes under `services/**`, `deploy/local/**`, `deploy/e2e/**`,
  `deploy/docker-compose.dev.yml`, or `deploy/services.yaml`, so a PR that only touches
  e.g. terraform or docs doesn't pay a ~10min stack boot for nothing.
- **`e2e-live`** — runs when `live-e2e-paths` says yes (or on a manual
  `workflow_dispatch`), and only after `test-go`/`test-python`/`test-node`/`no-stub-gate`
  have passed. Steps: checkout → free disk space → setup Go 1.26 / Python 3.12+uv /
  Node 20+pnpm → install Ollama and pull the real `llama3.2:latest`, `qwen2.5:0.5b`,
  `nomic-embed-text` models → install ui-web deps + Playwright chromium → `deploy/local/up.sh`
  (full real boot: infra, all 22 services, seed, bff, ui) → `pnpm --dir services/ui-web
  e2e:live` (this exact command, proven working by tasks #62/#63) → on failure, tail
  `deploy/e2e/logs/*.log` → always upload `playwright-live-report/` and
  `test-results-live/` as build artifacts (plus service logs on failure) → always tear
  down with `deploy/local/down.sh --infra`. `CI=1` is set for the run, which is what
  makes the config above add retries, `--forbid-only`, and the `github` reporter.
  Standard PR gate semantics: a failing `e2e:live` fails the `e2e-live` check; add it to
  the repo's required-status-checks branch protection list to make it merge-blocking.

**Known, called-out limitation — Ollama/runner sizing.** The platform's real-LLM
dependency (Rule: no fake/mock — see `up.sh`'s own preflight, which hard-fails if Ollama
isn't reachable with those three models) is a genuine CI cost: this job installs a real
Ollama binary and pulls real models, it does not stub or skip LLM-dependent specs.
Combined with booting all ~22 services + the full Docker infra stack
(Postgres/Redis/Redpanda/Keycloak/Temporal/MinIO/Iceberg/OPA/Vault/MLflow/OpenSearch/
ClickHouse), this does **not** reliably fit GitHub's standard hosted `ubuntu-latest`
runner (4 vCPU / 16GB RAM / ~14GB free disk) — expect OOM kills and disk-pressure
flakes under real load; `up.sh`'s own preflight already warns to give Docker alone
`>=10GB`. The job reads its runner label from the `E2E_LIVE_RUNNER` repo/org variable
(defaulting to `ubuntu-latest` so the workflow is valid out of the box) — point it at a
larger GitHub-hosted runner (8+ vCPU / 32GB+ RAM) or a self-hosted runner sized for this
stack once one is available. This is a real infra gap, not a silently-skipped test.
