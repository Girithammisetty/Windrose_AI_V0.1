// rbac-service server: HTTP API + projection recompute worker + outbox relay
// (+ optional Kafka consumers when brokers are configured).
package main

import (
	"context"
	"crypto/x509"
	"encoding/pem"
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

	"crypto/rsa"
	"crypto/tls"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/redis/go-redis/v9"

	"github.com/datacern-ai/rbac-service/internal/api"
	"github.com/datacern-ai/rbac-service/internal/authz"
	"github.com/datacern-ai/rbac-service/internal/domain"
	"github.com/datacern-ai/rbac-service/internal/events"
	"github.com/datacern-ai/rbac-service/internal/projection"
	"github.com/datacern-ai/rbac-service/internal/store"
	"github.com/datacern-ai/rbac-service/seed"

	gcoutbox "github.com/datacern-ai/go-common/outbox"
	"github.com/datacern-ai/go-common/otelx"
)

func main() {
	if err := run(); err != nil {
		slog.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// redisOptions builds go-redis options from REDIS_ADDR plus the platform's
// standard auth/TLS env vars (REDIS_USERNAME, REDIS_PASSWORD, REDIS_TLS), so a
// tenant can point rbac-service at a managed, authenticated Redis-compatible
// service (ElastiCache/Azure Cache/Memorystore) via configuration alone.
func redisOptions() *redis.Options {
	opts := &redis.Options{
		Addr:     env("REDIS_ADDR", "localhost:6379"),
		Username: os.Getenv("REDIS_USERNAME"),
		Password: os.Getenv("REDIS_PASSWORD"),
	}
	switch strings.ToLower(os.Getenv("REDIS_TLS")) {
	case "1", "true", "yes":
		opts.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	}
	return opts
}

// stalenessHistogram implements projection.StalenessRecorder (RBC-FR-042 SLI).
type stalenessHistogram struct{ h prometheus.Histogram }

func (s stalenessHistogram) Observe(d time.Duration) { s.h.Observe(d.Seconds()) }

func run() error {
	slog.SetDefault(slog.New(otelx.WrapLogHandler(slog.NewJSONHandler(os.Stdout, nil))))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless DATACERN_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "rbac-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	// Stabilization guard (rule: no fake/mock/stub in a runtime path). When
	// REQUIRE_REAL_ADAPTERS=true — set in every real deploy — the service REFUSES
	// to boot on an in-memory/fake adapter instead of silently faking success.
	// Absent (local unit dev), the loud-warn fallbacks below keep dev
	// self-contained. This is what stops a misconfigured prod (e.g. a secret that
	// ships KAFKA_BROKERS=false) from coming up "healthy" while dropping events
	// into a map.
	requireReal := os.Getenv("REQUIRE_REAL_ADAPTERS") == "true"
	mustReal := func(realEnv, adapter string) {
		slog.Error("REQUIRE_REAL_ADAPTERS=true but " + realEnv + " is unset — refusing to boot on the " + adapter + " fallback")
		os.Exit(1)
	}

	dbURL := env("DATABASE_URL", "postgres://rbac:rbac@localhost:5432/rbac?sslmode=disable")
	if env("RUN_MIGRATIONS", "true") == "true" {
		migrateURL := env("MIGRATE_DATABASE_URL", dbURL) // schema-owner URL
		if err := store.Migrate(migrateURL); err != nil {
			return fmt.Errorf("migrate: %w", err)
		}
	}

	poolCfg, err := pgxpool.ParseConfig(dbURL)
	if err != nil {
		return fmt.Errorf("db pool: %w", err)
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
		return fmt.Errorf("db pool: %w", err)
	}
	defer pool.Close()
	st := store.New(pool)

	rdb := redis.NewClient(redisOptions())
	defer func() { _ = rdb.Close() }()

	// Seed system roles + canonical action catalog (idempotent, deploy-time).
	if err := st.RegisterActions(ctx, domain.CanonicalCatalog()); err != nil {
		return fmt.Errorf("register canonical actions: %w", err)
	}
	seeds, err := domain.ParseRoleSeeds(seed.RolesActionsYAML)
	if err != nil {
		return fmt.Errorf("parse role seeds: %w", err)
	}
	if err := st.EnsureSystemRoles(ctx, seeds); err != nil {
		return fmt.Errorf("seed system roles: %w", err)
	}

	writer := projection.NewRedisWriter(rdb, projection.DefaultTTL)
	if catalog, err := st.CatalogMap(ctx); err == nil {
		if v, verr := st.NextVersion(ctx); verr == nil {
			if werr := writer.WriteCatalog(ctx, catalog, v); werr != nil {
				slog.Warn("catalog projection write failed", "err", werr)
			}
		}
	}

	reader := projection.NewRedisReader(rdb)
	reader.OnNearExpiry = func(tenant, user string) {
		if t, err := uuid.Parse(tenant); err == nil {
			_ = st.MarkDirty(context.Background(), t, []string{user}, "refresh_on_read")
		}
	}

	// Metrics.
	stalenessH := prometheus.NewHistogram(prometheus.HistogramOpts{
		Name:    "rbac_projection_staleness_seconds",
		Help:    "Enqueue-to-Redis-write staleness of projection recomputes (SLO: <=5s p99).",
		Buckets: []float64{0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30},
	})
	fallbackC := prometheus.NewCounter(prometheus.CounterOpts{
		Name: "rbac_authz_fallback_total",
		Help: "SQL-fallback authz checks (sustained rate > 0.1% alerts).",
	})
	prometheus.MustRegister(stalenessH, fallbackC)

	userLock := projection.NewUserLock(rdb, projection.DefaultLockTTL)
	checker := &authz.Checker{Store: st, Writer: writer, Lock: userLock, OnFallback: fallbackC.Inc}

	// Projection recompute worker (RBC-FR-042/048).
	worker := projection.NewWorker(env("HOSTNAME", "rbac-worker"), st, writer)
	worker.Lock = userLock
	worker.Staleness = stalenessHistogram{h: stalenessH}
	go worker.Run(ctx)

	// Outbox relay -> Kafka via the shared libs/go-common producer (Redpanda).
	// KAFKA_BROKERS defaults to the local broker so the runtime path is real;
	// KAFKA_BROKERS=false selects the in-memory publisher (local dev only).
	var pub events.EventPublisher
	brokers := env("KAFKA_BROKERS", "localhost:9092")
	if brokers == "false" {
		if requireReal {
			mustReal("KAFKA_BROKERS", "in-memory event publisher (outbox events would not reach Kafka)")
		}
		slog.Warn("KAFKA_BROKERS=false; using in-memory event publisher (events are not durable)")
		pub = events.NewInMemoryPublisher()
	} else {
		pub = events.NewGoCommonPublisher(ctx, strings.Split(brokers, ","), env("SCHEMA_REGISTRY_URL", ""))
		slog.Info("publisher: kafka (shared go-common producer)", "brokers", brokers)
	}
	defer func() { _ = pub.Close() }()
	relay := events.NewOutboxRelay(st, pub)
	go relay.Run(ctx)
	// B6 (BRD 58): published outbox rows are drained but never pruned; sweep
	// them past a retention window so the table doesn't grow unboundedly.
	go gcoutbox.NewPruner(pool, "outbox", "app.worker", "on").Run(ctx)

	// Inbound consumers (identity + case events: *.created implicit creator
	// grants and case.assigned/unassigned implicit editor grants — the
	// per-resource obo-grants tool-plane's write gate intersects against).
	if brokers != "false" {
		topics := strings.Split(env("CONSUME_TOPICS", "identity.events.v1,case.events.v1"), ",")
		handler := &events.Handler{Store: &store.ConsumerAdapter{S: st, DropProjection: writer.DropUser}, Log: slog.Default()}
		consumer := events.NewKafkaConsumer(strings.Split(brokers, ","), "rbac-service", topics, handler, rdb, pub)
		go consumer.Run(ctx)
		defer func() { _ = consumer.Close() }()
	}

	verifier, err := buildVerifier()
	if err != nil {
		return err
	}

	srv := &api.Server{Store: st, Checker: checker, Writer: writer, Reader: reader, Verifier: verifier, Redis: rdb}
	httpSrv := &http.Server{
		Addr:              env("LISTEN_ADDR", ":8080"),
		Handler:           otelx.WrapHandler(srv.Router(), "rbac-service"),
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
	}()
	slog.Info("rbac-service listening", "addr", httpSrv.Addr)
	if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func buildVerifier() (*api.Verifier, error) {
	issuer := env("AUTH_ISSUER", "")
	audience := env("AUTH_AUDIENCE", "")
	if jwks := env("AUTH_JWKS_URL", ""); jwks != "" {
		return api.NewVerifierJWKS(jwks, issuer, audience), nil
	}
	if pemStr := env("AUTH_PUBLIC_KEY_PEM", ""); pemStr != "" {
		block, _ := pem.Decode([]byte(pemStr))
		if block == nil {
			return nil, errors.New("AUTH_PUBLIC_KEY_PEM: invalid PEM")
		}
		key, err := x509.ParsePKIXPublicKey(block.Bytes)
		if err != nil {
			return nil, fmt.Errorf("AUTH_PUBLIC_KEY_PEM: %w", err)
		}
		rsaKey, ok := key.(*rsa.PublicKey)
		if !ok {
			return nil, errors.New("AUTH_PUBLIC_KEY_PEM: not an RSA key")
		}
		return api.NewVerifierStatic(rsaKey, issuer, audience), nil
	}
	return nil, errors.New("set AUTH_JWKS_URL or AUTH_PUBLIC_KEY_PEM")
}
