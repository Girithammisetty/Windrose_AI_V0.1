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

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"

	"github.com/datacern-ai/audit-service/internal/api"
	"github.com/datacern-ai/audit-service/internal/authz"
	"github.com/datacern-ai/audit-service/internal/chain"
	"github.com/datacern-ai/audit-service/internal/chstore"
	"github.com/datacern-ai/audit-service/internal/compliance"
	"github.com/datacern-ai/audit-service/internal/domain"
	"github.com/datacern-ai/audit-service/internal/export"
	"github.com/datacern-ai/audit-service/internal/ingest"
	"github.com/datacern-ai/audit-service/internal/meta"
	"github.com/datacern-ai/audit-service/internal/metrics"
	"github.com/datacern-ai/audit-service/internal/pgstore"
	"github.com/datacern-ai/audit-service/internal/register"
	"github.com/datacern-ai/audit-service/internal/siemexport"
	"github.com/datacern-ai/audit-service/internal/worm"
	gckafka "github.com/datacern-ai/go-common/kafka"
	"github.com/datacern-ai/go-common/otelx"
	"github.com/datacern-ai/go-common/redisx"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envBool(key string, def bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return def
	}
	return b
}

// envList parses a comma-separated env var into a trimmed, non-empty string
// slice (e.g. CLICKHOUSE_ADDRS="ch-0:9010,ch-1:9010,ch-2:9010" for an HA
// cluster, B9). Returns nil when unset, so callers fall back to the
// single-address CLICKHOUSE_ADDR.
func envList(key string) []string {
	v := os.Getenv(key)
	if v == "" {
		return nil
	}
	var out []string
	for _, part := range strings.Split(v, ",") {
		if p := strings.TrimSpace(part); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil))) // MASTER-FR-050

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Distributed tracing (no-op unless datacern_OTEL_ENABLED / an OTLP endpoint
	// is configured) — installs the global TracerProvider + W3C propagator.
	otelShutdown := otelx.InitFromEnv(ctx, "audit-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	// --- Postgres metadata: bootstrap DB + non-owner runtime role, migrate ---
	adminDSN := env("ADMIN_DATABASE_URL", "postgres://datacern:datacern_dev@localhost:5432/datacern?sslmode=disable")
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
		Addr: env("CLICKHOUSE_ADDR", "localhost:9010"),
		// CLICKHOUSE_ADDRS (comma-separated) targets an HA, Keeper-coordinated
		// cluster; CLICKHOUSE_REPLICATED then switches Migrate's DDL to
		// ReplicatedReplacingMergeTree (B9). Unset in dev/Hetzner: single-node
		// CLICKHOUSE_ADDR + ReplacingMergeTree, unchanged.
		Addrs:      envList("CLICKHOUSE_ADDRS"),
		Replicated: envBool("CLICKHOUSE_REPLICATED", false),
		Database:   env("CLICKHOUSE_DB", "audit"),
		Username:   env("CLICKHOUSE_USER", "datacern"),
		Password:   env("CLICKHOUSE_PASSWORD", "datacern_dev"),
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
	// BRD 59 WS2: per-tenant SIEM destination delivery, additive to the shared
	// Kafka topic above. SIEM_EXPORT_ALLOW_HTTP is the dev/e2e escape (mirrors
	// notification-service's WEBHOOK_ALLOW_HTTP) — never set in prod.
	siemExporter.Delivery = siemexport.NewHTTPDelivery(
		siemConfigLookup{store: pg}, env("SIEM_EXPORT_ALLOW_HTTP", "false") == "true")

	// --- WORM object storage (MinIO) ---
	wormClient, err := worm.New(worm.Config{
		Endpoint:  env("MINIO_ENDPOINT", "localhost:9000"),
		AccessKey: env("MINIO_ACCESS_KEY", "datacern"),
		SecretKey: env("MINIO_SECRET_KEY", "datacern_dev"),
		UseSSL:    os.Getenv("MINIO_USE_SSL") == "true",
		Bucket:    env("AUDIT_BUCKET", "datacern-audit"),
	})
	if err != nil {
		slog.Error("minio client failed", "err", err)
		os.Exit(1)
	}
	if err := wormClient.EnsureBucket(ctx); err != nil {
		slog.Warn("audit bucket ensure failed (WORM export degraded until fixed)", "err", err)
	}

	// --- Chain + ingest processor + real Kafka consumer ---
	auditMetrics := metrics.New(prometheus.DefaultRegisterer)
	chainMgr := chain.New(redis, pg, ch).WithMetrics(auditMetrics)
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
	scheduler := &export.Scheduler{Exporter: exporter, PG: pg, Interval: time.Hour, Metrics: auditMetrics}
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

// siemConfigLookup adapts *pgstore.Store to siemexport.ConfigLookup (BRD 59
// WS2) — siemexport stays free of a pgstore import (no dependency on pgx-
// specific types) by depending only on this narrow interface.
type siemConfigLookup struct{ store *pgstore.Store }

func (l siemConfigLookup) ActiveSiemConfigForDelivery(ctx context.Context, tenant uuid.UUID) (*siemexport.SiemDestination, error) {
	cfg, err := l.store.ActiveSiemConfigForDelivery(ctx, tenant)
	if err != nil || cfg == nil {
		return nil, err
	}
	return &siemexport.SiemDestination{
		Endpoint: cfg.Endpoint,
		Format:   siemexport.Format(cfg.Format),
		AuthRef:  cfg.AuthRef,
	}, nil
}
