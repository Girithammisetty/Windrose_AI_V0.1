// Command server runs query-service: the platform's single SQL execution
// broker (BRD 05).
package main

import (
	"context"
	"crypto/rand"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/datacern-ai/query-service/internal/api"
	"github.com/datacern-ai/query-service/internal/authz"
	"github.com/datacern-ai/query-service/internal/datasets"
	"github.com/datacern-ai/query-service/internal/engine"
	"github.com/datacern-ai/query-service/internal/events"
	"github.com/datacern-ai/query-service/internal/exec"
	"github.com/datacern-ai/query-service/internal/register"
	"github.com/datacern-ai/query-service/internal/results"
	"github.com/datacern-ai/query-service/internal/store"

	"github.com/datacern-ai/go-common/dbcheck"
	gcoutbox "github.com/datacern-ai/go-common/outbox"
	"github.com/datacern-ai/go-common/otelx"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	slog.SetDefault(slog.New(otelx.WrapLogHandler(slog.NewJSONHandler(os.Stdout, nil)))) // MASTER-FR-050 structured logs

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless DATACERN_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "query-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	// Stabilization guard (rule: no fake/mock/stub in a runtime path). When
	// REQUIRE_REAL_ADAPTERS=true — set in every real deploy — the service REFUSES
	// to boot on an in-memory/fake adapter instead of silently faking success.
	// Absent (local unit dev), the loud-warn fallbacks below keep dev
	// self-contained.
	requireReal := os.Getenv("REQUIRE_REAL_ADAPTERS") == "true"
	mustReal := func(realEnv, adapter string) {
		slog.Error("REQUIRE_REAL_ADAPTERS=true but " + realEnv + " is unset — refusing to boot on the " + adapter + " fallback")
		os.Exit(1)
	}

	dbURL := env("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/query?sslmode=disable")
	// Migrations need DDL/ownership + role creation, so they run under a
	// privileged role (MIGRATE_DATABASE_URL, default = DATABASE_URL). The runtime
	// pool connects as DATABASE_URL, which in a hardened deploy is a NON-superuser
	// app role (query_app) so FORCE row-level security is actually enforced.
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
	// SEC-1 (BRD 58): fail closed if the runtime role can bypass RLS.
	if err := dbcheck.AssertNonSuperuser(ctx, pool); err != nil {
		slog.Error("refusing to start", "err", err)
		os.Exit(1)
	}
	st := store.NewPG(pool)

	resultsRoot := env("RESULTS_ROOT", "/var/lib/query-service")
	resStore := results.NewStore(resultsRoot)

	var resolver datasets.Resolver
	if base := os.Getenv("DATASET_SERVICE_URL"); base != "" {
		resolver = datasets.NewHTTP(base)
	} else {
		if requireReal {
			mustReal("DATASET_SERVICE_URL", "in-memory static dataset resolver")
		}
		slog.Warn("DATASET_SERVICE_URL unset; using empty static resolver (dev only)")
		resolver = datasets.NewStatic()
	}

	engines := engine.NewRegistry(
		&engine.DuckDB{
			Path:        env("DUCKDB_PATH", ""),
			ReadOnly:    os.Getenv("DUCKDB_READONLY") == "true",
			S3Endpoint:  env("S3_ENDPOINT", "localhost:9000"),
			S3Region:    env("AWS_REGION", "us-east-1"),
			S3AccessKey: os.Getenv("AWS_ACCESS_KEY_ID"),
			S3SecretKey: os.Getenv("AWS_SECRET_ACCESS_KEY"),
			S3UseSSL:    os.Getenv("S3_USE_SSL") == "true",
		},
		&engine.Trino{
			Endpoint: os.Getenv("TRINO_ENDPOINT"),
			User:     env("TRINO_USER", "datacern"),
			Catalog:  env("TRINO_CATALOG", "iceberg"),
			Source:   "query-service",
		},
		&engine.Warehouse{Cloud: env("CELL_CLOUD", "aws"), Up: os.Getenv("WAREHOUSE_ENABLED") == "true"},
	)

	// Semantic auto-materialization schemas (QRY-FR-005): comma-separated
	// schema allowlist (e.g. "main"). Unset -> feature inert (prod default).
	autoSchemas := map[string]bool{}
	for _, s := range strings.Split(os.Getenv("DUCKDB_AUTOMATERIALIZE_SCHEMAS"), ",") {
		if s = strings.ToLower(strings.TrimSpace(s)); s != "" {
			autoSchemas[s] = true
		}
	}

	broker := &exec.Broker{
		Store:                  st,
		Resolver:               resolver,
		Engines:                engines,
		Results:                resStore,
		Slots:                  exec.NewSlotManager(),
		AutoMaterializeSchemas: autoSchemas,
	}
	// Note: the tenant guard admits an auto-materialized table's schema at
	// plan time only when that table resolves to a governed dataset — a
	// tighter grant than a blanket ExtraNamespaces allowlist (BR-2).

	secret := []byte(os.Getenv("EXPORT_SIGNING_SECRET"))
	if len(secret) == 0 {
		secret = make([]byte, 32)
		if _, err := rand.Read(secret); err != nil {
			slog.Error("secret generation failed", "err", err)
			os.Exit(1)
		}
		slog.Warn("EXPORT_SIGNING_SECRET unset; generated ephemeral secret (links break on restart)")
	}

	// Real authorization: the OPA sidecar evaluates the datacern.authz_input
	// bundle over the caller's Redis permissions_flat projection (MASTER-FR-012).
	// No allow-all escape hatch in the runtime path — the permissive fake lives
	// only in unit tests.
	az := authz.NewOPAClient(env("OPA_URL", "http://localhost:8281"), env("REDIS_ADDR", "localhost:6379"))

	var verifier *api.Verifier
	jwks := env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json")
	verifier = api.NewVerifierJWKS(jwks, os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	srv := &api.Server{
		Store:        st,
		Broker:       broker,
		Results:      resStore,
		Authz:        az,
		Verifier:     verifier,
		ExportSecret: secret,
		Datasets:     api.NewHTTPDatasetNamer(os.Getenv("DATASET_SERVICE_URL")),
	}

	// Deploy-time action-catalog registration (RBC-FR-022): push the guarded
	// action manifest to rbac so OPA's catalog knows each action
	// (`action_known`). FAIL LOUDLY (M1 hardening): a rejected manifest means
	// OPA denies every guarded route, so /readyz reports degraded until
	// registration succeeds — never a silent warn-and-continue.
	regCfg := register.Config{
		RBACURL:       os.Getenv("RBAC_URL"),
		SigningKeyPEM: os.Getenv("REGISTER_SIGNING_KEY_PEM"),
		SigningKID:    os.Getenv("REGISTER_SIGNING_KID"),
		Issuer:        os.Getenv("JWT_ISSUER"),
		Audience:      os.Getenv("JWT_AUDIENCE"),
		TenantID:      os.Getenv("REGISTER_TENANT_ID"),
	}
	if regCfg.RBACURL == "" || regCfg.SigningKeyPEM == "" {
		slog.Warn("action registration skipped (RBAC_URL or REGISTER_SIGNING_KEY_PEM unset; dev only) — guarded routes will 403 under real OPA")
	} else {
		gate := api.NewRegGate()
		srv.RegGate = gate
		go func() {
			for {
				err := register.Register(ctx, regCfg)
				if err == nil {
					gate.Succeed()
					return
				}
				slog.Error("rbac action-catalog registration FAILED; /readyz degraded until it succeeds", "err", err)
				gate.Fail(err.Error())
				select {
				case <-ctx.Done():
					return
				case <-time.After(30 * time.Second):
				}
			}
		}()
	}

	// Outbox relay (MASTER-FR-034): drains committed rows to the real
	// go-common Kafka producer (Redpanda) by default. KAFKA_BROKERS=false
	// selects the in-memory publisher for broker-less local dev only.
	var pub events.Publisher
	brokers := env("KAFKA_BROKERS", "localhost:9092")
	if brokers == "false" {
		if requireReal {
			mustReal("KAFKA_BROKERS", "in-memory publisher (outbox events would not reach Kafka)")
		}
		slog.Warn("KAFKA_BROKERS=false; using in-memory publisher (events are not durable; local dev only)")
		pub = events.NewInMemory()
	} else {
		kp := events.NewKafkaPublisher(ctx, strings.Split(brokers, ","), os.Getenv("SCHEMA_REGISTRY_URL"))
		defer func() { _ = kp.Close() }()
		pub = kp
		slog.Info("publisher: kafka (shared go-common producer)", "brokers", brokers)
	}
	relay := &events.Relay{Source: st, Publisher: pub, Interval: time.Second}
	go relay.Run(ctx)
	// B6 (BRD 58): published outbox rows are drained but never pruned; sweep
	// them past a retention window so the table doesn't grow unboundedly.
	go gcoutbox.NewPruner(pool, "outbox", "app.role", "platform").Run(ctx)

	// Result retention GC (QRY-FR-062: 24h then GC; history row persists).
	go func() {
		t := time.NewTicker(15 * time.Minute)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				if freed, err := resStore.GC(24 * time.Hour); err != nil {
					slog.Warn("result GC failed", "err", err)
				} else if freed > 0 {
					slog.Info("result GC", "bytes_freed", freed)
				}
			}
		}
	}()

	addr := env("LISTEN_ADDR", ":8080")
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(srv.Router(), "query-service"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
		broker.Wait()
	}()
	slog.Info("query-service listening", "addr", addr)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("server failed", "err", err)
		os.Exit(1)
	}
}
