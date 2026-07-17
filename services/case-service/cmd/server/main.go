// Command server runs case-service: the claims-triage service (BRD 08). It
// owns row-reference triage cases, the lifecycle state machine, SLA timers,
// dispositions, bulk ops, the OpenSearch projection and the copilot proposal
// application endpoints. Every adapter is real: Postgres (RLS), Redpanda
// (Kafka outbox + search-index consumer), Redis+OPA (authz), OpenSearch
// (list/search), a durable Postgres-backed SLA sweep worker (Temporal-
// equivalent when Temporal is absent), and object storage for closure snapshots.
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

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/otelx"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/case-service/internal/api"
	"github.com/windrose-ai/case-service/internal/authz"
	"github.com/windrose-ai/case-service/internal/blob"
	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
	"github.com/windrose-ai/case-service/internal/register"
	"github.com/windrose-ai/case-service/internal/search"
	"github.com/windrose-ai/case-service/internal/sla"
	"github.com/windrose-ai/case-service/internal/store"
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
	otelShutdown := otelx.InitFromEnv(ctx, "case-service")
	defer func() { _ = otelShutdown(context.Background()) }()

	dbURL := env("DATABASE_URL", "postgres://windrose:windrose_dev@localhost:5432/case?sslmode=disable")
	// Migrations need DDL/ownership + role creation, so they run under a
	// privileged role (MIGRATE_DATABASE_URL, default = DATABASE_URL). The runtime
	// pool connects as DATABASE_URL, which in a hardened deploy is a NON-superuser
	// app role (case_app) so FORCE row-level security is actually enforced —
	// a superuser/BYPASSRLS runtime role would silently defeat tenant isolation.
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

	// Real OpenSearch adapter (CASE-FR-040): list/search/facets + projection.
	searchClient, err := search.New(env("OPENSEARCH_URL", "http://localhost:9200"))
	if err != nil {
		slog.Error("opensearch client", "err", err)
		os.Exit(1)
	}
	projector := &search.Projector{Store: st, Search: searchClient}

	// Real authorization via the OPA sidecar + Redis projection (MASTER-FR-012).
	az := authz.NewOPAClient(env("OPA_URL", "http://localhost:8281"), env("REDIS_ADDR", "localhost:6379"))

	// Object storage for case evidence attachments (task #77): real MinIO/S3,
	// same adapter audit-service uses, minus object-lock. Fatal if unreachable —
	// evidence upload/download is a real capability, not a best-effort side path.
	evidence, err := blob.NewMinioEvidence(ctx, blob.Config{
		Endpoint:  env("MINIO_ENDPOINT", "localhost:9000"),
		AccessKey: env("MINIO_ACCESS_KEY", "windrose"),
		SecretKey: env("MINIO_SECRET_KEY", "windrose_dev"),
		UseSSL:    os.Getenv("MINIO_USE_SSL") == "true",
		Bucket:    env("CASE_EVIDENCE_BUCKET", "windrose-case-evidence"),
	})
	if err != nil {
		slog.Error("case evidence store init", "err", err)
		os.Exit(1)
	}

	verifier := api.NewVerifierJWKS(
		env("JWKS_URL", "http://identity-service/api/v1/.well-known/jwks.json"),
		os.Getenv("JWT_ISSUER"), os.Getenv("JWT_AUDIENCE"))

	srv := &api.Server{
		Store:      st,
		Search:     searchClient,
		Projector:  projector,
		Authz:      az,
		Verifier:   verifier,
		RowFetcher: api.NewHTTPRowFetcher(os.Getenv("QUERY_SERVICE_URL")),
		Snapshots:  api.NewFSSnapshotStore(env("SNAPSHOT_ROOT", "/var/lib/case-service/snapshots")),
		Evidence:   evidence,
		Redis:      redisx.NewFromEnv(env("REDIS_ADDR", "localhost:6379"), os.Getenv), // bulk concurrency gate (CASE-FR-032)
	}

	// Deploy-time action-catalog registration (RBC-FR-022): push case-service's
	// action manifest to rbac so OPA's catalog knows each action (`action_known`).
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

	// Outbox relay → real Kafka (MASTER-FR-034). KAFKA_BROKERS=false selects the
	// in-memory publisher for broker-less local dev only.
	brokers := env("KAFKA_BROKERS", "localhost:9092")
	var pub events.Publisher
	if brokers == "false" {
		slog.Warn("KAFKA_BROKERS=false; in-memory publisher (events not durable; dev only)")
		pub = events.NewInMemory()
	} else {
		kp := events.NewKafkaPublisher(ctx, strings.Split(brokers, ","), os.Getenv("SCHEMA_REGISTRY_URL"))
		defer func() { _ = kp.Close() }()
		pub = kp
		slog.Info("publisher: kafka (shared go-common producer)", "brokers", brokers)
	}
	relay := &events.Relay{Source: st, Publisher: pub, Interval: 250 * time.Millisecond}
	go relay.Run(ctx)

	// Search-index consumer: reprojects cases into OpenSearch from case.events.v1
	// (CASE-FR-041, ≤5s eventual). Real Kafka consumer group + Redis dedup.
	if brokers != "false" {
		rc := redisx.NewFromEnv(env("REDIS_ADDR", "localhost:6379"), os.Getenv)
		kafkaSASL, kafkaTLS := gckafka.SASLFromEnv(os.Getenv), gckafka.TLSFromEnv(os.Getenv)
		dlq := gckafka.NewProducer(gckafka.Config{Brokers: strings.Split(brokers, ","), SASL: kafkaSASL, TLS: kafkaTLS})
		idxConsumer := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
			Brokers: strings.Split(brokers, ","), GroupID: "case-search-indexer",
			Topics: []string{events.Topic}, Handler: events.SearchIndexHandler(projector),
			Dedup: rc, DLQ: dlq,
			SASL: kafkaSASL, TLS: kafkaTLS,
		})
		go idxConsumer.Run(ctx)
		defer func() { _ = idxConsumer.Close() }()

		// Inbound consumers: inference auto-case + identity unassign (§6).
		creator := &creatorAdapter{store: st}
		inboundHandler := func(ctx context.Context, e gcevent.Envelope) error {
			if err := events.InferenceHandler(creator)(ctx, e); err != nil {
				return err
			}
			return events.IdentityHandler(creator)(ctx, e)
		}
		inbound := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
			Brokers: strings.Split(brokers, ","), GroupID: "case-inbound",
			Topics: []string{"inference.events.v1", "identity.events.v1", "rbac.events.v1"},
			Handler: inboundHandler, Dedup: rc, DLQ: dlq,
			SASL: kafkaSASL, TLS: kafkaTLS,
		})
		go inbound.Run(ctx)
		defer func() { _ = inbound.Close() }()
	}

	// Durable SLA sweep worker (CASE-FR-012/013). When Temporal is available it
	// runs SLA as a workflow; without it, this Postgres-backed sweep provides the
	// same durability (AC-4).
	slaWorker := sla.New(st)
	go slaWorker.Run(ctx)

	addr := env("LISTEN_ADDR", ":8084")
	httpSrv := &http.Server{Addr: addr, Handler: otelx.WrapHandler(srv.Router(), "case-service"), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(shutdownCtx)
	}()
	slog.Info("case-service listening", "addr", addr)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("server failed", "err", err)
		os.Exit(1)
	}
}

