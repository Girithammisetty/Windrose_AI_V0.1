// identity-service entrypoint. Wires the domain services onto either the
// Postgres store (DATABASE_URL set) or the in-memory store (dev mode),
// bootstraps signing keys, and starts the HTTP server + background loops
// (outbox poller, key-cache refresh, scheduled deletions).
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/windrose-ai/go-common/otelx"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/identity-service/internal/adapters/awskms"
	"github.com/windrose-ai/identity-service/internal/adapters/azurekeyvault"
	"github.com/windrose-ai/identity-service/internal/adapters/denylist"
	"github.com/windrose-ai/identity-service/internal/adapters/gcpkms"
	"github.com/windrose-ai/identity-service/internal/adapters/keycloak"
	"github.com/windrose-ai/identity-service/internal/adapters/localinfra"
	"github.com/windrose-ai/identity-service/internal/adapters/vault"
	"github.com/windrose-ai/identity-service/internal/api"
	"github.com/windrose-ai/identity-service/internal/authz"
	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/events"
	"github.com/windrose-ai/identity-service/internal/keys"
	"github.com/windrose-ai/identity-service/internal/rbacclient"
	"github.com/windrose-ai/identity-service/internal/store/memory"
	"github.com/windrose-ai/identity-service/internal/store/postgres"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless WINDROSE_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "identity-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	var store domain.Store
	ready := func() error { return nil }
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		// Migrations need DDL/ownership + role creation, so they run under a
		// privileged role (MIGRATE_DATABASE_URL, default = DATABASE_URL). The
		// runtime pool connects as DATABASE_URL, which in a hardened deploy is a
		// NON-superuser app role (identity_app) so FORCE row-level security is
		// actually enforced — a superuser/BYPASSRLS runtime role would silently
		// defeat tenant isolation.
		migrateURL := dsn
		if m := os.Getenv("MIGRATE_DATABASE_URL"); m != "" {
			migrateURL = m
		}
		if err := postgres.Migrate(migrateURL); err != nil {
			log.Error("migrate failed", "error", err)
			os.Exit(1)
		}
		poolCfg, err := pgxpool.ParseConfig(dsn)
		if err != nil {
			log.Error("db connect failed", "error", err)
			os.Exit(1)
		}
		if v := os.Getenv("DB_MAX_CONNS"); v != "" {
			if n, e := strconv.Atoi(v); e == nil && n > 0 {
				poolCfg.MaxConns = int32(n)
			}
		} else {
			poolCfg.MaxConns = 20 // explicit default, up from pgx's ~4
		}
		pool, err := pgxpool.NewWithConfig(ctx, poolCfg)
		if err != nil {
			log.Error("db connect failed", "error", err)
			os.Exit(1)
		}
		defer pool.Close()
		store = postgres.New(pool)
		ready = func() error { return pool.Ping(context.Background()) }
		log.Info("store: postgres")
	} else {
		store = memory.New()
		log.Warn("store: in-memory (set DATABASE_URL for postgres)")
	}

	clock := time.Now

	// Signing keys (BYO Infra Hardening Phase 2, docs/design/
	// byo-infra-hardening.md): SECRETS_BACKEND=vault|aws|azure|gcp selects the
	// signer, default "vault" preserving the original VAULT_ADDR-gated
	// behavior unchanged when the env var is unset. Private keys never leave
	// whichever backend is selected (IDN-FR-050); LocalSigner is the only
	// dev/tests fallback (backend "vault" with VAULT_ADDR unset).
	signer, signerDesc, err := buildSigner(ctx, log)
	if err != nil {
		log.Error("signer init failed", "error", err)
		os.Exit(1)
	}
	log.Info("signer: " + signerDesc)
	km := keys.NewKeyManager(store, signer, clock)
	if err := km.Bootstrap(ctx); err != nil {
		log.Error("key bootstrap failed", "error", err)
		os.Exit(1)
	}
	issuer := keys.NewIssuer(km, clock)

	// Adapters. Real Keycloak admin is used when KEYCLOAK_URL is set;
	// otherwise the fake keeps dev mode self-contained. With
	// KEYCLOAK_ADMIN_USER/KEYCLOAK_ADMIN_PASSWORD set the adapter obtains (and
	// caches/refreshes) admin tokens itself via the master-realm password
	// grant (admin-cli); KEYCLOAK_ADMIN_TOKEN remains as a static fallback.
	var kc domain.KeycloakAdmin = keycloak.NewFake()
	if base := os.Getenv("KEYCLOAK_URL"); base != "" {
		tokenFn := func(context.Context) (string, error) {
			return os.Getenv("KEYCLOAK_ADMIN_TOKEN"), nil
		}
		if user := os.Getenv("KEYCLOAK_ADMIN_USER"); user != "" {
			tokenFn = keycloak.PasswordGrant(base, user, os.Getenv("KEYCLOAK_ADMIN_PASSWORD"), nil)
			log.Info("keycloak: real admin adapter (password grant)", "url", base, "user", user)
		} else {
			log.Info("keycloak: real admin adapter (static KEYCLOAK_ADMIN_TOKEN)", "url", base)
		}
		kc = &keycloak.HTTPAdmin{BaseURL: base, Token: tokenFn}
	} else {
		log.Warn("keycloak: fake (set KEYCLOAK_URL for a real Keycloak)")
	}
	// Local-equivalent infra adapters (adapters/localinfra): honest no-op
	// runners for the ports with no real single-Mac target — the per-cloud
	// Terraform runner (step 3), the per-tenant schema provisioner (step 4),
	// and the synthetic health prober (step 7). They SUCCEED and advance the
	// saga to `active`, and log loudly so nothing is silently faked. The
	// substantive local gate is the REAL Keycloak realm/user step. In a cloud
	// deploy these bind to the real per-cloud infra module runner.
	tf := localinfra.Runner{Log: log}
	db := localinfra.DB{Log: log}
	prober := localinfra.Prober{Log: log}
	log.Warn("infra: local-equivalent Terraform/DB/Prober (no cloud target on this host; " +
		"Keycloak realm/user provisioning is REAL)")

	// API-key denylist: real Redis (≤5s propagation, IDN-FR-033) when REDIS_ADDR
	// is set; in-memory otherwise (single-replica dev/tests).
	var deny domain.Denylist = denylist.NewMemory()
	if redisAddr := os.Getenv("REDIS_ADDR"); redisAddr != "" {
		deny = &denylist.Redis{Cmd: redisx.NewFromEnv(redisAddr, os.Getenv), Prefix: "denylist:apikey:", TTL: 24 * time.Hour}
		log.Info("denylist: redis")
	} else {
		log.Warn("denylist: in-memory (set REDIS_ADDR for multi-replica Redis)")
	}

	deps := domain.StepDeps{Store: store, Keycloak: kc, Terraform: tf, DB: db, Prober: prober, Clock: clock}
	notify := func(ctx context.Context, t *domain.Tenant, st *domain.ProvisioningStep) {
		// IDN-FR-010: provisioning progress events -> realtime-hub via outbox.
		_ = store.AppendOutbox(ctx, domain.NewEvent(domain.EvTenantStepCompleted, t.ID,
			domain.Actor{Type: "service", ID: "identity-service"}, t.URN(), clock().UTC(),
			map[string]any{"step": st.StepName, "status": string(st.Status), "attempt": st.Attempt}))
	}
	engine := domain.NewEngine(store, domain.DefaultEngineConfig(), deps.ProvisionSteps, deps.DestroySteps, notify)

	tenants := &domain.TenantService{
		Store: store, Engine: engine, Graph: domain.DefaultModuleGraph(), Prober: prober,
		Clock: clock, Async: true,
	}
	// BR-9 last-admin guard: real rbac-backed checker when RBAC_URL is set;
	// otherwise the allow-all stub (BR-9 NOT enforced — dev/tests only).
	rbacURL := os.Getenv("RBAC_URL")
	var lastAdmin domain.LastAdminChecker = domain.AllowAllLastAdminChecker{}
	if rbacURL != "" {
		lastAdmin = &rbacclient.Checker{BaseURL: rbacURL, Store: store, Issuer: issuer, Log: log}
		log.Info("last-admin checker: rbac", "url", rbacURL)
	} else {
		log.Warn("last-admin checker: allow-all — BR-9 (last tenant admin cannot be deactivated) is NOT enforced; set RBAC_URL to enable the real rbac-backed checker")
	}
	users := &domain.UserService{Store: store, Keycloak: kc, LastAdmin: lastAdmin, Clock: clock}
	sas := &domain.ServiceAccountService{Store: store, Denylist: deny, Clock: clock}
	tokens := &domain.TokenService{
		Store: store, Issuer: issuer, Verifier: issuer, Denylist: deny,
		Limiter: domain.NewSlidingWindowLimiter(domain.OBORateLimit, domain.OBORateWindow), Clock: clock,
	}

	trusted := map[string]bool{}
	for _, id := range strings.Split(os.Getenv("TRUSTED_SPIFFE_IDS"), ",") {
		if id = strings.TrimSpace(id); id != "" {
			trusted[id] = true
		}
	}
	if len(trusted) == 0 {
		trusted["spiffe://windrose.ai/ns/platform/sa/agent-runtime"] = true
	}

	// Runtime authorizer: the REAL OPA-over-projection path (MASTER-FR-012),
	// like every other Go service — a tenant Admin's rbac projection admin flag
	// (BR-7) authorizes identity's guarded actions without the scope being baked
	// into the token. ScopeAuthorizer remains only as the dev/test fallback when
	// no OPA sidecar is configured.
	var authorizer authz.Authorizer = authz.ScopeAuthorizer{}
	if opaURL := os.Getenv("OPA_URL"); opaURL != "" {
		authorizer = authz.NewOPAAuthorizer(opaURL, os.Getenv("REDIS_ADDR"))
		log.Info("authorizer: OPA over rbac projection", "opa", opaURL)
		// Make identity's guarded actions catalog-known in rbac (fail-loud via
		// logs; retries because identity boots before rbac). Without this the
		// OPA admin short-circuit denies them with reason "unknown_action".
		if rbacURL != "" {
			reg := &rbacclient.Registrar{BaseURL: rbacURL, Issuer: issuer, Log: log}
			go reg.Run(ctx)
		} else {
			log.Warn("authorizer: OPA enabled but RBAC_URL unset — cannot register identity's guarded actions; they may deny as unknown_action")
		}
	} else {
		log.Warn("authorizer: token-scope fallback (set OPA_URL for the real rbac-projection path; scope-based authz does NOT honor a tenant Admin's projection grant)")
	}

	srv := &api.Server{
		Store: store, Tenants: tenants, Users: users, SAs: sas, Tokens: tokens,
		KM: km, Verifier: issuer, Authz: authorizer,
		TrustedSpiffeIDs: trusted,
		// F-2: only honor X-Spiffe-Id when explicitly enabled (mesh strips +
		// re-injects it). TRUST_SPIFFE_HEADER=true to enable.
		TrustSpiffeHeader: os.Getenv("TRUST_SPIFFE_HEADER") == "true",
		Clock:             clock, Log: log, Ready: ready,
	}

	// Background loops.
	// Event publishing: real Kafka producer (Redpanda) via libs/go-common,
	// draining the transactional outbox (MASTER-FR-030/034). KAFKA_BROKERS
	// defaults to the local Redpanda so the runtime path has no log stub.
	var publisher events.Publisher
	if brokers := os.Getenv("KAFKA_BROKERS"); brokers != "false" {
		if brokers == "" {
			brokers = "localhost:9092"
		}
		kp := events.NewKafkaPublisher(ctx, strings.Split(brokers, ","), os.Getenv("SCHEMA_REGISTRY_URL"))
		defer func() { _ = kp.Close() }()
		publisher = kp
		log.Info("publisher: kafka", "brokers", brokers)
	} else {
		publisher = &events.LogPublisher{Log: log} // KAFKA_BROKERS=false: log-only (local dev without a broker)
		log.Warn("publisher: log-only (KAFKA_BROKERS=false)")
	}
	poller := &events.Poller{Store: store, Publisher: publisher, Interval: 2 * time.Second, BatchSize: 100, Log: log}
	go poller.Run(ctx)
	go func() { // key-cache refresh so retirements take effect (AC-8)
		t := time.NewTicker(time.Minute)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				_ = km.RetireDueKeys(ctx)
				_ = tenants.RunScheduledDeletions(ctx)
			}
		}
	}()

	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(srv.Router(), "identity-service"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shCtx)
	}()
	log.Info("identity-service listening", "addr", addr)
	if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Error("server error", "error", err)
		os.Exit(1)
	}
}

