# Windrose Hands-On Testing Walkthrough

**Date:** 2026-07-12 · **Audience:** developers testing the platform for the first time.
**Companion docs:** `README.md` (repo intro) · `BUILD_STATUS.md` (what's built vs designed) · `CONVENTIONS.md` (repo mechanics) · `deploy/e2e/driver.py` (scripted end-to-end verifier).

**The problem this doc solves.** You brought up the platform, saw four persona emails, logged in as each — and had no idea what to click, in what order, or how to verify anything actually happened. This is the buttons-to-click walkthrough.

---

## 0. Prerequisites (do this once)

You need Docker (≥10 GB memory), Go 1.23+, Python 3.12+ via `uv`, Node 20, `pnpm`, and Ollama running locally with three models:

```bash
brew services start ollama
ollama pull llama3.2:latest
ollama pull qwen2.5:0.5b
ollama pull nomic-embed-text
```

Verify from the repo root:

```bash
cd Windrose-ai
docker info                    # daemon reachable
curl -s http://localhost:11434/api/tags | grep -c model   # ≥3
```

## 1. Bring the whole platform up

**One command boots the entire platform + claims demo data:**

```bash
cd Windrose-ai
make up
```

That runs `deploy/local/up.sh` which:

1. **Preflight** — checks Docker/Ollama/ports/build tools.
2. **Infra** — Postgres, Redis, Redpanda, Keycloak, Temporal, OTel, MinIO, OpenSearch, OPA.
3. **Migrate + boot all 22 services** on ports `8085`, `8086`, `8300–8324`, `bff-graphql` on `4000`, `ui-web` on `3000`.
4. **Platform seed** (`deploy/local/seed_platform.py`) — creates the tenant, four personas, and real RBAC grants.
5. **Claims vertical seed** (`deploy/local/seed_claims_demo.py`) — ingests the claims CSV, publishes a `claims_core` semantic model, builds a "Claims Insights" dashboard, creates a queue of OPEN triage cases, runs the triage copilot on two cases so PENDING proposals sit in the approval inbox, and (best-effort) drives one full retrain.

Flags:
- `make up ARGS=--platform-only` — just tenant + personas (no vertical data). Use this to test your own "walk it like a first-time admin would" flow.
- `make up ARGS=--core` — RAM-constrained subset (fewer services, still walkable end-to-end).
- `make up ARGS=--no-retrain` — skip the training pipeline (faster boot).
- `make up ARGS=--skip-build` — reuse compiled binaries.

When it finishes you'll see a banner with the URL and the four logins. **URL: `http://localhost:3000`.**

To stop: `make down` (services only) or `make down ARGS=--infra` (services + Docker infra).

## 2. What just got seeded (mental model)

**One tenant** with **one workspace** containing:

| Seeded artifact | Detail |
|---|---|
| **Semantic model** `claims_core` | Entities: `claims`. Dimensions: `region`, `line_of_business`, `severity`. Measures: `claim_count`, `total_paid_usd`, `avg_paid_usd`. Approved, published, versioned. |
| **Dashboard** "Claims Insights" | Charts backed by `claims_core` compile. |
| **Datasets** | The claims CSV ingested and profiled. |
| **OPEN triage cases** | ~8 cases mined from CSV rows — includes duplicate-invoice pair (`CLM-1001`/`CLM-1002` on `INV-5540`), non-ASCII claimants (`Zürich Ré`, `María José Peña`, `Björk Ólafsdóttir`), high-value / mid-value / low-value spread. |
| **PENDING triage copilot proposal(s)** | Sit in the approval inbox waiting for a manager. |
| **Trained + promoted model** (if `--no-retrain` was NOT passed) | Real training pipeline ran to completion. |

**Four personas** are seeded with **real RBAC grants** (not fake tokens):

| Email | Role | Group memberships | What they can do |
|---|---|---|---|
| `adjuster@demo.windrose` | adjuster | Case Analyst | Read + write cases and dispositions. Read datasets/experiments/dashboards. Read + decide on proposals. |
| `manager@demo.windrose` | manager | Case Manager | All of Adjuster PLUS `usage.report.read` (cost/ROI panel). Primary persona for approving proposals. |
| `datascientist@demo.windrose` | datascientist | Model Builder, Data User, **Use case Admin** | Datasets, experiments, models, semantic-model builder. The Use case Admin membership means Data Scientist can approve a semantic-model version authored by someone else (four-eyes review per SEM-FR-007). |
| `admin@demo.windrose` | admin | Admin (wildcard `*`) | Tenant admin, kill switches, ai-gateway ladders + guardrails, memory admin, tools admin, archive, audit, usage/budgets. |

**Login is dev-mode** (`AUTH_MODE=dev`): type any of the four emails on the login form → the UI's `/api/auth/login` route mints a real RS256 JWT into an httpOnly cookie, using the `WINDROSE_PERSONAS` map that `make up` injected. Downstream services enforce real authz through OPA + the rbac projection.

## 3. Walk it as each persona

The walkthrough below is written as a story arc: each persona does one meaningful thing, and their outputs are the next persona's inputs. Follow it in order the first time; branch off after.

Open `http://localhost:3000` in your browser. You'll land on the login page.

---

### Persona A — Data Scientist (`datascientist@demo.windrose`)

**Role in the story:** the analyst who authored the semantic model and dashboards the workflow depends on.

**Login:** `datascientist@demo.windrose` → Sign In.

**Expected landing:** the workspace home (a chat surface + module nav). If `/` shows only Home + Copilot and no other nav, your rbac projection didn't materialize — see §7 Troubleshooting.

**Step-by-step:**

1. **Verify capabilities**
   Open `http://localhost:3000/admin/authz-explain` (Admin-visible; datascientist should see a subset). Expected: your `/me/capabilities` includes `dataset.dataset.list`, `experiment.experiment.read`, `chart.dashboard.read`, and `semantic.model.read/list`. If any are missing → seed didn't complete; re-run `make up`.

2. **Browse the datasets**
   Navigate to `/data/datasets`. Expected: at least one dataset with columns from the claims CSV (`claim_id`, `claimant`, `paid_amount`, etc.). Click one → **Profile** tab → column distributions render (via dataset-service `/profiles`).

3. **Open the semantic model**
   Navigate to `/dashboards` → open "Claims Insights". Expected: the dashboard renders with charts whose data comes from `semantic.compile` (not raw SQL). Hover any chart → cost-attribution footer should show (`<DecisionFooter>` is design-only in BRD 22 — until implemented, you see a bare chart; still verify the numbers are real).

4. **(Optional — the four-eyes review test)** As Data Scientist you also have `Use case Admin`, meaning you can approve a semantic model submitted by another author. To test: (a) log in as Admin (see below), (b) create/edit a semantic-model version and submit it for review, (c) log back in as Data Scientist and approve. This is the SEM-FR-007 review workflow made walkable by two personas.

**Evidence to capture:**
- A chart tile renders real numbers from the CSV. Zero mock data.
- Dataset profile shows non-null distribution histograms.

---

### Persona B — Case Analyst / Adjuster (`adjuster@demo.windrose`)

**Role in the story:** the day-to-day worker who triages open cases.

**Login:** `adjuster@demo.windrose` → Sign In. (Log out from the top-right avatar first, or use an incognito window to keep both sessions.)

**Step-by-step:**

1. **See the case queue**
   Navigate to `/cases`. Expected: virtualized table of OPEN cases from the CSV seed. Look for the two duplicate-invoice cases `CLM-1001` and `CLM-1002` (both cite `INV-5540`). Filter by `severity: high` — should show ~3 cases including `CLM-1006` (María José Peña, $27,650).

2. **Open a case and see the copilot's proposal**
   Click into `CLM-1001`. Expected: the case detail shows:
   - Row-reference data (fetched live from query-service — case-service intentionally never stores full row snapshots while open — CASE-FR-001).
   - The seeded triage-copilot proposal for this case in the **Proposals** tab. Read the rationale — it should cite the duplicate `INV-5540` and reference `CLM-1002`.
   - The audit timeline showing the copilot's tool calls (via `case.disposition_applied` and `ai.tool_invoked` events).

3. **Trigger a fresh copilot run** (optional but shows the live path)
   In the case detail, use the copilot drawer (bottom-right icon) or `/copilot` and ask: *"Explain why CLM-1001 might be a duplicate."* Expected: real Ollama-generated response with citations to `CLM-1002` and shared `INV-5540`. Streaming tokens; latency measurable.

4. **Attempt to approve the proposal**
   Try to Approve the copilot's proposal on this case. Expected: 403. The adjuster role has `agent.proposal.read/decide` scopes at the *base scope layer*, but the **manager** persona (below) is the one who typically has authority to APPROVE a proposal in the seeded workspace. This asymmetry is intentional and tests the authority chain.
   *(If Approve succeeds for adjuster: check `seed/roles_actions.yaml` in rbac — the demo may grant this to Case Analyst; both configurations are valid, note which one you observed.)*

5. **Mark disposition manually**
   As Adjuster you own the case lifecycle: assign to yourself, set status `IN_PROGRESS`, add a comment. These write to case-service directly (not through a proposal). Verify the status chip updates live in the case list (via realtime-hub SSE, no page refresh).

**Evidence to capture:**
- Real cases with the specific claimant names + invoice numbers from `deploy/e2e/data/claims.csv`.
- Copilot response includes ACTUAL tokens from Ollama (not template text).
- Live status update in the case list without hitting reload.

---

### Persona C — Case Manager (`manager@demo.windrose`)

**Role in the story:** the reviewer who approves copilot proposals and monitors cost/ROI.

**Login:** `manager@demo.windrose`.

**Step-by-step:**

1. **The approval inbox**
   Navigate to `/inbox`. Expected: the seeded PENDING triage-copilot proposal(s). Each card shows: agent id + version, tool + args diff, rationale, affected URNs, predicted effect, cost estimate (if usage-service metering wired for this run).

2. **Approve a proposal**
   Open the proposal card → click **Approve**. Watch what happens:
   - agent-runtime issues a signed grant (via its JWKS) that tool-plane verifies before executing the write.
   - case-service applies the disposition.
   - realtime-hub fans out the update; the inbox badge decrements without a reload.
   - The case is now RESOLVED (or whatever status the proposal specified).

3. **Reject one with a reason**
   On a second proposal, click **Reject**. A reason is mandatory. Enter something short like "investigate duplicate first". Expected: rejection recorded; the reason feeds the eval dataset for the next model version (per V3 §5.11 feedback loop).

4. **See the cost panel**
   Navigate to `/admin/usage` (Manager persona has `usage.report.read`). Expected: real metering rows from ai-gateway `ai.token_usage.v1` events, showing per-tenant token counts and cost. Note: **decision-URN attribution (USG-FR-080..086) is design-only** — you will NOT yet see per-case cost until BRD 17 §3.8 is implemented.

**Evidence to capture:**
- Inbox badge decrements without reload (SSE).
- `case.disposition_applied` event in Redpanda for the approved case (verify with `deploy/e2e/lib/kafka.py` if you're spelunking).
- Rejection reason stored on the proposal decision.

---

### Persona D — Tenant Admin (`admin@demo.windrose`)

**Role in the story:** the platform operator managing users, agents, and infrastructure.

**Login:** `admin@demo.windrose`. Wildcard grants — everything in `/admin` is visible.

**Step-by-step tour of the admin surfaces:**

1. **`/admin/tenant`** — tenant profile, provisioning status.

2. **`/admin/tools`** — the MCP tool registry (BRD 13). See every tool exposed to agents, its schema, its per-agent authorizations.

3. **`/admin/memory`** — memory-service admin. Browse scoped memory (session/user/workspace/tenant tiers). Right-to-erasure surface for GDPR-style deletes.

4. **`/admin/ai-gateway/ladders`** — the model ladders per request class (`chat`, `sql-gen`, `judge`, `embed`). See what's pinned per tenant.

5. **`/admin/ai-gateway/guardrails`** — the PII redaction + injection classifier policy, per-tenant. Toggle modes (redact/block/off).

6. **`/admin/ai-gateway/providers`** — provider deployments (Ollama in local dev; Azure OpenAI/Bedrock/Vertex in real deployments).

7. **Agent kill switches** — the admin persona has the literal `tenant.admin` scope (see `seed_platform.py:persona_scopes`) required by agent-runtime's kill-switch routes. Try: turn a kill switch ON for the triage agent → then log in as Manager and try to trigger a run → should see agent disabled state, not an error (per UI-FR-037).

8. **`/admin/archive`** — soft-deleted resources, restore.

9. **`/admin/audit`** — search across audit-service. Filter by actor, agent, resource URN, date range. Every decision made in this walkthrough should appear here with dual attribution (`actor` + `via_agent`).

**Evidence to capture:**
- Every persona action in `/admin/audit` — including the manager's proposal approvals and rejections.
- Kill switch toggling changes UI state within ~5s (Redis pub/sub invalidation).

---

## 4. End-to-end scenario — the "story of a claim"

This is the walkthrough that ties everything together. Use it to prove the platform works end-to-end.

**Scenario: `CLM-1001` (duplicate invoice suspicion) gets triaged, approved, resolved, and feeds retrain.**

| Step | Persona | Action | Where to see evidence |
|---|---|---|---|
| 1 | Data Scientist | Open the claims dataset, see `INV-5540` appearing twice in the profile | `/data/datasets/:id/profile` |
| 2 | Data Scientist | Open Claims Insights dashboard, note the "duplicate-invoice suspects" tile | `/dashboards` |
| 3 | Adjuster | Open `CLM-1001` in `/cases`, read the seeded copilot proposal citing the duplicate | `/cases/CLM-1001` |
| 4 | Adjuster | Ask copilot "why is this a duplicate?" — see real Ollama streaming | Copilot drawer |
| 5 | Manager | Log in, go to `/inbox`, approve the proposal | `/inbox` |
| 6 | Manager | Watch case status update live in `/cases` without reload | `/cases` (SSE) |
| 7 | Manager | Open `/admin/usage` → verify token metering rows for the proposal | `/admin/usage` |
| 8 | Admin | Open `/admin/audit` → find the proposal decision with actor + via_agent attribution | `/admin/audit` |
| 9 | (Automatic) | The disposition feeds a labeled example into the label store | `logs/agent.log` shows `case.disposition_applied` |
| 10 | (Automatic, if `--no-retrain` not passed) | A retrain pipeline promotes a new model version | `/ml/experiments` → new run visible |

If all 10 steps produce real, non-mock evidence, the platform is walkable end-to-end. **This is exactly what `deploy/e2e/driver.py` verifies programmatically** — run it with `make e2e` to see the scripted version.

## 5. What you WON'T see (design-only, not yet in code)

Per [BUILD_STATUS.md](BUILD_STATUS.md) §3.1 these are queued for build but not yet in the running system:

- **`CopilotHome` full-height chat as workspace home** (UI-FR-060..062) — you'll land on a functional but not-yet-chat-first home.
- **`<DecisionFooter>` + `<CostChip>` + `<RoiChip>`** on charts and cases (UI-FR-076..078) — you'll see charts but not the per-decision cost+ROI footer.
- **`<Label>` primitive + `<UrnLink>` human labels** (UI-FR-070..072) — you may see raw URNs in some places (design-only display_labels + BRD 21 §BFF-FR-080..088).
- **Per-decision cost aggregation** (USG-FR-080..086) — `/admin/usage` shows totals but not per-case/per-proposal attribution.
- **ai-gateway cost mechanisms** (AIG-FR-080..092) — deterministic-first pre-router, auto-cascade, distillation candidates stream, workflow budgets. The gateway runs, but these advanced routing behaviors are not present yet.
- **`<CommandPalette>` (Cmd/Ctrl-K global search)** (UI-FR-079..080) — not yet wired.
- **Pack install flow** (BRD 23 pack-service + BRD 24 insurance-claims-payer pack) — `make up` seeds vertical data via a script, not via a signed pack install through pack-service. That's the whole point of the pack model, but it's design-only today.

None of these gaps break the story arc; they're the next wave of build work.

## 6. Verifying the invariants after each step

**No fakes in the runtime path.** This is the master rule (see `CONVENTIONS.md`). To verify:

```bash
# Real Ollama call latency (should be visible in logs)
grep -c "ollama" logs/ai-gateway.log
grep -c "gen_ai.usage" logs/ai-gateway.log

# Real events (should stream continuously)
docker exec -it $(docker ps -qf name=redpanda) rpk topic consume case.events.v1 --num 3

# Real database writes
docker exec -it $(docker ps -qf name=postgres) psql -U windrose -d case_svc \
  -c "SELECT id, status, created_at FROM cases ORDER BY created_at DESC LIMIT 5;"

# Real OpenSearch index
curl -s 'http://localhost:9200/cases/_search?size=3' | python3 -m json.tool
```

**Every decision is audited.** Cross-check any action in `/admin/audit`. If an action doesn't appear there within ~5s, something is broken.

**Cross-tenant isolation.** From a browser terminal or via `curl`, forge a request to another tenant's ID — should return 404 (not 403 — that's a MASTER-FR-003 requirement).

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Login page returns "unknown user" | `WINDROSE_PERSONAS` env not injected into ui-web | Kill ui-web (`kill $(cat deploy/local/run/pids/ui-web.pid)`) and re-run `make up` |
| Home shows only Home + Copilot; no other nav | rbac projection didn't materialize (empty `perm:*` in Redis) | Check `logs/rbac.log` for projection worker errors. Re-run `deploy/local/seed_platform.py` |
| Copilot response is generic, no tokens streaming | Ollama unreachable or model missing | `curl http://localhost:11434/api/tags` and confirm `llama3.2:latest` present. Re-pull if missing. |
| Case list is empty | Vertical seed didn't run (--platform-only or claims seed failed) | Run `python3 deploy/local/seed_claims_demo.py` from the repo root |
| Proposal Approve returns 403 for manager | Manager missing `agent.proposal.decide` capability | Verify with `curl -H "Authorization: Bearer <token>" http://localhost:8302/api/v1/me/capabilities` |
| Kill switch UI visible but toggle doesn't propagate | agent-runtime authz gap (see the memory: eval-service + ai-gateway admin actions missing from rbac catalog) | Check `logs/agent-runtime.log`; if 403 on kill-switch route, the persona's `tenant.admin` scope claim is missing |
| E2E driver fails halfway | Any of the above + eventual consistency lag | `make e2e-keep` leaves services running; use `logs/*.log` to trace the exact failure point |

**Log locations:** every service writes to `logs/<service>.log`. Tail them all in parallel with `tail -f logs/*.log`.

## 8. Tear-down

```bash
make down                        # services only (leaves infra + data)
make down ARGS=--infra           # services + Docker infra (cleaner slate)
```

Docker volumes for Postgres/OpenSearch/MinIO persist by default. Nuke them with `docker compose -f deploy/docker-compose.dev.yml down -v` if you want a truly clean state.

## 9. Where to go next

- **Watch the scripted e2e journey run**: `make e2e-keep` — this is the same story you just walked, but automated with assertions at every step.
- **Read a service's own README**: each service in `services/*/README.md` has a FR-by-FR "what's done, what's stubbed" checklist tied to its BRD.
- **Add a new persona or role**: edit `deploy/local/seed_platform.py` `PERSONAS` map + `ROLE_GROUPS`, re-run `make up`.
- **Prep the next wave**: [BUILD_STATUS.md](BUILD_STATUS.md) §3 lists the design-only backlog and the critical path to first design-partner pilot readiness.
- **Understand why we chose this shape**: `../WINDROSE_STRATEGY.md` (the founding thesis), `../WINDROSE_CORE_CAPABILITIES.md` (what's Core vs pack), `../WINDROSE_MODEL_STRATEGY.md` (SLM-first, watch-don't-chase).
