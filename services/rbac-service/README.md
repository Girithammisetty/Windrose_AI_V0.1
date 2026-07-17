# rbac-service

Windrose's authorization data plane (BRD 02). Owns workspaces, groups,
memberships, roles, the platform action catalog and content grants, and
**materializes flattened permissions into Redis** (`permissions_flat`) so every
other service authorizes locally via its OPA sidecar in O(1) — rbac-service is
never in the request path (RBC-FR-040..048).

## Run

```bash
# deps for local dev
docker run -d -p 5432:5432 -e POSTGRES_USER=rbac -e POSTGRES_PASSWORD=rbac -e POSTGRES_DB=rbac postgres:16
docker run -d -p 6379:6379 redis:7

export DATABASE_URL="postgres://rbac:rbac@localhost:5432/rbac?sslmode=disable"
export REDIS_ADDR="localhost:6379"
export AUTH_JWKS_URL="https://identity.local/realms/windrose/protocol/openid-connect/certs"  # or AUTH_PUBLIC_KEY_PEM for dev
export KAFKA_BROKERS="localhost:9092"   # optional; in-memory publisher without it
make run
```

Config (env): `DATABASE_URL`, `MIGRATE_DATABASE_URL` (schema-owner URL; app URL
should be a plain role subject to RLS), `REDIS_ADDR`, `KAFKA_BROKERS`,
`CONSUME_TOPICS`, `AUTH_JWKS_URL` | `AUTH_PUBLIC_KEY_PEM`, `AUTH_ISSUER`,
`AUTH_AUDIENCE`, `LISTEN_ADDR`, `RUN_MIGRATIONS`.

Binaries: `cmd/server` (API + projection worker + outbox relay + consumers),
`cmd/rebuild` (full per-tenant projection rebuild + verify, RBC-FR-043).

## Test

```bash
make test-unit          # no external deps (flattening, decide, Rego parity, domain, events)
make test-integration   # Docker required: Testcontainers Postgres + Redis; auto-skips without Docker
make test               # both tiers
make vet lint
```

## permissions_flat projection — design notes

**Key scheme** (RBC-FR-040; values are JSON carrying `v` + `computed_at`):

```
perm:{tenant}:{user}:actions    {"v":17,"computed_at":"...","actions":["rbac.group.list",...]}
perm:{tenant}:{user}:ws:{ws}    {"v":17,"actions":[...],"archived":false}     # absent => not assigned
perm:{tenant}:{user}:res:{h}    {"v":17,"urn":"wr:...","level":"editor","archived":false}  # h = sha256(urn)[:32]
perm:{tenant}:{user}:flags      {"v":17,"admin":false,"ws_admin":["<ws>"]}
perm:{tenant}:{user}:index      bookkeeping: subsidiary keys, for stale-key GC
perm:{tenant}:archived_ws       tenant-level archived workspace ids (admin write-block, BR-7)
perm:{tenant}:meta              {"autonomous_enabled":false}
perm:catalog:actions            action -> workspace_scoped (global, no TTL)
```

**Flattening** (`internal/projection/flatten.go`, pure function per RBC-FR-041):
union(permission groups → roles → actions) → split by catalog
`workspace_scoped` → workspace-scoped set intersected with assigned workspaces
(public ∪ content-group-linked, RBC-FR-003); archived workspaces keep only
read/list/export; resource grants overlaid as level-per-URN-hash; the admin
flag is carried, never expanded, and short-circuits at decision time
(tenant-bound; it does NOT bypass the archived-write block or last-admin rule,
BR-7). Deny-by-default, additive only — no negative grants.

