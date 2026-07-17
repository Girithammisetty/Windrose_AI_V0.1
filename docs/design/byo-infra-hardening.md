# Design — BYO Infrastructure Hardening (Observability, Secrets, SIEM, Identity)

## Problem

Most enterprise customers already own security and observability infrastructure —
their own IdP (Okta/Auth0/Entra), their own secrets backend (AWS Secrets Manager/
Azure Key Vault/GCP Secret Manager), their own observability stack (Datadog/
Splunk/Grafana Cloud/New Relic), their own SIEM. Today Windrose only really works
against its own local stack (Keycloak, Vault, a dev OTel collector, ClickHouse-
backed audit). A customer evaluating Windrose for their own infrastructure needs
each of these to be a swap-in, not a rip-and-replace of their existing tooling.

## Current state (full audit — see project memory `project_windrose_byo_infra_readiness`)

The pattern is consistent across every area: **the seam is usually cut (a real
interface or config point exists), but only one implementation is written.** The
one fully-solved exception is infra-credential delivery (DB/Redis/Kafka/S3
passwords), which is genuinely cloud-agnostic already via Terraform + External
Secrets Operator.

| Area | State | Gap |
|---|---|---|
| Traces (OTel) | Code-level config-only (`OTEL_EXPORTER_OTLP_ENDPOINT` real env var, ~16 services) | Zero Helm/values wiring, no k8s collector template, undocumented |
| Metrics | Pull-based `/metrics`, backend-agnostic | No ServiceMonitor/scrape-discovery convention shipped |
| Logging | Go=JSON stdout, Python=plain stdout | No shared Python JSON formatter, no shipping config anywhere |
| Secrets (connector creds) | `SecretsStore` Protocol exists (`ingestion-service/app/domain/secrets.py`) | Only `VaultSecretsStore` implements it |
| Secrets (JWT signing) | `Signer` interface exists (`identity-service/internal/keys/keys.go`) | Only `LocalSigner`/`TransitSigner` (Vault) implement it |
| SIEM/audit export | audit-service writes to ClickHouse/Postgres/WORM-S3 only | No webhook, no SIEM-bound Kafka topic, no vendor connector |
| Policy engine (OPA) | `OPA_URL` config-only | Rego package/input schema is fixed, proprietary — can't bring your own policies or engine |
| Identity provider | `domain.KeycloakAdmin` — Keycloak-only interface | No generic `IdentityProvider` port, no SAML, no OIDC-discovery consumption, no real login flow (only `AUTH_MODE=dev` direct-mint) |

This doc scopes closing these gaps as four independent, incrementally-shippable
phases, ordered by ascending effort/blast-radius: **OTel wiring → secrets
adapters → SIEM export → IdP/OIDC**. Phases 1–3 have no dependency on each
other and can ship in any order or in parallel; Phase 4 is the largest lift and
is scoped last because it requires new interfaces (unlike 1–3, which extend
existing ones) and touches the login UX directly.

---

## Phase 1 — OTel wiring (packaging only, no new code)

**Goal**: make the observability egress that already works in code actually
reachable from a production Helm deploy, so a customer can point every service
at their own OTLP-compatible backend (Datadog, Honeycomb, Grafana Cloud, New
Relic, Splunk O11y) by setting Helm values — nothing else.

**Why this is cheap**: `go-common/otelx` and `py-common/windrose_common/otelx.py`
already read `OTEL_EXPORTER_OTLP_ENDPOINT` / `WINDROSE_OTEL_ENABLED` from env
with a real conditional no-op — this is done. The gap is entirely in
`deploy/helm/windrose/values.yaml` and docs.

**Increments**:
1. Add `OTEL_EXPORTER_OTLP_ENDPOINT` and `WINDROSE_OTEL_ENABLED` to
   `deploy/helm/windrose/values.yaml`'s `config:` block (flows to all 24
   services automatically via the existing `envFrom: configMapRef`).
2. Add per-cloud override examples in `values-{aws,gcp,azure}.yaml` (commented,
   pointing at nothing by default — opt-in).
