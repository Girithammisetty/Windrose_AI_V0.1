// Command server runs usage-service: the platform's metering, cost-attribution
// and budget-enforcement authority (BRD 17). Every adapter is real by default —
// real Postgres (meter store + rollups), real Redpanda (Kafka ingest + outbox),
// real Redis (dedup + counters), real OPA sidecar (authz). There is no env flag
// that swaps a real adapter for a fake; doubles exist only in *_test.go.
package main

import (
	"context"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"

	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/otelx"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/usage-service/internal/api"
	"github.com/windrose-ai/usage-service/internal/authz"
	"github.com/windrose-ai/usage-service/internal/events"
	"github.com/windrose-ai/usage-service/internal/ingest"
	"github.com/windrose-ai/usage-service/internal/jobs"
	"github.com/windrose-ai/usage-service/internal/metrics"
	"github.com/windrose-ai/usage-service/internal/register"
	"github.com/windrose-ai/usage-service/internal/store"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil))) // MASTER-FR-050

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless WINDROSE_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "usage-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	// Migrations run as the owner/admin role (creates the non-owner runtime
	// role usage_app); the service pool connects as usage_app (RLS applies).
	adminURL := env("MIGRATE_DATABASE_URL", "postgres://windrose:windrose_dev@localhost:5432/usage?sslmode=disable")
	if err := store.Migrate(adminURL); err != nil {
		slog.Error("migrations failed", "err", err)
		os.Exit(1)
	}
	dbURL := env("DATABASE_URL", "postgres://usage_app:usage_app@localhost:5432/usage?sslmode=disable")
	slog.Info("db adapter: postgres (real)", "runtime_role", roleOf(dbURL))
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
	if err := st.SeedMeters(ctx); err != nil {
		slog.Error("seed meters failed", "err", err)
		os.Exit(1)
	}

	m := metrics.New(prometheus.DefaultRegisterer)

	// Real Redis (dedup + counters). No in-memory mode.
	redis := redisx.NewFromEnv(env("REDIS_ADDR", "localhost:6379"), os.Getenv)
	defer func() { _ = redis.Close() }()
	slog.Info("cache adapter: redis (real)", "addr", env("REDIS_ADDR", "localhost:6379"))

	// Real authz via the OPA sidecar over the Redis permissions_flat projection.
	az := authz.NewOPAClient(env("OPA_URL", "http://localhost:8281"), env("REDIS_ADDR", "localhost:6379"))
	slog.Info("authz adapter: opa sidecar (real)", "opa", env("OPA_URL", "http://localhost:8281"))

	// Real JWKS verification (MASTER-FR-010).
	verifier := api.NewVerifierJWKS(
		env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	// Real Kafka (Redpanda): one shared producer for the emit publisher and the
	// ingest DLQ.
	brokers := strings.Split(env("KAFKA_BROKERS", "localhost:9092"), ",")
	srURL := os.Getenv("SCHEMA_REGISTRY_URL")
	producer := gckafka.NewProducer(gckafka.Config{
		Brokers: brokers,
		SASL:    gckafka.SASLFromEnv(os.Getenv), TLS: gckafka.TLSFromEnv(os.Getenv),
	})
	defer func() { _ = producer.Close() }()
	kpub := events.NewKafkaPublisher(ctx, brokers, srURL)
	defer func() { _ = kpub.Close() }()
	slog.Info("event adapter: kafka (real)", "brokers", brokers)

	// Ingest pipeline (mapping catalog validated at startup, USG-FR-015).
	mappings := ingest.Catalog()
	if err := ingest.ValidateCatalog(mappings); err != nil {
		slog.Error("mapping catalog invalid", "err", err)
		os.Exit(1)
	}
	pipeline := ingest.NewPipeline(mappings, st, st, m)

	// Real inbound consumer group (Redis dedup + DLQ) over the metering topics.
	consumer := events.NewIngestConsumer(brokers, redis, producer, pipeline)
	defer func() { _ = consumer.Close() }()
	go consumer.Run(ctx)

	// Outbox relay drains committed budget/anomaly/reconciliation events to
	// usage.events.v1 (MASTER-FR-034).
	relay := &events.Relay{Source: st, Publisher: kpub, Interval: 500 * time.Millisecond}
	go relay.Run(ctx)

	// Periodic workers.
	runner := &jobs.Runner{Store: st}
	startJobs(ctx, runner)

	// Register the action manifest with rbac (best-effort, RBC-FR-022).
	go func() {
		err := register.Register(ctx, register.Config{
			RBACURL:       os.Getenv("RBAC_URL"),
			SigningKeyPEM: os.Getenv("SERVICE_SIGNING_KEY_PEM"),
			SigningKID:    os.Getenv("SERVICE_SIGNING_KID"),
			Issuer:        os.Getenv("JWT_ISSUER"),
			Audience:      os.Getenv("JWT_AUDIENCE"),
			TenantID:      env("PLATFORM_TENANT_ID", "00000000-0000-0000-0000-000000000000"),
		})
		if err != nil {
			slog.Warn("action registration failed", "err", err)
		}
	}()

	srv := &api.Server{
		Store:    st,
		Authz:    az,
		Verifier: verifier,
		Ready: func(ctx context.Context) error {
			if err := st.Ping(ctx); err != nil {
				return err
			}
			return redis.Ping(ctx)
		},
	}

	addr := env("LISTEN_ADDR", ":8080")
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(srv.Router(), "usage-service"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
	}()
	slog.Info("usage-service listening", "addr", addr)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("server failed", "err", err)
		os.Exit(1)
	}
}

// startJobs launches the periodic workers as real background loops.
func startJobs(ctx context.Context, r *jobs.Runner) {
	every(ctx, time.Minute, func() {
		if err := r.RefreshRollups(ctx); err != nil {
			slog.Warn("rollup refresh failed", "err", err)
		}
		if err := r.SweepBudgets(ctx); err != nil {
			slog.Warn("budget sweep failed", "err", err)
		}
	})
	every(ctx, 30*time.Minute, func() {
		if _, err := r.AnomalyScan(ctx, time.Now().AddDate(0, 0, -1)); err != nil {
			slog.Warn("anomaly scan failed", "err", err)
		}
	})
	every(ctx, 6*time.Hour, func() {
		if err := r.EnforceRetention(ctx); err != nil {
			slog.Warn("retention failed", "err", err)
		}
	})
}

func every(ctx context.Context, d time.Duration, fn func()) {
	go func() {
		t := time.NewTicker(d)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				fn()
			}
		}
	}()
}

func roleOf(dsn string) string {
	u, err := url.Parse(dsn)
	if err != nil || u.User == nil {
		return "unknown"
	}
	return u.User.Username()
}