**Pipeline** (RBC-FR-042/048): every mutation writes, in the same transaction,
its rows + an outbox event + `projection_dirty` markers for affected users
(transactional outbox). The recompute worker claims dirty rows with
`FOR UPDATE SKIP LOCKED` + a 30s visibility timeout (crashed claims are
reclaimed, at-least-once), collapses all pending rows per user into one
recompute, then takes a **per-user Redis mutex** (`internal/projection/lock.go`)
and loads its SQL snapshot *under the lock* so two workers can never interleave
load/write for the same user (SKIP-LOCKED row claiming alone does not prevent
this — a user can have rows enqueued at different times claimed by different
workers). The snapshot allocates a monotonic version from
`projection_version_seq`, and Redis is written through Lua compare-and-set
scripts — **versioned last-writer-wins**. Subsidiary keys (`ws:{id}`,
`res:{hash}`) that drop out of a newer snapshot are removed by writing a
**version-carrying tombstone**, never a raw `DEL`: a raw delete would leave no
value for a later *stale* writer's CAS to compare against, so an older snapshot
could recreate a key a newer snapshot had removed — resurrecting a revoked
grant or workspace assignment for up to the TTL. The tombstone keeps the
version present, so the stale recreate is blocked exactly as an in-place
overwrite is, and the reader treats a tombstone as key-absent. After each batch
it publishes `perm.invalidate {tenant, users[]}` on
Redis pub/sub and records enqueue→write staleness in
`rbac_projection_staleness_seconds` (SLO ≤5s p99, alerting SLI).

**Self-healing** (RBC-FR-045/047): entries carry a 24h TTL; the `RedisReader`
re-marks users dirty when it observes <1h TTL (refresh-on-read). On a miss,
OPA falls back to `POST /authz/check`, which evaluates a fresh SQL snapshot
**through the exact same `Decide` code** and warms the keys — fallback and
projection semantics cannot diverge by construction. Fallback volume is
`rbac_authz_fallback_total`. Weekly verification (`/admin/projection/verify`,
`rebuild -verify`) diffs sampled users against SQL ground truth and repairs
drift (AC-12).

**Decision paths** (RBC-FR-044): `internal/authz/decide.go` is the reference
implementation of the OPA data contract; `policy/windrose_authz.rego` mirrors
it and is tested for parity through the OPA Go SDK with the same case table.
Both fail closed on an **unknown principal `typ`** (explicit default-deny in
the Go switch; no `user_path` in Rego) so they cannot diverge. OBO agents
evaluate the original user's projection intersected with token scopes (BR-6);
autonomous agents require scope + tenant enablement flag. The `/authz/check`
fallback binds the request `tenant` to the caller's token (service tokens may
only check their own tenant; super-admins may check any), never trusting a
body-supplied tenant for authorization (MASTER-FR-002).

## FR traceability

