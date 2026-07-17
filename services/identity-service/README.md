# identity-service

Windrose platform root of trust (BRD 01): tenant lifecycle + compensable
provisioning, user directory + invitations, service accounts / API keys,
agent principals + OBO token issuance, JWKS publication + key rotation.

Go 1.26 · chi · pgx · golang-migrate · distroless. Inherits every contract in
`docs/brd/00_MASTER_BRD.md` (error envelope, cursor pagination, idempotency
keys, transactional outbox, RLS, URNs, JWT claims).

## Run

```bash
make build            # bin/identity-service
make run              # in-memory store, local RSA signer, fake adapters
DATABASE_URL=postgres://user:pw@localhost:5432/identity?sslmode=disable make run
                      # postgres store; migrations auto-apply at boot
docker build -t identity-service .   # distroless image
```

Environment: `DATABASE_URL`, `LISTEN_ADDR` (default `:8080`), `KEYCLOAK_URL`
(+`KEYCLOAK_ADMIN_TOKEN`) to enable the real Keycloak adapter,
`TRUSTED_SPIFFE_IDS` (comma-separated; callers of `POST /token/agent`).

Production note: connect with a `NOSUPERUSER NOBYPASSRLS` role — superusers
bypass row-level security. The integration suite tests through such a role.

## Test

```bash
make test-unit          # tier 1 — no external dependencies
make test-integration   # tier 2 — Docker (testcontainers Postgres); auto-skips without Docker
make test               # both
make lint               # golangci-lint if installed, else go vet (incl. integration tag)
```

## Layout

```
cmd/server/           entrypoint + wiring
internal/domain/      entities, state machine, validation, token rules,
                      provisioning engine, store/adapters ports (interfaces)
internal/keys/        Signer port, local RSA signer, KeyManager (rotation
                      overlap), JWT issuer/verifier (RS256-only), JWKS
internal/store/       memory (unit tier) + postgres (pgx, RLS per tx)
internal/api/         chi router, middleware (trace, auth, idempotency),
                      handlers, error envelope, pagination
internal/adapters/    keycloak (fake + HTTP), terraform (fake),
                      vault (REAL transit signer), denylist (memory + REAL redis)
internal/authz/       Authorizer port: ScopeAuthorizer (active), OPA adapter
internal/events/      outbox poller + publisher port (log publisher active)
internal/temporalwf/  real Temporal workflow skeleton (compiles, not wired)
migrations/           forward-only SQL (embedded; RLS policies in 0002)
api/openapi.yaml      REST contract        events/identity_event.avsc  event schema
test/integration/     testcontainers suite (build tag `integration`)
```

## Security review fixes (post-review hardening)

| ID | Fix | Where / test |
|---|---|---|
| F-1 | Restart-safe signing: `KeyManager.Bootstrap` probes whether the configured signer can actually sign with the active kid; if not (LocalSigner loses in-memory keys on restart) it mints an immediately-usable key so token issuance never bricks. A preferred-kid tie-break keeps `SigningKey` from picking the stale unmintable key on a `not_before` tie. | `keys/keys.go` · `TestF1_RestartTokenIssuance` (integration, double-boot), `TestBootstrapRestartMintsUsableKey` (unit) |
| F-2 | `X-Spiffe-Id` is honored only when `TrustSpiffeHeader` (env `TRUST_SPIFFE_HEADER`) is explicitly true; default false drops the header so agent-autonomous minting is refused. | `api/middleware.go`, `server.go` · `TestF2_SpiffeHeaderUntrustedByDefault` |
| F-3 | `GET /tenants/{id}` now requires an admin scope; a zero-scope token can no longer read registry internals. | `api/server.go` · `TestF3_GetTenantRequiresAdminScope` |
| F-4 | Idempotency records are namespaced by acting subject as well as tenant, so super-admins (shared `tenant_id=Nil`) cannot cross-replay each other's responses. | `api/idempotency.go` · `TestF4_IdempotencyScopedPerSubject` |
| F-5 | `typ` allowlist on identity admin endpoints: only `user`/`service` tokens may administer; an `agent_obo`/`agent_autonomous` token with an admin-looking scope string is rejected before ScopeAuthorizer. | `api/middleware.go` · `TestF5_AgentTypeRejectedOnAdminEndpoints` |
| F-6 | Business-logic coverage raised to **80.4%** (from 76.1%) — added handler, poller, scheduled-deletion, and user-lifecycle tests. | — |

