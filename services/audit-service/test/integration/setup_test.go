//go:build integration

// Package integration is audit-service's Docker-backed tier. It runs against the
// REAL local infra in deploy/docker-compose.dev.yml — ClickHouse (append-only
// store), Redpanda/Kafka, Postgres (RLS as the non-owner audit_rw role), Redis,
// MinIO (WORM/Object-Lock) and the OPA sidecar. It auto-skips with a clear
// message when any component is unreachable. Test names follow TestACnn_*.
package integration

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"fmt"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/datacern-ai/audit-service/internal/api"
	"github.com/datacern-ai/audit-service/internal/authz"
	"github.com/datacern-ai/audit-service/internal/chain"
	"github.com/datacern-ai/audit-service/internal/chstore"
	"github.com/datacern-ai/audit-service/internal/compliance"
	"github.com/datacern-ai/audit-service/internal/export"
	"github.com/datacern-ai/audit-service/internal/ingest"
	"github.com/datacern-ai/audit-service/internal/meta"
	"github.com/datacern-ai/audit-service/internal/pgstore"
	"github.com/datacern-ai/audit-service/internal/worm"
	gckafka "github.com/datacern-ai/go-common/kafka"
	"github.com/datacern-ai/go-common/opaclient"
	"github.com/datacern-ai/go-common/redisx"
)