// buildSigner selects the keys.Signer backend via SECRETS_BACKEND=
// vault|aws|azure|gcp (BYO Infra Hardening Phase 2,
// docs/design/byo-infra-hardening.md). Default "vault" preserves the original
// VAULT_ADDR-gated LocalSigner-fallback behavior unchanged when the env var
// is unset — no regression to existing deployments.
func buildSigner(ctx context.Context, log *slog.Logger) (keys.Signer, string, error) {
	backend := strings.ToLower(os.Getenv("SECRETS_BACKEND"))
	if backend == "" {
		backend = "vault"
	}
	switch backend {
	case "vault":
		if vaultAddr := os.Getenv("VAULT_ADDR"); vaultAddr != "" {
			vs, err := vault.New(vaultAddr, os.Getenv("VAULT_TOKEN"), os.Getenv("VAULT_TRANSIT_MOUNT"))
			if err != nil {
				return nil, "", err
			}
			return vs, "vault transit", nil
		}
		log.Warn("signer: local RSA (set VAULT_ADDR for Vault transit, or SECRETS_BACKEND=aws|azure|gcp)")
		return keys.NewLocalSigner(), "local RSA (dev)", nil
	case "aws":
		cfg := awskms.Config{
			Region:          os.Getenv("AWS_REGION"),
			EndpointURL:     os.Getenv("AWS_KMS_ENDPOINT_URL"),
			AccessKeyID:     os.Getenv("AWS_ACCESS_KEY_ID"),
			SecretAccessKey: os.Getenv("AWS_SECRET_ACCESS_KEY"),
		}
		s, err := awskms.New(ctx, cfg)
		if err != nil {
			return nil, "", err
		}
		return s, "aws kms", nil
	case "azure":
		vaultURL := os.Getenv("AZURE_KEY_VAULT_URL")
		if vaultURL == "" {
			return nil, "", errors.New("SECRETS_BACKEND=azure requires AZURE_KEY_VAULT_URL")
		}
		s, err := azurekeyvault.New(vaultURL, nil, nil)
		if err != nil {
			return nil, "", err
		}
		return s, "azure key vault", nil
	case "gcp":
		keyRing := os.Getenv("GCP_KMS_KEY_RING")
		if keyRing == "" {
			return nil, "", errors.New("SECRETS_BACKEND=gcp requires GCP_KMS_KEY_RING (projects/*/locations/*/keyRings/*)")
		}
		s, err := gcpkms.New(ctx, keyRing)
		if err != nil {
			return nil, "", err
		}
		return s, "gcp cloud kms", nil
	default:
		return nil, "", fmt.Errorf("unknown SECRETS_BACKEND: %q", backend)
	}
}