## Shared plumbing (`libs/go-common`)

The cross-cutting adapters are now the real shared implementations in
`libs/go-common` (wired via the repo-root `go.work`): `kafka` (producer +
consumer group + Schema Registry, over Redpanda), `outbox` (relay), `authjwt`
(JWKS verifier + middleware), `opaclient` (OPA decision client), `redisx`,
`otelx`, and `httpx` (error envelope + cursor pagination). identity's event
publishing, denylist, and JWKS-consumer contracts run through them; no runtime
stub remains for Kafka, the denylist, or JWT signing.

## Adapter / stub inventory (pragmatic build scope)

| Concern | Interface (port) | Active implementation | Production adapter status |
|---|---|---|---|
| Keycloak realms/users | `domain.KeycloakAdmin` | `keycloak.Fake` (tests, dev) | `keycloak.HTTPAdmin` — real Admin-REST calls, compiles, **untested against live Keycloak**; enabled via `KEYCLOAK_URL` |
| JWT signing | `keys.Signer` | **`vault.TransitSigner` — REAL Vault transit (RSA-2048; private keys never leave Vault; RS256 pkcs1v15/sha2-256), used when `VAULT_ADDR` is set.** `keys.LocalSigner` is the dev/test fallback | Real. Wired via `libs/go-common`; proven by `TestVaultTransitSigner_GenerateAndSign` + `TestVaultSignedToken_VerifiesViaGoCommonJWKS` (integration, live Vault + cross-service JWKS verify) |
| Provisioning workflow | `domain.ProvisioningEngine` | `domain.Engine` — deterministic in-process: 7 steps, 5 attempts/step w/ exp backoff, compensation stack, resumable via `provisioning_runs` markers | `temporalwf.Engine` — real Temporal SDK workflow skeleton (saga, REJECT_DUPLICATE id `provision-<tenant>`), compiles, **not wired to a cluster (TODO)** |
| Terraform runner | `domain.TerraformRunner` | `terraform.Fake` (scriptable failures) | real per-cloud runner lives in infra repo (contract = `domain.TerraformInputs`, TODO) |
| Tenant DB schemas (step 4) | `domain.DatabaseProvisioner` | `terraform.FakeDBProvisioner` | TODO |
| Health probe (step 7) | `domain.HealthProber` | `terraform.FakeProber` | TODO (synthetic login probe) |
| API-key denylist | `domain.Denylist` | **`denylist.Redis` — REAL, wired to Redis via `go-common/redisx` when `REDIS_ADDR` is set (≤5s propagation, IDN-FR-033).** `denylist.Memory` is the single-replica dev/test fallback | Real; proven by `TestRedisDenylist_RevokeRoundtrip` (integration, live Redis) |
| Authorization | `authz.Authorizer` | `authz.ScopeAuthorizer` — enforces JWT action scopes; `platform.admin` = super-admin | `authz.OPAAuthorizer` — sidecar call per MASTER-FR-012, compiles, **untested** |
| Kafka publish | `events.Publisher` | **`events.KafkaPublisher` — REAL, over the shared `go-common/kafka` producer (Redpanda), draining the outbox poller; envelope keyed by `tenant_id`, subject registered in Schema Registry (MASTER-FR-030/031). Default runtime path (`KAFKA_BROKERS` defaults to `localhost:9092`; `=false` selects the log stub for broker-less local dev).** `events.LogPublisher` retained only as that escape hatch | Real; the shared producer is proven by go-common's `TestSchemaRegistryRegisterAndPublishConsume` / dedup / DLQ integration tests |
| Agent-registry consume | `TokenService.ApplyAgentEvent` | called directly (tests) | TODO Kafka consumer for `agent.events.v1` |
| OBO rate limit | `domain.RateLimiter` | in-memory sliding window (per instance) | TODO Redis window for multi-replica |
| Last-admin guard (BR-9) | `domain.LastAdminChecker` | `AllowAllLastAdminChecker` (rbac-service projection not built yet) | TODO rbac-service projection |
| SPIFFE mTLS (FR-030/042) | `X-Spiffe-Id` header + allowlist | mesh is assumed to terminate mTLS and inject the header | TODO verify against mesh XFCC header |