3. Document the contract in `deploy/CONFIG.md`: which env vars, what a
   customer's OTel Collector needs to accept (OTLP/gRPC for Go, OTLP/HTTP
   acceptable for Python), and that metrics remain pull-based (`/metrics`) —
   ship a `ServiceMonitor` template (behind a `values.yaml` flag,
   `observability.serviceMonitor.enabled`) for Prometheus-Operator-based
   customers, since this is the standard k8s convention and currently absent.
4. Fix the dead RUM stub: either wire `NEXT_PUBLIC_OTEL_COLLECTOR_URL` in
   ui-web for real (web-vitals + client error export), or remove the unused
   `.env.example` entry so it stops implying a capability that doesn't exist.
5. Python logging: add a minimal JSON log formatter to `py-common` (mirroring
   Go's `slog.NewJSONHandler` convention, `MASTER-FR-050`) so Python services'
   stdout is forwarder-friendly like Go's already is. This is a real code
   change but small and self-contained — include here since it's the other
   half of "observability egress" and blocks log-shipping regardless of
   backend.

**Explicitly out of scope**: a production k8s OTel Collector Deployment (the
recommendation is customers point services directly at their own OTLP
endpoint, or run their own collector — Windrose doesn't need to ship one).

**Acceptance criteria**: `helm template` renders the new values cleanly;
live-verify by pointing a local service at a real (or local Jaeger/Tempo)
OTLP endpoint via the new Helm values path and confirming a trace/log line
arrives. No new services touched beyond Helm chart + `py-common`.

**Effort**: small (days). **Risk**: low — additive config, no behavior change
when unset.

---

## Phase 2 — Secrets adapters (extend two existing interfaces)

**Goal**: let a customer store connector credentials and JWT signing material
in their own AWS Secrets Manager / Azure Key Vault / GCP Secret Manager
instead of Vault, without Windrose code depending on which one.

**Why this is tractable**: both seams already exist as narrow interfaces — no
new abstraction design needed, just new implementations.
- `SecretsStore` Protocol (`ingestion-service/app/domain/secrets.py`): 4
  methods (`put`/`get`/`delete`/`schedule_destroy`).
- `Signer` interface (`identity-service/internal/keys/keys.go`): 2 methods
  (`Generate`/`Sign`).

**Increments**:
1. `AWSSecretsManagerStore` implementing `SecretsStore` (boto3
   `secretsmanager` client) + `AWSKMSSigner` implementing `Signer` (KMS
   asymmetric sign, RS256).
2. `AzureKeyVaultStore` + `AzureKeyVaultSigner` (Azure SDK, Key Vault secrets +
   keys API).
3. `GCPSecretManagerStore` + `GCPKMSSigner` (Secret Manager + Cloud KMS).
4. Selection wiring: extend `container.py`'s Vault-only construction
   (ingestion-service) and `cmd/server/main.go`'s `VAULT_ADDR`-gated selection
   (identity-service) into a small `SECRETS_BACKEND=vault|aws|azure|gcp`
   switch, mirroring how `deploy/terraform/{cloud}` already parameterizes
   which cloud is active — this should read as "the same choice already made
   for infra secrets, now also driving app secrets."
5. Fold the embedded-UI per-tenant embed secret (`tenant_embed_configs`,
   currently a bespoke raw-Postgres-sha256 pattern, per
   `docs/design/embedded-ui.md`) onto the same `SecretsStore` abstraction
   instead of its own fourth pattern — store the hash reference through the
   same interface so there's one secrets story, not four.
6. Update Terraform (`deploy/terraform/{aws,gcp,azure}/secrets.tf` /
   `keyvault.tf` / `secretmanager.tf`) to optionally provision the *app-level*
   secrets path (not just infra creds) when `SECRETS_BACKEND` matches that
   cloud, so a single `terraform apply` sets up both.

**Explicitly out of scope**: migrating existing Vault deployments; Vault
remains a first-class, fully-supported option, not deprecated.

**Acceptance criteria**: each new adapter passes the same contract test suite
the `VaultSecretsStore`/`TransitSigner` already have (if none exist today,
write one shared contract test per interface first, run against all
implementations — this also closes a testing gap). Live-verify at least one
non-Vault backend end-to-end (mint a connector credential, round-trip through
ingestion-service; sign and verify a JWT through identity-service).

**Effort**: medium (1-2 weeks per cloud, can parallelize across clouds).
**Risk**: low-medium — new code behind existing interfaces, but touches
credential handling so needs careful review + the same secret-store contract
tests across all four implementations.

### Status — increments 1-4 + contract tests DONE, Terraform stretch DONE, embed-secret fold DEFERRED (2026-07-16)

**Built** (`libs/py-common/windrose_common/secrets.py`, `services/ingestion-service/app/domain/secrets.py`,
`services/identity-service/internal/adapters/{awskms,azurekeyvault,gcpkms}`):

- `AWSSecretsManagerStore` (boto3) + `AWSKMSSigner` (AWS SDK v2 `kms`,
  RSA-2048 asymmetric SIGN_VERIFY, RS256).
- `AzureKeyVaultStore` (`azure-keyvault-secrets`) + `AzureKeyVaultSigner`
  (`azkeys`, RSA-2048, Sign over a locally-computed SHA-256 digest).
- `GCPSecretManagerStore` (`google-cloud-secret-manager`) + `GCPKMSSigner`
  (Cloud KMS `apiv1`, `RSA_SIGN_PKCS1_2048_SHA256`, `AsymmetricSign` over a
  digest). `GCPKMSSigner` exposes `NewWithClient` for DI — no emulator exists.
- **Selection wiring**: `SECRETS_BACKEND=vault|aws|azure|gcp` in
  `ingestion-service/app/container.py` (`_build_secrets_store`) and
  `identity-service/cmd/server/main.go` (`buildSigner`). Default `"vault"`
  reproduces the exact prior behavior (`VAULT_ADDR`-gated Vault-or-LocalSigner)
  byte-for-byte when the env var is unset — verified via the full existing
  test suites (see below), zero regressions.

**Live-verified — AWS, against a real local LocalStack container**
(`localstack/localstack:3.4`, Secrets Manager + KMS services): `uv run pytest
services/ingestion-service/tests/integration/test_secrets_store_contract.py`
puts/gets/deletes real secrets through the real Secrets Manager wire protocol,
and `go test -tags integration
./services/identity-service/test/integration/secretsigner/...` generates a
real KMS asymmetric key, signs, and verifies the signature with `crypto/rsa`
against the real KMS-returned public key. Both genuinely exercise the network
protocol, matching `VaultSecretsStore`'s existing rigor — not mocks.

**Empirically-discovered AWS divergence** (found by running the suite, not
assumed): AWS Secrets Manager's real `DeleteSecret` makes a secret unreadable
via `GetSecretValue` **immediately** once marked for deletion, even with a
future `RecoveryWindowInDays` — unlike Vault/InMemory, there is no way to
schedule a destroy that doesn't affect reads until it's actually due. The
contract suite documents this per-backend rather than forcing false
uniformity (see `test_schedule_destroy_future_does_not_remove_immediately`'s
AWS skip + the dedicated
`test_aws_schedule_destroy_future_blocks_reads_immediately`).

**Mock-tested only — Azure and GCP, per the honesty note in the task scoping**
(no local Key Vault / Secret Manager / Cloud KMS emulator exists, no cloud
credentials available in this sandbox):
- Python: `AzureKeyVaultStore`/`GCPSecretManagerStore` are exercised in the
  same contract suite against hand-written fake clients that satisfy the real
  SDKs' exception types (`ResourceNotFoundError`, `NotFound`, `AlreadyExists`)
  — standard practice for these SDKs without an emulator.
- Go: `AzureKeyVaultSigner` is exercised against the Azure SDK's **own
  documented fake transport** (`azkeys/fake.Server` +
  `fake.NewServerTransport`), backed by real `crypto/rsa` key generation and
  signing so the "signature verifies" assertion is genuine, not a rubber
  stamp. `GCPKMSSigner` is exercised via an injected fake satisfying the
  adapter's own narrow `gcpkms.Client` interface (no first-party fake exists
  for Cloud KMS), likewise backed by real RSA math.
- **Not live-verified against a real Azure or GCP account** — this is the
  honest ceiling stated in the task scoping, not a gap introduced silently.

**Shared contract test suites** (step 5 of the increment list), run against
**every** implementation including the pre-existing Vault ones (closes the
gap noted in the Acceptance Criteria above — no such suite existed before):
- Python — `services/ingestion-service/tests/integration/test_secrets_store_contract.py`:
  put-then-get round trip (+ merge semantics), delete removes, missing-get
  returns `None`, missing-delete doesn't raise, schedule_destroy
  future-doesn't-remove / past-due-destroys-once-swept. **35 passed, 1
  documented skip** (the AWS future-destroy case, see divergence above) across
  `InMemorySecretsStore`, `VaultSecretsStore` (real Vault), `AWSSecretsManagerStore`
  (real LocalStack), `AzureKeyVaultStore`/`GCPSecretManagerStore` (mock-tested).
- Go — `services/identity-service/test/integration/secretsigner/signer_contract_test.go`
  (`go test -tags integration ./test/integration/secretsigner/...`):
  Generate produces a parseable RSA public key of real strength; Sign produces
  a signature that verifies with `crypto/rsa.VerifyPKCS1v15` against that
  key AND that tampering invalidates it. **All 10 cases pass** (2 assertions ×
  5 implementations: `LocalSigner`, `TransitSigner` real Vault,
  `AWSKMSSigner` real LocalStack, `AzureKeyVaultSigner` SDK fake transport,
  `GCPKMSSigner` injected fake).
- Full pre-existing suites re-run clean after the change: ingestion-service
  **468 passed / 10 skipped** (1 unrelated pre-existing failure, a Spanner
  emulator driver issue with no connection to secrets — confirmed failing in
  isolation, untouched by this work); identity-service **all packages green**,
  plus the pre-existing `-tags integration` suite (Postgres) still green.

**Deferred — increment 5, the embed-secret fold onto `SecretsStore`.**
`identity-service`'s embed-secret code
(`internal/domain/token_embed.go`: `TenantEmbedConfig.SecretHash`,
`HashEmbedSecret`/`VerifyEmbedSecret`) is Go; `SecretsStore` is a Python
Protocol with no Go equivalent — a parallel Go interface could be built (the
task explicitly allowed this), but investigation surfaced a deeper mismatch
than the language gap: the embed-secret pattern is a one-way
**hash-and-compare** (never stores or retrieves the plaintext secret; Postgres
holds only a SHA-256, verified via `subtle.ConstantTimeCompare`), whereas
`SecretsStore.put`/`get` is a **store-and-retrieve-the-plaintext** contract
(the shape connector credentials and signing keys actually need, since the
caller must use the real value). Folding the embed secret onto `SecretsStore`
would mean either storing the *raw* high-entropy secret in Vault/AWS/Azure/GCP
and fetching it back over the network on every `/token/embed` call — a
strictly worse security posture than today's zero-roundtrip local hash
compare — or storing just the *hash* through `SecretsStore`, which is
achievable but gains none of the BYO-infra rotation/audit value the
abstraction exists for (a KV store holding a hash isn't meaningfully different
from the existing Postgres column holding one). Given the task's own
guidance not to force an awkward cross-cutting abstraction just to check a
box, this increment is deferred rather than forced through. If a customer's
compliance posture later requires zero secret material (including hashes)
outside a managed secrets backend, a follow-on increment could add a small Go
`SecretsStore`-shaped interface (`Put`/`Get`/`Delete`, mirroring the Python
Protocol's 3 non-scheduling methods) backed by the same four cloud adapters,
and switch `TenantEmbedConfig.SecretHash`'s storage to go through it — scoped
as new, separate work, not bundled into this phase.

**Done — increment 6 (Terraform), the stretch goal.**
`deploy/terraform/{aws,gcp,azure}/secrets_backend.tf` (new files, additive):
a `secrets_backend` variable (mirrors `SECRETS_BACKEND`) and
`enable_app_secrets_backend` (default `false`) gate optional resources,
namespaced separately from each cloud's existing single infra-creds blob
(`secrets.tf`'s `aws_secretsmanager_secret.windrose`,
`secretmanager.tf`'s `google_secret_manager_secret.windrose`,
`keyvault.tf`'s `azurerm_key_vault_secret.app`):
- **AWS**: IAM policy on the existing workload IRSA role, scoped to a
  `${name_prefix}/app-secrets/*` ARN prefix (Secrets Manager) and a
  `windrose:role=identity-signer` resource-tag condition (KMS
  CreateKey/Sign/GetPublicKey/ScheduleKeyDeletion — KMS key ARNs don't exist
  before creation, so tag-gating is the standard scoping mechanism).
- **GCP**: a new `google_kms_key_ring` (Cloud KMS key rings can't be created
  dynamically by the app, and are undeletable — this is the one piece of the
  GCP path Terraform must own, matching `gcpkms.Signer`'s assumption of a
  pre-provisioned ring), a dedicated Workload-Identity-bound service account,
  a conditional `secretmanager.admin` IAM binding scoped by resource-name
  prefix (CEL `startsWith`), and `cloudkms.admin`/`cloudkms.signerVerifier` on
  the new key ring.
- **Azure**: an access-policy grant on the *same* Key Vault `keyvault.tf`
  already provisions (one vault, one `AZURE_KEY_VAULT_URL`), for a new
  Workload-Identity-federated managed identity, with Secret
  (Get/Set/Delete/List/Purge) + Key (Get/Create/Sign/List) permissions.
  Signing keys are created dynamically by the adapter, so none is declared.

`terraform validate` is green for all three directories after the addition
(`terraform init -backend=false` + `terraform validate`, no credentials
needed); `terraform fmt` applied. All new resources are `count`-gated to `0`
unless both `enable_app_secrets_backend = true` and `secrets_backend` matches
that cloud, so a default `terraform apply` is unchanged.

---

## Phase 3 — SIEM / audit export (new capability, additive)

**Goal**: let a customer's SIEM (Splunk, Sentinel, Chronicle, Datadog
Security, or a generic webhook receiver) consume Windrose's audit trail
without polling the search API themselves.

**Why a generic sink first, not per-vendor connectors**: audit-service
already treats Kafka as its own ingest bus (`ingest.Consumer` subscribes to a
topic regex). The lowest-risk, most reusable shape is (a) a stable, versioned
**egress** topic audit-service itself publishes normalized security-relevant
events to, which any Kafka-consuming SIEM connector (Splunk's Kafka Connect,
Sentinel's Event Hub-compatible ingestion, a generic consumer) can subscribe
to directly, plus (b) an optional **webhook forwarder** for SIEMs that prefer
push (mirrors the existing notification-service webhook pattern already built
for Tier 2 — reuse that delivery/retry code rather than inventing a second
one).

**Increments**:
1. Define a versioned, documented `audit.export.v1` event schema (Avro or
   JSON-Schema, matching the existing event-envelope convention used
   elsewhere in the platform) — a normalized subset of audit-service's
   internal event shape, stable as an **external contract** (unlike today's
   incidental internal topics).
2. audit-service publishes to `audit.export.v1` alongside its existing
   ClickHouse/Postgres/WORM writes (same event, one more sink) — additive,
   no change to existing storage/retention behavior.
3. Reuse notification-service's existing webhook delivery + retry
   infrastructure (built for Tier 2 webhooks/rules) to add an optional
   "forward `audit.export.v1` to this HTTPS endpoint" config per tenant, for
   customers who want push instead of pulling Kafka.
4. Document both integration paths in a new `docs/design/siem-export.md`:
   schema reference, a worked example (Splunk HEC via the webhook path,
   Sentinel via the Kafka path), and the versioning/deprecation policy for
   the exported schema (since external consumers now depend on it).

**Explicitly out of scope**: building first-party Splunk/Sentinel/Chronicle
connectors ourselves — ship the stable export surface, let customers (or
their SI) wire their own connector against it, same posture as the rest of
the platform's integration philosophy.

**Acceptance criteria**: live-verify a real consumer (a throwaway Kafka
consumer script) receiving `audit.export.v1` events for a real audited action,
and a real webhook delivery to a local receiver, both end-to-end against the
live stack.

**Effort**: medium (1-2 weeks). **Risk**: low — additive sink, no change to
existing audit guarantees (hash-chain, WORM retention untouched).

### Phase 3 status: DONE, live-verified (2026-07-16)

All four increments built and live-verified end-to-end against the running
dev stack; full schema reference, both integration paths (worked Kafka +
Splunk-HEC-webhook examples) and the versioning/deprecation policy are in
`docs/design/siem-export.md`.

- **Schema**: `audit.export.v1` — the standard platform `event.Envelope`
  (top-level `event_id`/`event_type`/`tenant_id`/`actor`/`via_agent`/
  `resource_urn`/`occurred_at`/`trace_id`), with a normalized payload
  (`schema_version`, `source_event_id`, `source_event_type`, `action`,
  `resource_service`, `resource_type`, `outcome`, `payload_digest`,
  `source_topic`, `chain_date`, `chain_seq`). New package
  `services/audit-service/internal/siemexport/siemexport.go`.
- **Publish**: additive-only call in
  `services/audit-service/internal/ingest/processor.go:Handle`, strictly
  after the ClickHouse insert succeeds; reuses the existing Kafka producer
  (no new client); best-effort (logged + swallowed on failure, never returned
  from `Handle`). Zero changes to `internal/chain/chain.go`,
  `internal/export/export.go` (WORM) or any storage/retention path.
- **Webhook forwarder**: reused notification-service's existing webhook
  delivery/retry/circuit-breaker stack end-to-end with **no new delivery
  code** — added `"audit.export.v1"` to
  `internal/events/events.go:ConsumedTopics()` and one registry mapping in
  `internal/registry/registry.go` (empty audience/channels, exists only so
  `Process` reaches `deliverWebhooks`). The per-tenant "forward to this HTTPS
  endpoint" config is the **existing** `webhook_endpoints` table/admin API
  (`POST /api/v1/webhooks` with `event_types: ["audit.export.v1"]`) — no new
  table needed.
- **Live verification**: restarted only audit-service + notification-service
  (surgical, other ~20 services untouched); created a real case in
  case-service; a throwaway Kafka consumer received the corresponding
  `audit.export.v1` event with correct fields; a webhook endpoint registered
  against a throwaway local HTTP receiver received a correctly HMAC-signed
  delivery, recorded `status: "delivered"` via the existing deliveries API.
  audit-service's unit suite (`go test ./... -short`, all packages) passed
  unmodified both before and after.
- **One real bug found + fixed during verification**: the export envelope
  initially reused the source event's `event_id` verbatim, which collided
  with notification-service's global (not topic-scoped) Kafka consumer dedup
  keyspace (`evt:dedup:<event_id>`) whenever it already consumed the source
  domain topic — silently dropping the export event before it ever reached
  the webhook dispatch. Fixed by deriving a distinct, deterministic
  `event_id` (`uuid5("audit.export.v1:" + source_event_id)`) and preserving
  the original id as `source_event_id` in the payload. See
  `docs/design/siem-export.md`'s "Why a derived event_id" for the full
  writeup.

---

## Phase 4 — Identity provider / OIDC (new interface, largest lift)

**Goal**: let a customer authenticate Windrose users against their own Okta/
Auth0/Entra tenant (or any standards-compliant OIDC IdP), with a real
interactive login flow — not just JWT verification pointed at a different
JWKS URL (which already works today, per the audit).

**Why this is the biggest phase**: unlike Phases 1-3, there is no existing
`IdentityProvider` interface to extend — `domain.KeycloakAdmin` is a
single-vendor interface with Keycloak-specific semantics (realm-per-tenant)
that don't map cleanly onto Okta/Auth0/Entra's admin models. This phase
designs and builds the seam ai-gateway already has for LLM providers
(`provider_deployments` + adapter registry), but for identity.

**Increments** (roughly sequential — each unblocks the next):
1. **Generic `IdentityProvider` interface** in identity-service, scoped to
   what Windrose actually needs (not full user-lifecycle CRUD parity across
   vendors): `VerifyDiscoveryDocument`, `ExchangeAuthCode` (or delegate PKCE
   entirely to the IdP + just verify the resulting ID token), and — for
   deployments that still want Windrose-managed invites — an optional
   `InviteUser`/`DisableUser` subset, explicitly marked best-effort per
   vendor rather than a hard contract.
2. **Real OIDC/PKCE login flow in ui-web** — the actual missing piece today
   (`AUTH_MODE=dev` is the only implemented path). Standard `/login` →
   redirect to IdP authorize endpoint → `/api/auth/callback` exchanges code
   → verifies ID token against the IdP's own JWKS (fetched via standard
   `.well-known/openid-configuration` discovery, which nothing in the
   codebase does today) → mints Windrose's own internal session JWT.
3. **Claims-normalization layer** — the load-bearing piece for authorization
   to keep working: map IdP-specific claims (Okta groups, Auth0 custom
   claims/Actions output, Entra App Roles/group claims) into Windrose's
   required `tenant_id`/`typ`/`scopes` shape. Ship this as a per-tenant
   configurable mapping (e.g. "IdP group `claims-adjusters` → Windrose scope
   set X"), not hardcoded per vendor — this is what makes it a platform
   capability instead of three one-off integrations.
4. **Per-tenant IdP config** — a new table (identity-service), replacing/
   extending the current "one Keycloak realm per tenant" model: issuer URL,
   client ID, discovery URL (or explicit JWKS/authorize/token endpoints for
   IdPs with nonstandard discovery), the claims-mapping config from #3. Wire
   into the tenant self-service admin UI already built (per
   `project_windrose_rbac_model`) as a new settings screen.
5. **Keycloak becomes ONE configuration of Phase 4's generic OIDC path**, not
   a separately-coded special case — i.e., after this phase, "use Keycloak"
   and "use Okta" are the same code path with different per-tenant config,
   which also simplifies the existing dev/e2e harness rather than adding a
   fifth special case.
6. Helm/Terraform: make `JWKS_URL`/`JWT_ISSUER` genuinely customer-settable
   per deployment (today they default to the local harness) — small, but
   sequence after #1-4 so there's a real login flow to point them at.

**Explicitly out of scope for v1**: full bidirectional user-provisioning sync
(SCIM) — start with "customer's IdP is the source of truth for who can log
in and what claims they carry," not "Windrose pushes user lifecycle events
back to the IdP." SCIM can be a follow-on phase if a customer needs it.

**Acceptance criteria**: live-verify a real interactive login against at
least one real external IdP (a free-tier Okta or Auth0 dev tenant is
sufficient for verification) end-to-end — login → claims mapped → real RBAC
authorization decision on a subsequent request — plus regression-verify the
existing Keycloak/dev-mode paths still work unchanged via the live E2E suite
(`tests-live/`).

**Effort**: large (4-6 weeks) — this is a genuine multi-service, UX-touching
feature, not a config/adapter task like Phases 1-3.
**Risk**: medium-high — touches the login UX and the authorization claims
contract directly; needs the live E2E regression suite (`tests-live/`,
already built) as a hard regression gate before/after, given how much of the
platform depends on the current claims shape.

---

## Sequencing note

Phases 1-3 are independent and can be built in parallel or reordered without
consequence. Phase 4 is deliberately last: it's the only phase requiring a
brand-new interface (vs. extending one that exists), it's the only one that
changes user-facing login UX, and it benefits most from the live E2E
regression suite (`tests-live/`) being mature first, since it's the highest-
blast-radius change of the four.

## Status

Analysis complete (this document). Phases 2–4: not started. Each remaining
phase should get the same treatment prior workstreams in this build received:
real (non-mocked) implementation, live-verified against the running stack,
and — for Phase 4 especially — run through `tests-live/` before being
considered done.

### Phase 1 — OTel wiring: DONE (task #73)

All 5 increments shipped:

1. **Helm wiring** — `deploy/helm/windrose/values.yaml` `config:` block now
   carries `WINDROSE_OTEL_ENABLED: "false"` (default, clean no-op) and a
   commented-out `OTEL_EXPORTER_OTLP_ENDPOINT` example. Flows to every service
   via the existing `envFrom: configMapRef` in `templates/deployment.yaml` —
   no per-service change needed. Verified with `helm template` showing the
   key rendered correctly in the ConfigMap both unset and overridden (e.g.
   `--set config.OTEL_EXPORTER_OTLP_ENDPOINT=otel-collector:4317`).
2. **Per-cloud override examples** — `values-aws.yaml`, `values-gcp.yaml`,
   `values-azure.yaml` each got a commented block showing the opt-in override
   pattern (in-cluster collector or a vendor OTLP/gRPC endpoint placeholder).
   Nothing is defaulted on for any cloud.
3. **ServiceMonitor template** — new `templates/servicemonitor.yaml`, gated
   on `observability.serviceMonitor.enabled` (default `false`), ranges over
   `.Values.services` (the same list `deployment.yaml`/`service.yaml` already
   iterate, kept in sync with `deploy/services.yaml`'s 23-service inventory)
   and scrapes each service's existing `/metrics` on the `http` port. Verified
   `helm template` renders 0 ServiceMonitors when disabled and 23 when
   enabled, and `helm lint` is clean in both states.
4. **CONFIG.md** — new "Observability — bring your own backend" section:
   the two env vars, that metrics stay pull-based (`/metrics`, recommend the
   new ServiceMonitor or a manual scrape config), and the **corrected**
   protocol claim (see "Course correction" below).
5. **Dead RUM stub** — removed. `NEXT_PUBLIC_OTEL_COLLECTOR_URL` deleted from
   `services/ui-web/.env.example`; grep-confirmed it was declared but never
   read anywhere in ui-web source (no `@opentelemetry/exporter-trace-otlp-http`
   or similar dependency existed either). **Deliberately deferred**: real
   browser RUM export (web-vitals + client-error OTLP/HTTP exporter) is a
   genuine new-code lift (new dependency, a client entrypoint, a batching/
   retry story) — out of scope for a "packaging only" phase. Tracked as a
   follow-on if a customer asks for browser-side traces specifically.
6. **Python JSON logging** — new `libs/py-common/windrose_common/logging.py`
   (`JsonFormatter` + `configure_json_logging(service_name)`, stdlib-only, no
   new dependency), mirroring Go's `slog.NewJSONHandler(os.Stdout, nil)` /
   `MASTER-FR-050` convention. Wired into `eval-service`, `ai-gateway`, and
   `agent-runtime` (`app/main.py`, one line each, before the first logger is
   used) as the proof of pattern. Live-verified with a real interpreter
   process importing each service's `app.main` and emitting log lines —
   output was valid single-line JSON with `time`/`level`/`logger`/`message`/
   `service` fields, `extra={...}` merged in, and exception tracebacks
   captured as an `exc_info` string. Each service's full non-integration unit
   suite still passes after the change (eval-service 47, ai-gateway 141,
   agent-runtime 108 — all green). **Remaining Python services should adopt
   the same one-line call at their next touch** — not done for all of them in
   this phase, per the task's scoping ("prove the pattern," not a
   full-platform sweep).

**Course correction versus this doc's original increment 3 text**: the design
doc assumed "OTLP/gRPC for Go, OTLP/HTTP acceptable for Python." Checking the
actual code (`libs/py-common/windrose_common/otelx.py`) shows Python's
exporter is `opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter`
— **gRPC**, not HTTP, same as Go. `pyproject.toml` only depends on
`opentelemetry-exporter-otlp-proto-grpc`; there is no HTTP exporter installed
and no `OTEL_EXPORTER_OTLP_PROTOCOL` switch. CONFIG.md documents the corrected
claim. Left as-is for this phase (packaging only, no new code) — adding an
HTTP exporter variant to `otelx.py` would be a real code change belonging to
a future increment, not implied by "wire up the Helm values."

**Verification summary**: `helm lint` clean; `helm template` clean for
default values, `--set observability.serviceMonitor.enabled=true`, and all
three `values-<cloud>.yaml` overlays; Python JSON logging live-verified via
real process starts (not unit-test mocks) for all three touched services.
