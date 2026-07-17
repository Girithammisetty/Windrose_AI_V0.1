// Package integration is case-service's Docker-backed tier. It runs against a
// Testcontainers Postgres 16 (for real RLS with a non-superuser role) plus the
// REAL dev-stack infra already running on localhost: Redpanda (Kafka) at :9092,
// OpenSearch at :9200, Redis at :6379. It auto-skips with a clear message when
// Docker or that infra is unavailable, and is excluded from `make test-unit`
// via -short. No fakes are in the exercised path except the authz double (a
// test-tier substitute for the OPA sidecar) — every other adapter is real.
package integration

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/require"
	kafkago "github.com/segmentio/kafka-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/case-service/internal/api"
	"github.com/windrose-ai/case-service/internal/authz"
	"github.com/windrose-ai/case-service/internal/events"
	"github.com/windrose-ai/case-service/internal/search"
	"github.com/windrose-ai/case-service/internal/sla"
	"github.com/windrose-ai/case-service/internal/store"
)

type harness struct {
	pg        *store.PG
	adminPool *pgxpool.Pool
	server    *api.Server
	httpSrv   *httptest.Server
	search    *search.Client
	slaWorker *sla.Worker
	key       *rsa.PrivateKey
	kafka     bool
}

var (
	h          *harness
	skipReason string
	cancelBg   context.CancelFunc
)

func testingShort() bool {
	if !flag.Parsed() {
		flag.Parse()
	}
	return testing.Short()
}

func portOpen(hostport string) bool {
	c, err := net.DialTimeout("tcp", hostport, 750*time.Millisecond)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

func requireHarness(t *testing.T) *harness {
	t.Helper()
	if h == nil {
		t.Skip("integration tests skipped: " + skipReason)
	}
	return h
}

func TestMain(m *testing.M) {
	if os.Getenv("CASE_IT") == "" && testingShort() {
		skipReason = "-short mode (unit tier)"
		os.Exit(m.Run())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancelBg = cancel

	// Real infra must be up (dev stack). Missing → skip, not fail.
	for _, hp := range []string{"localhost:9200", "localhost:6379"} {
		if !portOpen(hp) {
			skipReason = "dev infra not reachable at " + hp + " (run docker compose up -d)"
			os.Exit(m.Run())
		}
	}
	kafkaUp := portOpen("localhost:9092")

	pgc, err := tcpostgres.Run(ctx, "postgres:16-alpine",
		tcpostgres.WithDatabase("case"),
		tcpostgres.WithUsername("postgres"),
		tcpostgres.WithPassword("postgres"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		skipReason = "Docker unavailable (" + err.Error() + ")"
		os.Exit(m.Run())
	}
	defer func() { _ = pgc.Terminate(ctx) }()

	dsn, err := pgc.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		log.Fatalf("pg dsn: %v", err)
	}
	if err := store.Migrate(dsn); err != nil {
		log.Fatalf("migrations: %v", err)
	}

	// App pool connects as NOSUPERUSER/NOBYPASSRLS so RLS actually binds (AC-13).
	adminPool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		log.Fatalf("admin pool: %v", err)
	}
	for _, stmt := range []string{
		`CREATE ROLE app_user WITH LOGIN PASSWORD 'app_pw' NOSUPERUSER NOBYPASSRLS`,
		`GRANT USAGE ON SCHEMA public TO app_user`,
		`GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user`,
		`GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user`,
	} {
		if _, err := adminPool.Exec(ctx, stmt); err != nil {
			log.Fatalf("app role setup (%s): %v", stmt, err)
		}
	}
	u, _ := url.Parse(dsn)
	u.User = url.UserPassword("app_user", "app_pw")
	pool, err := pgxpool.New(ctx, u.String())
	if err != nil {
		log.Fatalf("app pool: %v", err)
	}

	pg := store.NewPG(pool)
	searchClient, err := search.New("http://localhost:9200")
	if err != nil {
		log.Fatalf("opensearch: %v", err)
	}
	projector := &search.Projector{Store: pg, Search: searchClient}

	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	server := &api.Server{
		Store: pg, Search: searchClient, Projector: projector, Authz: authz.AllowAll{},
		Verifier:   api.NewVerifierStatic(&key.PublicKey, "windrose-test", "windrose"),
		RowFetcher: api.NewHTTPRowFetcher(""), // unset → GET ?with_row surfaces row_error (BR-5)
		Snapshots:  api.NewFSSnapshotStore(mustTempDir()),
		Redis:      redisx.New("localhost:6379"), // real bulk concurrency gate (CASE-FR-032)
	}
	httpSrv := httptest.NewServer(server.Router())

	slaWorker := sla.New(pg)

	// Real outbox relay → real Kafka, and the real search-index consumer that
	// reprojects into real OpenSearch (CASE-FR-041). Group ids are unique per
	// run so tests observe their own writes deterministically.
	if kafkaUp {
		// Reset the event topic to a clean slate for this run: the dev-stack
		// Redpanda persists case.events.v1 across runs, and each run uses a fresh
		// consumer group that reads from the earliest offset — without a reset the
		// projector would have to churn through every prior run's backlog before
		// reaching this run's events. Deleting then recreating gives each run the
		// realistic production steady state (consumer assigned its partition from
		// an empty log). Best-effort: a fresh cluster has nothing to delete.
		kc := &kafkago.Client{Addr: kafkago.TCP("localhost:9092")}
		_, _ = kc.DeleteTopics(ctx, &kafkago.DeleteTopicsRequest{Topics: []string{events.Topic}})
		time.Sleep(500 * time.Millisecond) // let the delete propagate before recreate
		_, _ = kc.CreateTopics(ctx, &kafkago.CreateTopicsRequest{Topics: []kafkago.TopicConfig{
			{Topic: events.Topic, NumPartitions: 1, ReplicationFactor: 1},
		}})
		pub := events.NewKafkaPublisher(ctx, []string{"localhost:9092"}, "")
		relay := &events.Relay{Source: pg, Publisher: pub, Interval: 150 * time.Millisecond}
		go relay.Run(ctx)

		rc := redisx.New("localhost:6379")
		group := "case-search-indexer-it-" + uuid.NewString()
		idx := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
			Brokers: []string{"localhost:9092"}, GroupID: group, Topics: []string{events.Topic},
			Handler: events.SearchIndexHandler(projector), Dedup: rc,
		})
		go idx.Run(ctx)
	}
	go slaWorker.Run(ctx)

	h = &harness{pg: pg, adminPool: adminPool, server: server, httpSrv: httpSrv,
		search: searchClient, slaWorker: slaWorker, key: key, kafka: kafkaUp}

	code := m.Run()
	cancel()
	httpSrv.Close()
	pool.Close()
	adminPool.Close()
	os.Exit(code)
}