| FR | Status | Code | Tests |
|---|---|---|---|
| RBC-FR-001 workspace model, per-tenant CI-unique names | Done | `migrations/000001`, `store/workspaces.go` | `TestIntegration_WorkspaceNameUniquePerTenant` |
| RBC-FR-002 visibility rule | Done | `Store.GetWorkspace/ListWorkspaces` | `TestAC01`, `TestAC02` |
| RBC-FR-003 assignment rule | Done | `Store.LoadSnapshot` (step 4), `Flatten` | `TestFlatten_*`, `TestAC01` |
| RBC-FR-004 archive/restore, filters, write-block, reads live | Done | `Store.ArchiveWorkspace/RestoreWorkspace` | `TestAC14_ArchivedWorkspaceSemantics` |
| RBC-FR-005 no hard delete (archive terminal; purge = retention job) | Done | no DELETE route | `api/openapi.yaml` |
| RBC-FR-006 default workspace `Default use case` | Done | `Store.SeedTenant` | harness `newTenant`, `TestAC02` |
| RBC-FR-007 (S) workspace metadata icon/tags | Stubbed | not implemented (Should) | — |
| RBC-FR-008 workspace events | Done | `events.EvWorkspace*` via outbox | `TestOutboxRelay_*` |
| RBC-FR-010 two-kind group model, unique names | Done | `store/groups.go` | `TestIntegration_*`, migrations |
| RBC-FR-011 unique membership | Done | `uq_members_group_user` | `TestAC09_DuplicateMemberIdempotent` |
| RBC-FR-012 link uniqueness; delete cascades links + grants | Done | FKs `ON DELETE CASCADE` | `TestAC10_GroupDeleteCascadesGrants` |
| RBC-FR-013 system groups seeded per role, immutable | Done | `Store.SeedTenant`, immutability guards | `TestIntegration_RoleLifecycle`, `TestAC06` |
| RBC-FR-014 auto-generated groups hidden | Done | `ListGroups(includeAuto)` | covered via handler param |
| RBC-FR-015 last-admin + last-owner protection, audited override | Done | `RemoveMember`, `DeleteGrant` | `TestAC06_LastAdminProtection` |
| RBC-FR-016 bulk membership ≤500 + partial report | Done | `Store.BulkMembers` | `TestIntegration_BulkMembers` |
| RBC-FR-017 (S) membership expiry | Partial | `expires_at` stored + honored in snapshot/queries; Temporal timer (BR-11) not wired | `TestFlatten_*` (expiry in loader SQL) |
| RBC-FR-020 10 system roles, V1 names + mapping | Done | `domain.SystemRoleNames`, `seed/roles_actions.yaml` | `TestSystemRoleCatalog`, `TestRoleSeedsShipValid` |
| RBC-FR-021 custom roles, delete-when-unassigned | Done | `store/roles.go` | `TestIntegration_RoleLifecycle` |
| BRD §4 group_roles = permission groups only | Done | app-layer `BindGroupRole` **+ DB composite-FK/CHECK** (`migrations/000003`) | `TestIntegration_RoleLifecycle` |
| RBC-FR-022 action catalog, static registration, workspace_scoped | Done | `domain/catalog.go`, `POST /actions/register` | `TestCanonicalCatalog`, `TestAC03` |
| RBC-FR-023 versioned bindings, role.updated diff | Done | `Store.SetRoleActions` | `TestAC04_RoleEditPropagatesWithin5s` |
| RBC-FR-024 seed role→action matrix | Done | `seed/roles_actions.yaml`, `EnsureSystemRoles` | `TestDecide_SystemRoleMatrix` |
| RBC-FR-025 (C) role templates | Not implemented (Could) | — | — |
| RBC-FR-030 grants with levels, unique tuple | Done | `store/grants.go`, `domain/levels.go` | `TestDecide_LevelVerbMapping` |
| RBC-FR-031 group-in-workspace integrity + sweep | Done | `CreateGrant`, `SweepOrphanGrants` | `TestAC05_GrantIntegrity`, `TestAC10` |
| RBC-FR-032 implicit creator grant | Done | `CreateImplicitOwnerGrant` + consumer | `TestAC13_ImplicitCreatorGrant` |
| RBC-FR-033 URN-based grants | Done | `domain/urn.go` | `TestURN` |
| RBC-FR-034 effective-access list with provenance | Done | `Store.EffectiveAccess` | `TestAC13`, `TestIntegration_ExplainFullChain` |
| RBC-FR-035 (S) public-link sharing | Not implemented (flag-gated Could) | — | — |
| RBC-FR-040 Redis projection key scheme | Done | `projection/keys.go`, `redis.go` | `TestIntegration_ProjectionRecomputeEndToEnd` |
| RBC-FR-041 flattening algorithm | Done | `projection/flatten.go` | `TestFlatten_*` (exhaustive table-driven) |
| RBC-FR-042 outbox dirty-marking, worker, pub/sub, ≤5s, staleness SLI | Done | `opctx.go`, `projection/worker.go` | `TestAC01`, `TestAC04`, `TestIntegration_InvalidationPubSub` |
| RBC-FR-043 full rebuild + weekly verification | Done | `cmd/rebuild`, `/admin/projection/{rebuild,verify}` | `TestIntegration_ProjectionRebuild`, `TestAC12` |
| RBC-FR-044 OPA decision contract + policy bundle | Done | `authz/decide.go`, `policy/windrose_authz.rego` | `TestDecide_*`, `TestPolicy_*` (parity) |
| RBC-FR-045 cold-start fallback + warm + alert metric | Done | `authz/checker.go` | `TestAC08_RedisFlushFallback` |
| RBC-FR-046 explain chain | Done | `authz/explain.go` | `TestIntegration_ExplainFullChain`, `TestAC07` |
| RBC-FR-047 TTL 24h + refresh-on-read | Done | `RedisWriter` TTL, `RedisReader.OnNearExpiry` | `TestIntegration_ProjectionRecomputeEndToEnd` (TTL assert) |
| RBC-FR-048 idempotent ordered workers, per-user mutex, versioned LWW, no resurrection | Done | `ClaimDirty` (SKIP LOCKED + visibility), `projection.UserLock`, Lua CAS + version-carrying tombstones | `TestIntegration_VersionedLastWriterWins`, `TestIntegration_StaleWriterCannotResurrectRevokedGrant`, `TestIntegration_StaleWriterCannotResurrectWorkspaceAssignment`, `TestIntegration_UserLockSerializesRecompute` |

