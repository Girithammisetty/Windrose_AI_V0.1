// Command registry runs tool-registry: the tool-plane catalog + admin API (BRD
// 13) — tool registration/versioning/lifecycle, per-tenant enablement, kill
// switches, BYO onboarding, real semantic discovery (Ollama embeddings +
// pgvector), and the tool.events.v1 lifecycle stream via the outbox → Kafka.
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

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/otelx"
	"github.com/windrose-ai/go-common/redisx"
	"github.com/windrose-ai/tool-plane/internal/api"
	"github.com/windrose-ai/tool-plane/internal/authz"
	"github.com/windrose-ai/tool-plane/internal/embed"
	"github.com/windrose-ai/tool-plane/internal/enforce"
	"github.com/windrose-ai/tool-plane/internal/events"
	"github.com/windrose-ai/tool-plane/internal/register"
	"github.com/windrose-ai/tool-plane/internal/store"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless WINDROSE_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "tool-registry")
	defer func() { _ = otelShutdown(context.Background()) }()

	dbURL := env("DATABASE_URL", "postgres://windrose:windrose_dev@localhost:5432/tool_plane?sslmode=disable")
	// Migrations need DDL/ownership + role creation, so they run under a
	// privileged role (MIGRATE_DATABASE_URL, default = DATABASE_URL). The runtime
	// pool connects as DATABASE_URL, which in a hardened deploy is a NON-superuser
	// app role (toolplane_app) so FORCE row-level security is actually enforced.
	migrateURL := env("MIGRATE_DATABASE_URL", dbURL)
	if err := store.Migrate(migrateURL); err != nil {
		slog.Error("migrations failed", "err", err)
		os.Exit(1)
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
	st := store.NewPG(pool)

	rc := redisx.NewFromEnv(env("REDIS_ADDR", "localhost:6379"), os.Getenv)
	kill := enforce.NewKillRegistry(rc)
	if err := kill.SyncFromStore(ctx, st); err != nil {
		slog.Warn("kill sync failed (continuing)", "err", err)
	}

	embedder := embed.NewOllama(env("OLLAMA_URL", "http://localhost:11434/v1"), env("EMBED_MODEL", embed.ModelNomic))

	verifier := authjwt.NewJWKS(
		env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	// Real OPA admin authorizer (MASTER-FR-012): every /api/v1 route authorizes
	// its canonical action against the rbac permissions_flat projection.
	adminAuthz := authz.NewAdminOPA(env("OPA_URL", "http://localhost:8281"), env("REDIS_ADDR", "localhost:6379"))

	// Deploy-time action-catalog registration (RBC-FR-022): push tool-plane's
	// action manifest to rbac so OPA's catalog knows each action (`action_known`).
	// FAIL LOUDLY: a configured registration that fails keeps /readyz at 503.
	regStatus := register.NewStatus()
	register.RunAsync(ctx, register.Config{
		RBACURL:       os.Getenv("RBAC_URL"),
		SigningKeyPEM: os.Getenv("REGISTER_SIGNING_KEY_PEM"),
		SigningKID:    os.Getenv("REGISTER_SIGNING_KID"),
		Issuer:        os.Getenv("JWT_ISSUER"),
		Audience:      os.Getenv("JWT_AUDIENCE"),
		TenantID:      os.Getenv("REGISTER_TENANT_ID"),
	}, regStatus)

	srv := &api.RegistryServer{
		Store: st, Embedder: embedder, Kill: kill, Health: enforce.NewHealthStore(rc),
		Verifier: verifier, Authz: adminAuthz, RegStatus: regStatus,
	}

	// Outbox relay → real Kafka (Redpanda) unless KAFKA_BROKERS=false.
	startRelay(ctx, st)

	addr := env("LISTEN_ADDR", ":8090")
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(srv.Router(), "tool-registry"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(sctx)
	}()
	slog.Info("tool-registry listening", "addr", addr)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("server failed", "err", err)
		os.Exit(1)
	}
}

// startRelay wires the transactional-outbox relay to the real go-common Kafka
// producer (MASTER-FR-034). KAFKA_BROKERS=false uses the in-memory publisher for
// broker-less local dev only.
func startRelay(ctx context.Context, st *store.PG) {
	var pub events.Publisher
	brokers := env("KAFKA_BROKERS", "localhost:9092")
	if brokers == "false" {
		slog.Warn("KAFKA_BROKERS=false; in-memory publisher (events not durable; dev only)")
		pub = events.NewInMemory()
	} else {
		kp := events.NewKafkaPublisher(ctx, strings.Split(brokers, ","), os.Getenv("SCHEMA_REGISTRY_URL"))
		pub = kp
	}
	relay := &events.Relay{Source: st, Publisher: pub, Interval: 250 * time.Millisecond}
	go relay.Run(ctx)
}