Other documented deviations:
- **Invitation acceptance**: production flips `invited→active` on first SSO
  login (IDN-FR-021); the accept endpoint models that callback and receives
  the Keycloak subject in the body.
- **Quota-change resize workflow** (PATCH quotas) records the new quotas but
  does not launch a resize workflow (TODO).
- **Grace-period deletions** run from an in-process sweep loop
  (`RunScheduledDeletions`, 1-min tick) rather than a Temporal timer.
- **Metrics/OTel**: `/metrics` is a text stub; full OTel wiring TODO
  (MASTER-FR-050/051). `/healthz`, `/readyz` implemented.
- **outbox RLS**: outbox has tenant policy for reads + a `app.role=platform`
  policy for the poller; inserts are unrestricted (they only occur inside
  service-controlled transactions).
- IDN-FR-009 (S) platform-version registry: table + `GET /platform-versions`
  returns 501 NOT_IMPLEMENTED. IDN-FR-024 (S) SCIM: 501. IDN-FR-011 (C)
  tenant export: not built.

## FR traceability (Must + Should)

| FR | Status | Where (code / test) |
|---|---|---|
| IDN-FR-001 tenant entity | ✅ | `domain/tenant.go`, `migrations/0001` · `TestAC01` |
| IDN-FR-002 name rules + derivation + reserved | ✅ | `NormalizeTenantName` · `TestNormalizeTenantName`, `TestAC04` |
| IDN-FR-003 guarded state machine, 409 | ✅ | `domain/tenant.go` transitions + CAS `TransitionTenant` (both stores) · `TestTenantTransitionMatrix`, `TestStateMachinePersistence` (PG), `TestBR2ConcurrentPublishRejected` |
| IDN-FR-004 quota defaults + validation | ✅ | `DefaultQuotas`, `TenantService.Create/Patch` (cell-capacity check at AssignCell) |
| IDN-FR-005 module dependency graph | ✅ | `ModuleGraph.Resolve` · `TestModuleGraphResolve` |
| IDN-FR-006 7-step workflow, retry, compensation | ✅ (in-process engine; Temporal skeleton) | `domain/provisioning.go`, `engine_steps.go`, `temporalwf/` · `TestEngineHappyPath`, `TestEngineRetriesWithBackoffThenFails`, `TestEngineCompensationStack` |
| IDN-FR-007 provision_failed + queryable steps | ✅ | `GET /tenants/:id/provisioning` · `TestAC02` |
| IDN-FR-008 delete archive/destroy, grace, cascade | ✅ | `TenantService.Delete`, `DestroySteps` · `TestAC09`, `TestEngineDeprovisionStaysDeletingOnDestroyFailure` |
| IDN-FR-009 (S) version registry | 🔶 stub 501 | `handleNotImplemented` |
| IDN-FR-010 step-completed events | ✅ | engine `notify` → outbox `tenant.provision_step_completed` (main.go) |
| IDN-FR-011 (C) tenant export | ❌ out of build scope | — |
| IDN-FR-020 user entity + email validation | ✅ | `domain/user.go` · `TestValidateEmail` |
| IDN-FR-021 invite flow, 7-day token, resend | ✅ | `UserService.Invite/ResendInvite/AcceptInvitation` · `TestAC05` |
| IDN-FR-022 deactivation, session revoke, OBO cutoff | ✅ | `UserService.Deactivate` · `TestAC06` |
| IDN-FR-023 soft delete + user.deleted | ✅ | `UserService.SoftDelete` |
| IDN-FR-024 (S) SCIM | 🔶 stub 501 | router |
| IDN-FR-025 super-admin, platform scope audit | ✅ | `requireSuperAdmin`, `actorFrom` scope=platform |
| IDN-FR-030 SPIFFE for services | ✅ (mesh-header adapter) | `spiffeMiddleware` · `TestAutonomousToken` |
| IDN-FR-031 API keys, argon2id, shown once, max 20 | ✅ | `domain/serviceaccount.go`, `ServiceAccountService` · `TestAPIKeyRoundtrip`, `TestServiceAccountLimit` |
| IDN-FR-032 edge exchange → typ=service JWT | ✅ | `POST /token/apikey` · `TestAC10/11` |
| IDN-FR-033 rotation overlap, denylist, last-used | ✅ | `Rotate`, `Revoke`, denylist · `TestSARotationOverlap`, `TestAC11` |
| IDN-FR-034 no auth bypass switches | ✅ | no env bypass exists; all endpoints authenticated except documented public ones |
| IDN-FR-040 agent principals from events only | ✅ | `ApplyAgentEvent` (no manual API) |
| IDN-FR-041 OBO exchange, full claim spec | ✅ | `TokenService.OBOExchange` · `TestOBOTokenClaims` |
| IDN-FR-042 autonomous tokens (SPIFFE, allowed flag) | ✅ | `AutonomousToken` · `TestAutonomousToken` |
| IDN-FR-043 refusal rules (deactivated/suspended/killed/eval) | ✅ | `IssuableOBO`, `tenantIssuable` · `TestAC06/07/10` |
| IDN-FR-044 token.obo_issued + 60/min rate limit | ✅ | outbox event + `SlidingWindowLimiter` · `TestAC14`, `TestOBOTokenClaims` |
| IDN-FR-045 alg=none banned, RS256 only | ✅ | `keys.Issuer.Verify` (`WithValidMethods`) · `TestVerifyRejectsAlgNone`, `TestAC13` |
| IDN-FR-050 signing keys via Signer port | ✅ dev / 🔶 Vault stub | `keys/`, `adapters/vault` |
| IDN-FR-051 JWKS endpoint, max-age=300 | ✅ | `handleJWKS` · `TestAC08` |
| IDN-FR-052 rotation overlap ≥10min, retire after TTL+skew | ✅ | `KeyManager.Rotate` · `TestRotationOverlap`, `TestAC08` |
| IDN-FR-053 per-tenant IdP via Keycloak | ✅ by design (realm-per-tenant; config in Keycloak, out of service logic) | `CreateRealm` step |
| MASTER-FR-001 RLS everywhere | ✅ | `migrations/0002` (FORCE + policies) · `TestRLSIsolation` |
| MASTER-FR-003 cross-tenant → 404 + audit | ✅ | stores + `handleGetTenant` · `TestAC12`, `TestAPITenantIsolationOnPostgres` |
| MASTER-FR-022 cursor pagination | ✅ | `domain/pagination.go` · `TestCursorPagination` |
| MASTER-FR-024 error envelope | ✅ | `api/respond.go` (asserted in every AC test via `errCode`) |
| MASTER-FR-025 idempotency keys | ✅ | `api/idempotency.go` · `TestIdempotencyReplay` |
| MASTER-FR-034 transactional outbox | ✅ | store `evs ...OutboxEvent` in-tx · `TestOutboxTransactional` (PG) |