// creatorAdapter bridges inbound Kafka consumers to the store (§6).
type creatorAdapter struct {
	store *store.PG
}

// AutoCreateFromInference creates cases from an inference.completed payload with
// auto_case=true (CASE-FR-003).
func (a *creatorAdapter) AutoCreateFromInference(ctx context.Context, tenant uuid.UUID, payload map[string]any) error {
	wsRaw, _ := payload["workspace_id"].(string)
	ws, err := uuid.Parse(wsRaw)
	if err != nil {
		return nil
	}
	datasetURN, _ := payload["dataset_urn"].(string)
	queryURN, _ := payload["query_urn"].(string)
	threshold, _ := payload["score_threshold"].(float64)
	rowsRaw, _ := payload["rows"].([]any)
	if datasetURN == "" || len(rowsRaw) == 0 {
		return nil
	}
	op := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "agent", ID: "inference"}}
	due := time.Now().Add(72 * time.Hour)
	var cases []*domain.Case
	now := time.Now().UTC()
	for _, rr := range rowsRaw {
		m, _ := rr.(map[string]any)
		if m == nil {
			continue
		}
		if score, ok := m["score"].(float64); ok && score < threshold {
			continue
		}
		rowPK, _ := m["row_pk"].(string)
		proj := map[string]string{}
		if p, ok := m["display_projection"].(map[string]any); ok {
			for k, v := range p {
				if sv, ok := v.(string); ok {
					proj[k] = sv
				}
			}
		}
		tproj, trunc := domain.TruncateProjection(proj)
		var dedup *string
		if k, ok := domain.DedupKey(datasetURN, rowPK); ok {
			dedup = &k
		}
		cases = append(cases, &domain.Case{
			ID: domain.NewID(), TenantID: tenant, WorkspaceID: ws, Status: domain.StatusUnassigned, Severity: domain.SeverityMedium,
			CreatedByID: "agent/inference", DatasetURN: datasetURN, RowPK: rowPK, DedupKey: dedup,
			DisplayProjection: tproj, ProjectionTruncated: trunc, SourceQueryURNs: []string{}, DueDate: due,
			CustomFields: map[string]any{}, CaseVersion: 1, CreatedAt: now, UpdatedAt: now,
		})
	}
	if len(cases) == 0 {
		return nil
	}
	_, _, err = a.store.CreateCases(ctx, op, cases, queryURN, 24*time.Hour)
	return err
}

func (a *creatorAdapter) UnassignUserCases(ctx context.Context, tenant, userID uuid.UUID) error {
	return a.store.UnassignUserCases(ctx, tenant, userID)
}
