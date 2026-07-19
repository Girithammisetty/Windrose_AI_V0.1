// Command server runs audit-service: the platform's immutable system of record
// (BRD 18). Every adapter is REAL by default with no env flag needed —
// ClickHouse (append-only store), Redpanda/Kafka (consume every domain + ai
// topic), Postgres (chain checkpoints + manifests, RLS as a non-owner role),
// Redis (dedup + chain counters), MinIO (WORM export), OPA + JWKS (admin authz).
// In-memory/fake doubles exist only in *_test.go and are unreachable from here.
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

	"github.com/windrose-ai/audit-service/internal/api"
	"github.com/windrose-ai/audit-service/internal/authz"
	"github.com/windrose-ai/audit-service/internal/chain"
	"github.com/windrose-ai/audit-service/internal/chstore"
	"github.com/windrose-ai/audit-service/internal/compliance"
	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/export"
	"github.com/windrose-ai/audit-service/internal/ingest"
	"github.com/windrose-ai/audit-service/internal/meta"
	"github.com/windrose-ai/audit-service/internal/pgstore"
	"github.com/windrose-ai/audit-service/internal/register"
	"github.com/windrose-ai/audit-service/internal/siemexport"
	"github.com/windrose-ai/audit-service/internal/worm"
	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/otelx"
	"github.com/windrose-ai/go-common/redisx"
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
	otelShutdown := otelx.InitFromEnv(ctx, "audit-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	// --- Postgres metadata: bootstrap DB + non-owner runtime role, migrate ---
	adminDSN := env("ADMIN_DATABASE_URL", "postgres://windrose:windrose_dev@localhost:5432/windrose?sslmode=disable")
	dbName := env("AUDIT_DB_NAME", "audit")
	runtimeRole := env("DB_RUNTIME_ROLE", "audit_rw")
	runtimePass := env("DB_RUNTIME_PASSWORD", "audit_rw_dev")
	// The shipped default runtime DSN connects as the NON-owner audit_rw role.
	dbURL := env("DATABASE_URL", "postgres://audit_rw:audit_rw_dev@localhost:5432/audit?sslmode=disable")

	if err := pgstore.Bootstrap(ctx, adminDSN, dbName, runtimeRole, runtimePass); err != nil {
		slog.Error("postgres bootstrap failed", "err", err)
		os.Exit(1)
	}
	ownerDSN, err := pgstore.ReplaceDBAndUser(adminDSN, "", "", dbName)
	if err != nil {
		slog.Error("owner dsn derive failed", "err", err)
		os.Exit(1)
	}
	if err := pgstore.Migrate(ownerDSN); err != nil {
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
	pg := pgstore.New(pool)

	// --- ClickHouse append-only store ---
	ch, err := chstore.Open(ctx, chstore.Config{
		Addr:     env("CLICKHOUSE_ADDR", "localhost:9010"),
		Database: env("CLICKHOUSE_DB", "audit"),
		Username: env("CLICKHOUSE_USER", "windrose"),
		Password: env("CLICKHOUSE_PASSWORD", "windrose_dev"),
	})
	if err != nil {
		slog.Error("clickhouse connect failed", "err", err)
		os.Exit(1)
	}
	defer ch.Close()
	if err := ch.Migrate(ctx); err != nil {
		slog.Error("clickhouse migrate failed", "err", err)
		os.Exit(1)
	}

	// --- Redis (dedup + chain counters) ---
	redis := redisx.NewFromEnv(env("REDIS_ADDR", "localhost:6379"), os.Getenv)
	defer redis.Close()

	// --- Kafka producer (DLQ + meta events) ---
	brokers := strings.Split(env("KAFKA_BROKERS", "localhost:9092"), ",")
	producer := gckafka.NewProducer(gckafka.Config{
		Brokers: brokers,
		SASL:    gckafka.SASLFromEnv(os.Getenv), TLS: gckafka.TLSFromEnv(os.Getenv),
	})
	defer producer.Close()
	metaEmitter := meta.New(producer)
	// Additive SIEM export sink (Phase 3, docs/design/siem-export.md): reuses
	// the SAME Kafka producer already wired for DLQ + meta events — no new
	// Kafka client. Best-effort; never affects ingest, the hash chain, or WORM.
	siemExporter := siemexport.New(producer)

	// --- WORM object storage (MinIO) ---
	wormClient, err := worm.New(worm.Config{
		Endpoint:  env("MINIO_ENDPOINT", "localhost:9000"),
		AccessKey: env("MINIO_ACCESS_KEY", "windrose"),
		SecretKey: env("MINIO_SECRET_KEY", "windrose_dev"),
		UseSSL:    os.Getenv("MINIO_USE_SSL") == "true",
		Bucket:    env("AUDIT_BUCKET", "windrose-audit"),
	})
	if err != nil {
		slog.Error("minio client failed", "err", err)
		os.Exit(1)
	}
	if err := wormClient.EnsureBucket(ctx); err != nil {
		slog.Warn("audit bucket ensure failed (WORM export degraded until fixed)", "err", err)
	}

	// --- Chain + ingest processor + real Kafka consumer ---
	chainMgr := chain.New(redis, pg, ch)
	proc := &ingest.Processor{CH: ch, Chain: chainMgr, Meta: metaEmitter, CleanAllow: nil, Export: siemExporter}
	sub, err := domain.NewSubscription(os.Getenv("SUBSCRIPTION_PATTERN"))
	if err != nil {
		slog.Error("bad subscription pattern", "err", err)
		os.Exit(1)
	}
	group := env("INGEST_GROUP", "audit-ingest")
	consumer := &ingest.Consumer{
		Brokers: brokers, GroupID: group, Sub: sub, Processor: proc,
		Dedup: redis, CH: ch, DLQ: producer, Meta: metaEmitter,
		RescanInterval: 60 * time.Second,
	}
	go consumer.Run(ctx)

	// --- WORM export scheduler ---
	exporter := &export.Exporter{CH: ch, PG: pg, WORM: wormClient, Meta: metaEmitter}
	scheduler := &export.Scheduler{Exporter: exporter, PG: pg, Interval: time.Hour}
	go scheduler.Run(ctx)

	// --- Authz (OPA sidecar + Redis projection) + JWKS verifier ---
	az := authz.NewOPAClient(env("OPA_URL", "http://localhost:8281"), env("REDIS_ADDR", "localhost:6379"))
	verifier := api.NewVerifierJWKS(
		env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	// --- Action-catalog registration with rbac ---
	go func() {
		if err := register.Register(ctx, register.Config{
			RBACURL:       os.Getenv("RBAC_URL"),
			SigningKeyPEM: os.Getenv("REGISTER_SIGNING_KEY_PEM"),
			SigningKID:    os.Getenv("REGISTER_SIGNING_KID"),
			Issuer:        os.Getenv("JWT_ISSUER"),
			Audience:      os.Getenv("JWT_AUDIENCE"),
			TenantID:      os.Getenv("REGISTER_TENANT_ID"),
		}); err != nil {
			slog.Warn("action catalog registration failed", "err", err)
		}
	}()

	srv := &api.Server{
		CH:          ch,
		PG:          pg,
		Redis:       redis,
		WORM:        wormClient,
		Compliance:  &compliance.Builder{CH: ch, WORM: wormClient},
		Redriver:    consumer,
		Meta:        metaEmitter,
		Authz:       az,
		Verifier:    verifier,
		IngestGroup: group,
		PresignTTL:  time.Hour,
	}

	addr := env("LISTEN_ADDR", ":8087")
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(srv.Router(), "audit-service"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
	}()
	slog.Info("audit-service listening", "addr", addr, "ingest_group", group)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("server failed", "err", err)
		os.Exit(1)
	}
}