const (
	issuer   = "audit-it"
	audience = "datacern"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

type harness struct {
	pg        *pgstore.Store
	ch        *chstore.Store
	redis     *redisx.Client
	worm      *worm.Client
	chain     *chain.Manager
	proc      *ingest.Processor
	exporter  *export.Exporter
	consumer  *ingest.Consumer
	server    *api.Server
	http      *httptest.Server
	producer  *gckafka.Producer
	key       *rsa.PrivateKey
	brokers   []string
	group     string
	today     string
}

// newHarness wires the full stack against real infra, skipping if unavailable.
func newHarness(t *testing.T) *harness {
	t.Helper()
	if testing.Short() {
		t.Skip("integration test requires Docker infra; skipped under -short")
	}
	ctx := context.Background()

	adminDSN := env("ADMIN_DATABASE_URL", "postgres://datacern:datacern_dev@localhost:5432/datacern?sslmode=disable")
	if err := pgstore.Bootstrap(ctx, adminDSN, "audit", "audit_rw", "audit_rw_dev"); err != nil {
		t.Skipf("postgres unavailable (bootstrap): %v", err)
	}
	ownerDSN, _ := pgstore.ReplaceDBAndUser(adminDSN, "", "", "audit")
	if err := pgstore.Migrate(ownerDSN); err != nil {
		t.Skipf("postgres migrate failed: %v", err)
	}
	// Runtime pool as the NON-owner audit_rw role (default DSN).
	runtimeDSN := env("DATABASE_URL", "postgres://audit_rw:audit_rw_dev@localhost:5432/audit?sslmode=disable")
	pool, err := pgxpool.New(ctx, runtimeDSN)
	if err != nil {
		t.Skipf("postgres runtime connect failed: %v", err)
	}
	pg := pgstore.New(pool)

	ch, err := chstore.Open(ctx, chstore.Config{
		Addr:     env("CLICKHOUSE_ADDR", "localhost:9010"),
		Database: env("CLICKHOUSE_DB", "audit"),
		Username: env("CLICKHOUSE_USER", "datacern"),
		Password: env("CLICKHOUSE_PASSWORD", "datacern_dev"),
	})
	if err != nil {
		t.Skipf("clickhouse unavailable: %v", err)
	}
	if err := ch.Migrate(ctx); err != nil {
		t.Fatalf("clickhouse migrate: %v", err)
	}

	redis := redisx.New(env("REDIS_ADDR", "localhost:6379"))
	if err := redis.Ping(ctx); err != nil {
		t.Skipf("redis unavailable: %v", err)
	}

	bucket := fmt.Sprintf("audit-it-%d", time.Now().UnixNano())
	wm, err := worm.New(worm.Config{
		Endpoint: env("MINIO_ENDPOINT", "localhost:9000"),
		AccessKey: env("MINIO_ACCESS_KEY", "datacern"),
		SecretKey: env("MINIO_SECRET_KEY", "datacern_dev"),
		Bucket:   bucket, RetentionYears: 7,
	})
	if err != nil {
		t.Skipf("minio client: %v", err)
	}
	if err := wm.EnsureBucket(ctx); err != nil {
		t.Skipf("minio unavailable (ensure bucket): %v", err)
	}

	brokers := []string{env("KAFKA_BROKERS", "localhost:9092")}
	producer := gckafka.NewProducer(gckafka.Config{Brokers: brokers})
	metaEmitter := meta.New(producer)

	chainMgr := chain.New(redis, pg, ch)
	proc := &ingest.Processor{CH: ch, Chain: chainMgr, Meta: metaEmitter}
	// Meta is deliberately NOT wired here (export.go's `if e.Meta != nil` guard
	// makes this safe): ExportDay's self-audit event publishes to the real,
	// unnamespaced `audit.events.v1` topic (meta.Topic), which by design any
	// audit-service consumer subscribes to (domain/topics.go) — including one
	// left running from `deploy/e2e/boot_services.sh` against this SAME shared
	// dev-stack Kafka/ClickHouse/Postgres. Such a co-resident consumer would
	// append the self-audit event to this test's own (tenant, chain_date)
	// chain moments after ExportDay seals it, racing the pre-tamper verify
	// call in TestAC05/06 with a nondeterministic 9th event (EventsChecked
	// flapping 8/9, manifest_match flapping on the read-order race between the
	// Postgres chain_heads bump and the ClickHouse insert). No test here reads
	// this signal back (waitForMeta only ever checks `audit.searched`), so
	// omitting it costs no coverage while making the exact-count assertions
	// hermetic against any other audit-service instance sharing this infra.
	exporter := &export.Exporter{CH: ch, PG: pg, WORM: wm}

	// Real OPA authz over the sidecar + Redis projection.
	opaURL := env("OPA_URL", "http://localhost:8281")
	az := authz.NewOPAClient(opaURL, env("REDIS_ADDR", "localhost:6379"))

	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	verifier := api.NewVerifierStatic(&key.PublicKey, issuer, audience)

	group := "audit-it-" + uuid.NewString()[:8]
	sub, _ := domainSub()
	consumer := &ingest.Consumer{
		Brokers: brokers, GroupID: group, Sub: sub, Processor: proc,
		Dedup: redis, CH: ch, DLQ: producer, Meta: metaEmitter, RescanInterval: 3 * time.Second,
	}

	srv := &api.Server{
		CH: ch, PG: pg, WORM: wm, Compliance: &compliance.Builder{CH: ch, WORM: wm},
		Redriver: consumer, Meta: metaEmitter, Authz: az, Verifier: verifier,
		IngestGroup: group, PresignTTL: time.Hour,
	}
	ts := httptest.NewServer(srv.Router())

	h := &harness{
		pg: pg, ch: ch, redis: redis, worm: wm, chain: chainMgr, proc: proc,
		exporter: exporter, consumer: consumer, server: srv, http: ts, producer: producer,
		key: key, brokers: brokers, group: group, today: time.Now().UTC().Format("2006-01-02"),
	}
	t.Cleanup(func() {
		ts.Close()
		_ = producer.Close()
		ch.Close()
		pool.Close()
		_ = redis.Close()
	})
	return h
}

// token mints a signed RS256 JWT the static verifier accepts.
func (h *harness) token(sub, tenant, typ string, scopes []string) string {
	claims := jwt.MapClaims{
		"sub": sub, "tenant_id": tenant, "typ": typ, "scopes": scopes,
		"iss": issuer, "aud": audience,
		"iat": time.Now().Unix(), "exp": time.Now().Add(10 * time.Minute).Unix(),
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	s, err := tok.SignedString(h.key)
	if err != nil {
		panic(err)
	}
	return s
}

// seedCatalog registers audit actions as known + tenant-scoped=false so OPA's
// action_known holds (mirrors rbac's catalog projection). The catalog key is a
// shared global on the dev-stack Redis — merge additively, never replace (and
// never via setJSON, whose 1h TTL would expire the whole platform catalog).
func (h *harness) seedCatalog(t *testing.T) {
	t.Helper()
	actions := map[string]bool{}
	for _, e := range authz.Manifest() {
		actions[e.Action] = e.WorkspaceScoped
	}
	if err := opaclient.SeedCatalogActions(context.Background(), h.redis, actions); err != nil {
		t.Fatalf("seed catalog: %v", err)
	}
}

// seedAdmin grants a user the tenant-admin flag so OPA allows audit actions.
func (h *harness) seedAdmin(t *testing.T, tenant, user string) {
	t.Helper()
	setJSON(t, h.redis, fmt.Sprintf("perm:%s:%s:flags", tenant, user), map[string]any{"admin": true, "ws_admin": []string{}})
}

func setJSON(t *testing.T, r *redisx.Client, key string, v any) {
	t.Helper()
	b := mustJSON(v)
	if err := r.Set(context.Background(), key, string(b), time.Hour); err != nil {
		t.Fatalf("redis seed %s: %v", key, err)
	}
}
