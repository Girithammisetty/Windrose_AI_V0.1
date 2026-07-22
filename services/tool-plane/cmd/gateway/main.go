// Command gateway runs mcp-gateway: the tool-plane data plane (BRD 13) — the
// single /mcp endpoint hosting/federating backend MCP facades behind the
// per-call enforcement pipeline (authN → kill/enablement → OPA → rate limit →
// schema → tier → invoke → audit), emitting ai.tool_invoked.v1 to real Kafka.
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/datacern-ai/go-common/authjwt"
	"github.com/datacern-ai/go-common/dbcheck"
	"github.com/datacern-ai/go-common/otelx"
	gcoutbox "github.com/datacern-ai/go-common/outbox"
	"github.com/datacern-ai/go-common/redisx"
	"github.com/datacern-ai/tool-plane/internal/api"
	"github.com/datacern-ai/tool-plane/internal/authz"
	"github.com/datacern-ai/tool-plane/internal/enforce"
	"github.com/datacern-ai/tool-plane/internal/events"
	"github.com/datacern-ai/tool-plane/internal/mcp"
	"github.com/datacern-ai/tool-plane/internal/register"
	"github.com/datacern-ai/tool-plane/internal/store"
	"github.com/datacern-ai/tool-plane/policy"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	slog.SetDefault(slog.New(otelx.WrapLogHandler(slog.NewJSONHandler(os.Stdout, nil))))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless datacern_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "mcp-gateway")
	defer func() { _ = otelShutdown(context.Background()) }()

	// Stabilization guard (rule: no fake/mock/stub in a runtime path). When
	// REQUIRE_REAL_ADAPTERS=true — set in every real deploy — the service REFUSES
	// to boot on an in-memory/fake adapter instead of silently faking success.
	// Absent (local dev), the loud-warn fallbacks below keep dev self-contained.
	requireReal := os.Getenv("REQUIRE_REAL_ADAPTERS") == "true"
	mustReal := func(realEnv, adapter string) {
		slog.Error("REQUIRE_REAL_ADAPTERS=true but " + realEnv + " is unset — refusing to boot on the " + adapter + " fallback")
		os.Exit(1)
	}

	dbURL := env("DATABASE_URL", "postgres://datacern:datacern_dev@localhost:5432/tool_plane?sslmode=disable")
	// The registry applies migrations; the gateway retries a lightweight apply so
	// it is safe to start either first. Migrations run under a privileged role
	// (MIGRATE_DATABASE_URL, default = DATABASE_URL); the runtime pool connects as
	// DATABASE_URL, a NON-superuser app role (toolplane_app) so FORCE RLS binds it.
	migrateURL := env("MIGRATE_DATABASE_URL", dbURL)
	if err := store.Migrate(migrateURL); err != nil {
		slog.Warn("migrate (gateway) failed; assuming registry applied", "err", err)
	}
	poolCfg, err := pgxpool.ParseConfig(dbURL)
	if err != nil {
		slog.Error("db connect failed", "err", err)
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
		slog.Error("db connect failed", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	// SEC-1 (BRD 58): fail closed if the runtime role can bypass RLS.
	if err := dbcheck.AssertNonSuperuser(ctx, pool); err != nil {
		slog.Error("refusing to start", "err", err)
		os.Exit(1)
	}
	st := store.NewPG(pool)

	rc := redisx.NewFromEnv(env("REDIS_ADDR", "localhost:6379"), os.Getenv)

	// Real OPA sidecar client. In dev, upload the tool-plane policy into the
	// shared sidecar (it otherwise serves only the rbac bundle).
	opa := authz.NewOPAClient(env("OPA_URL", "http://localhost:8281"))
	if env("TP_OPA_UPLOAD_POLICY", "true") == "true" {
		if err := opa.UploadPolicy(ctx, policy.ToolPlaneModuleID, policy.ToolPlaneRego); err != nil {
			slog.Warn("policy upload failed (assuming bundle-mounted)", "err", err)
		}
	}

	kill := enforce.NewKillRegistry(rc)
	if err := kill.SyncFromStore(ctx, st); err != nil {
		slog.Warn("kill sync failed (continuing)", "err", err)
	}
	go kill.Watch(ctx) // ≤5s kill propagation across replicas (BR-17/AC-5)

	health := enforce.NewHealthStore(rc)

	// Signed proposal-execution grants are verified against agent-runtime's JWKS
	// (TPL-FR-035). Nothing from the MCP body can execute a write without a
	// verified, human-approved grant.
	proposals := authz.NewProposalVerifierJWKS(
		env("PROPOSAL_JWKS_URL", "http://agent-runtime/api/v1/.well-known/jwks.json"),
		env("PROPOSAL_ISSUER", "datacern-agent-runtime"))

	pipeline := &enforce.Pipeline{
		Catalog:    api.NewCatalogResolver(st),
		Enablement: st,
		Kill:       kill,
		OPA:        opa,
		Rate:       enforce.NewRateLimiter(rc),
		Grants:     enforce.NewRedisGrantLoader(rc),
		Backend:    mcp.NewHTTPBackend(),
		Audit:      st,
		Proposals:  proposals,
		Health:     health,
	}

	verifier := authjwt.NewJWKS(
		env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	// Deploy-time action-catalog registration (RBC-FR-022): both tool-plane
	// binaries register the manifest (rbac upserts, so this is idempotent with
	// cmd/registry's registration). FAIL LOUDLY: a configured registration that
	// fails keeps /readyz at 503.
	regStatus := register.NewStatus()
	register.RunAsync(ctx, register.Config{
		RBACURL:       os.Getenv("RBAC_URL"),
		SigningKeyPEM: os.Getenv("REGISTER_SIGNING_KEY_PEM"),
		SigningKID:    os.Getenv("REGISTER_SIGNING_KID"),
		Issuer:        os.Getenv("JWT_ISSUER"),
		Audience:      os.Getenv("JWT_AUDIENCE"),
		TenantID:      os.Getenv("REGISTER_TENANT_ID"),
	}, regStatus)

	gw := &api.GatewayServer{Pipeline: pipeline, Store: st, Verifier: verifier, Kill: kill, RegStatus: regStatus}

	// SLA-breach detector + auto-quarantine sweep (TPL-FR-051): evaluates rolling
	// health against declared SLAs each minute and quarantines on sustained breach.
	go runSLASweep(ctx, st, health)

	startRelay(ctx, st, requireReal, mustReal)
	// B6 (BRD 58): published outbox rows are drained but never pruned; sweep
	// them past a retention window so the table doesn't grow unboundedly.
	go gcoutbox.NewPruner(pool, "outbox", "app.role", "platform").Run(ctx)

	addr := env("LISTEN_ADDR", ":8091")
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(gw.Router(), "mcp-gateway"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(sctx)
	}()
	slog.Info("mcp-gateway listening", "addr", addr, "mcp_spec", mcp.SpecVersion)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("server failed", "err", err)
		os.Exit(1)
	}
}

func startRelay(ctx context.Context, st *store.PG, requireReal bool, mustReal func(string, string)) {
	var pub events.Publisher
	brokers := env("KAFKA_BROKERS", "localhost:9092")
	if brokers == "false" {
		if requireReal {
			mustReal("KAFKA_BROKERS", "in-memory publisher (outbox events would not reach Kafka)")
		}
		slog.Warn("KAFKA_BROKERS=false; in-memory publisher (events not durable; dev only)")
		pub = events.NewInMemory()
	} else {
		pub = events.NewKafkaPublisher(ctx, strings.Split(brokers, ","), os.Getenv("SCHEMA_REGISTRY_URL"))
	}
	relay := &events.Relay{Source: st, Publisher: pub, Interval: 250 * time.Millisecond}
	go relay.Run(ctx)
}

// runSLASweep evaluates each active tool-version's rolling health against its
// declared SLA once a minute (TPL-FR-051). Sustained breach emits
// tool.sla_breached; with auto-quarantine enabled it moves the version to
// quarantined (which the pipeline then serves as TOOL_KILLED).
func runSLASweep(ctx context.Context, st *store.PG, health *enforce.HealthStore) {
	autoQuarantine := env("TP_AUTO_QUARANTINE", "false") == "true"
	q := &enforce.Quarantiner{Health: health, Store: st, Threshold: 10}
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			versions, err := st.ListActiveVersions(ctx)
			if err != nil {
				slog.Warn("sla sweep: list versions failed", "err", err)
				continue
			}
			for _, v := range versions {
				if _, _, err := q.Evaluate(ctx, v.ToolID, v.Version, v.DeclaredSLA, autoQuarantine); err != nil {
					slog.Warn("sla sweep evaluate failed", "tool", v.ToolID, "err", err)
				}
			}
		}
	}
}