func mustTempDir() string {
	d, err := os.MkdirTemp("", "case-it-snap-*")
	if err != nil {
		log.Fatal(err)
	}
	return d
}

func (h *harness) token(t *testing.T, tenant, workspace uuid.UUID, typ, sub string, extra map[string]any) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub, "tenant_id": tenant.String(), "typ": typ, "workspace_id": workspace.String(),
		"iss": "windrose-test", "aud": "windrose", "exp": time.Now().Add(5 * time.Minute).Unix(),
	}
	for k, v := range extra {
		claims[k] = v
	}
	signed, err := jwt.NewWithClaims(jwt.SigningMethodRS256, claims).SignedString(h.key)
	require.NoError(t, err)
	return signed
}

type resp struct {
	status  int
	body    map[string]any
	headers http.Header
}

func (h *harness) do(t *testing.T, method, path, token string, body any, hdrs map[string]string) resp {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		require.NoError(t, err)
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, h.httpSrv.URL+path, rdr)
	require.NoError(t, err)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdrs {
		req.Header.Set(k, v)
	}
	res, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer res.Body.Close()
	raw, _ := io.ReadAll(res.Body)
	out := resp{status: res.StatusCode, headers: res.Header}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &out.body)
	}
	return out
}

func dataMap(r resp) map[string]any {
	d, _ := r.body["data"].(map[string]any)
	return d
}

func errCode(r resp) string {
	if e, ok := r.body["error"].(map[string]any); ok {
		c, _ := e["code"].(string)
		return c
	}
	return ""
}

var _ = fmt.Sprintf