## AC traceability

| AC | Test | Tier |
|---|---|---|
| AC-1 | `TestAC01_ProvisioningHappyPath` | unit |
| AC-2 | `TestAC02_ProvisionFailureAfterRetries` (+`TestEngineCompensationStack`) | unit |
| AC-3 | `TestAC03_RetryResumesFromFailedStep` (+`TestProvisioningPersistenceAndResume` on PG) | unit + integration |
| AC-4 | `TestAC04_DuplicateNameCaseInsensitive` | unit |
| AC-5 | `TestAC05_ExpiredInvitationAndResend` | unit |
| AC-6 | `TestAC06_DeactivatedUserOBORefused` | unit |
| AC-7 | `TestAC07_KillSwitchDisablesOBO` (event applied directly; Kafka consumer is a stub, latency bound not testable) | unit |
| AC-8 | `TestAC08_KeyRotationOverlap` (+`TestRotationOverlap`) | unit |
| AC-9 | `TestAC09_DestroyNeverCompletesWithoutTerraform` | unit |
| AC-10 | `TestAC10_SuspendedTenantBlocksIssuance` | unit |
| AC-11 | `TestAC11_RevokedAPIKeyRejected` (in-memory denylist is instant; ≤5s Redis propagation not testable here) | unit |
| AC-12 | `TestAC12_CrossTenantTenantReadIs404` + `TestAPITenantIsolationOnPostgres` | unit + integration |
| AC-13 | `TestAC13_AlgNoneRejected` + `TestVerifyRejectsAlgNone` | unit |
| AC-14 | `TestAC14_OBORateLimit` | unit |

Mandatory suites (MASTER-FR-071): `TestTenantIsolationSuite` (unit, in-memory)
+ `TestRLSIsolation`/`TestAPITenantIsolationOnPostgres` (integration, real RLS);
authz matrix: `TestAuthzMatrix`.