Master-BRD: RLS per-request tenant pinning (`store.WithTenant`), 404-not-403
(`TestAC11`), UUIDv7 ids, cursor pagination, error envelope, Idempotency-Key
replay (`TestIntegration_IdempotencyReplay`), transactional outbox, JWT RS256
with `alg=none` rejected, `/healthz` `/readyz` `/metrics`.

## AC traceability

| AC | Test |
|---|---|
| AC-1 | `TestAC01_WorkspaceVisibilityFollowsGroupLink` |
| AC-2 | `TestAC02_PublicWorkspaceRoleAccess` |
| AC-3 | `TestAC03_WorkspaceContextValidation` (+ unit `TestDecide_Matrix`, Rego) |
| AC-4 | `TestAC04_RoleEditPropagatesWithin5s` |
| AC-5 | `TestAC05_GrantIntegrity` |
| AC-6 | `TestAC06_LastAdminProtection` |
| AC-7 | `TestAC07_OBOScopeExcluded` (+ unit `TestDecide_OBOIntersection`, Rego) |
| AC-8 | `TestAC08_RedisFlushFallback` |
| AC-9 | `TestAC09_DuplicateMemberIdempotent` |
| AC-10 | `TestAC10_GroupDeleteCascadesGrants` |
| AC-11 | `TestAC11_CrossTenantIs404` |
| AC-12 | `TestAC12_VerifyDetectsAndRepairsDrift` |
| AC-13 | `TestAC13_ImplicitCreatorGrant` |
| AC-14 | `TestAC14_ArchivedWorkspaceSemantics` |

## Shared plumbing (`libs/go-common`)

Cross-cutting adapters now run through the real shared packages in
`libs/go-common` (wired via the repo-root `go.work`). rbac's outbox relay and
consumer DLQ publish through `events.GoCommonPublisher`, backed by the shared
`go-common/kafka` producer (Redpanda, envelope keyed by `tenant_id`, subject
registered in Schema Registry). The **decision path** is exercised end-to-end
against the real OPA container: `libs/go-common/opaclient` reads a user's
`permissions_flat` slice from Redis and evaluates the `windrose.authz_input`
Rego bundle, and `TestOPAContainerParityWithGoDecide`
(`internal/integration/opa_parity_test.go`) proves — over a case matrix — that
the OPA container and rbac's Go `Decide` return identical allow/deny+reason
for the same projection.

## Known deviations / stubs

- **Kafka**: the runtime publisher is `events.GoCommonPublisher` (shared
  `go-common/kafka` producer over Redpanda; default `KAFKA_BROKERS=localhost:9092`,
  `=false` selects the in-memory publisher for broker-less local dev). The
  in-memory `EventPublisher` fake remains **unit-test-only** (relay/consumer
  tests). Envelope is JSON matching `events/rbac_envelope.avsc`; the envelope
  Avro schema is registered as the topic-value subject in Schema Registry.
  Publish→consume→dedup→DLQ is container-tested in `go-common/kafka`.
- **Cross-tenant audit event** (MASTER-FR-003): RLS makes cross-tenant rows
  indistinguishable from nonexistent inside the app role, so the 404 is
  enforced but `security.cross_tenant_denied` cannot be attributed without a
  privileged lookup; deferred to the edge/audit-service correlation.
- **Temporal** (BR-11 expiry timers, nightly sweep, weekly verify schedules):
  the operations exist as store methods/admin APIs; scheduling is infra wiring.
  Expired memberships are already excluded from snapshots/queries at read time.
- **BR-5 deprecation window**: `Store.DeprecateAction` flips the flag and
  deprecated actions still evaluate (they stay in the catalog), but the
  `deprecated_action_used` usage log is a TODO pending the shared telemetry
  helper.
- Helm chart / dashboards-as-code (MASTER-FR-072): not in this service's wave-1
  scope; Dockerfile (distroless) + RUNBOOK.md ship here.
- `/authz/explain` requires `audit.log.read` (tenant admins hold it via the
  admin flag), standing in for a dedicated auditor role.
