// Package integration is usage-service's Docker-backed test tier. It runs
// against the REAL local infra from deploy/docker-compose.dev.yml — real
// Postgres (:5432), real Redpanda/Kafka (:9092), real Redis (:6379) and the
// real OPA sidecar (:8281) — with NO fakes in the metering path. It auto-skips
// with a clear message when the infra is unavailable, and is excluded from
// `make test-unit` via -short.
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

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/usage-service/internal/api"
	"github.com/windrose-ai/usage-service/internal/authz"
	"github.com/windrose-ai/usage-service/internal/events"
	"github.com/windrose-ai/usage-service/internal/ingest"
	"github.com/windrose-ai/usage-service/internal/jobs"
	"github.com/windrose-ai/usage-service/internal/store"
)

const (
	pgAdmin  = "postgres://windrose:windrose_dev@localhost:5432/%s?sslmode=disable"
	redisAddr = "localhost:6379"
	opaURL    = "http://localhost:8281"
)

var brokers = []string{"localhost:9092"}

type harness struct {
	st         *store.PG
	appPool    *pgxpool.Pool
	redis      *redisx.Client
	producer   *gckafka.Producer
	httpSrv    *httptest.Server
	key        *rsa.PrivateKey
	runner     *jobs.Runner
	inputTopic string
	cancel     context.CancelFunc
}

var (
	h          *harness
	skipReason string
)

func requireHarness(t *testing.T) *harness {
	t.Helper()
	if h == nil {
		t.Skip("integration tests skipped: " + skipReason)
	}
	return h
}

func TestMain(m *testing.M) {
	flag.Parse()
	if testing.Short() {
		skipReason = "-short mode (unit tier)"
		os.Exit(m.Run())
	}
	if err := setup(); err != nil {
		skipReason = "real infra unavailable: " + err.Error()
		log.Printf("integration setup skipped: %v", err)
		os.Exit(m.Run())
	}
	code := m.Run()
	if h != nil {
		h.cancel()
		h.httpSrv.Close()
	}
	os.Exit(code)
}

func setup() error {
	ctx, cancel := context.WithCancel(context.Background())
	dialCtx, dialCancel := context.WithTimeout(ctx, 8*time.Second)
	defer dialCancel()

	// --- Real Postgres: fresh DB, migrate as owner, connect as non-owner role.
	dbName := "usage_it"
	admin, err := pgxpool.New(dialCtx, fmt.Sprintf(pgAdmin, "windrose"))
	if err != nil {
		cancel()
		return fmt.Errorf("pg admin connect: %w", err)
	}
	if err := admin.Ping(dialCtx); err != nil {
		admin.Close()
		cancel()
		return fmt.Errorf("pg ping: %w", err)
	}
	_, _ = admin.Exec(dialCtx, "DROP DATABASE IF EXISTS "+dbName+" WITH (FORCE)")
	if _, err := admin.Exec(dialCtx, "CREATE DATABASE "+dbName); err != nil {
		admin.Close()
		cancel()
		return fmt.Errorf("create db: %w", err)
	}
	admin.Close()

	adminURL := fmt.Sprintf(pgAdmin, dbName)
	if err := store.Migrate(adminURL); err != nil {
		cancel()
		return fmt.Errorf("migrate: %w", err)
	}

	// Shipped default runtime DSN: the non-owner NOSUPERUSER role usage_app.
	u, _ := url.Parse(adminURL)
	u.User = url.UserPassword("usage_app", "usage_app")
	appPool, err := pgxpool.New(ctx, u.String())
	if err != nil {
		cancel()
		return fmt.Errorf("app pool: %w", err)
	}
	if err := appPool.Ping(dialCtx); err != nil {
		cancel()
		return fmt.Errorf("app pool ping (non-owner role): %w", err)
	}
	st := store.NewPG(appPool)
	if err := st.SeedMeters(ctx); err != nil {
		cancel()
		return fmt.Errorf("seed meters: %w", err)
	}

	// --- Real Redis.
	redis := redisx.New(redisAddr)
	if err := redis.Ping(dialCtx); err != nil {
		cancel()
		return fmt.Errorf("redis ping: %w", err)
	}

	// --- Real Kafka (Redpanda). Per-run private input topic for isolation; the
	// mapping keys on event_type so any topic carries the metering envelope.
	producer := gckafka.NewProducer(gckafka.Config{Brokers: brokers})
	inputTopic := "usage.itest.in." + uuid.NewString()[:8]

	m := &noopMetrics{}
	pipeline := ingest.NewPipeline(ingest.Catalog(), st, st, m)
	group := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
		Brokers: brokers,
		GroupID: "usage-ingest-it-" + uuid.NewString()[:8],
		Topics:  []string{inputTopic},
		Handler: pipeline.Handle,
		Dedup:   redis,
		DLQ:     producer,
	})
	go group.Run(ctx)

	// Outbox relay → real usage.events.v1 (the ai-gateway feedback path).
	kpub := events.NewKafkaPublisher(ctx, brokers, "")
	relay := &events.Relay{Source: st, Publisher: kpub, Interval: 300 * time.Millisecond}
	go relay.Run(ctx)

	// HTTP server: real store, AllowAll authz double (unit-test-only double,
	// permitted in *_test.go), static RSA verifier. The real OPA path is proven
	// separately in the OPA test via the real authz.OPAClient.
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	srv := &api.Server{
		Store:    st,
		Authz:    authz.AllowAll{},
		Verifier: api.NewVerifierStatic(&key.PublicKey, "windrose-test", "windrose"),
		Ready:    func(ctx context.Context) error { return st.Ping(ctx) },
	}
	httpSrv := httptest.NewServer(srv.Router())

	h = &harness{
		st: st, appPool: appPool, redis: redis, producer: producer,
		httpSrv: httpSrv, key: key, runner: &jobs.Runner{Store: st},
		inputTopic: inputTopic, cancel: cancel,
	}
	return nil
}

type noopMetrics struct{}

func (noopMetrics) IncUnmapped(string)         {}
func (noopMetrics) IncIngested(string, int)    {}
func (noopMetrics) ObserveIngestLag(float64)   {}

// --- helpers ----------------------------------------------------------------

func (h *harness) publish(t *testing.T, eventType string, tenant uuid.UUID, eventID uuid.UUID, occurredAt time.Time, payload map[string]any) {
	t.Helper()
	env := gcevent.Envelope{
		EventID:    eventID,
		EventType:  eventType,
		TenantID:   tenant,
		Actor:      gcevent.Actor{Type: "service", ID: "ai-gateway"},
		OccurredAt: occurredAt,
		Payload:    payload,
	}
	require.NoError(t, h.producer.Publish(context.Background(), h.inputTopic, env))
}

func (h *harness) tokenUsagePayload(ws, principal, agent, model string, in, out int) map[string]any {
	return map[string]any{
		"request_id": uuid.NewString(), "tenant_id": "", "workspace_id": ws,
		"principal": principal, "agent_id": agent, "model_alias": model,
		"input_tokens": float64(in), "output_tokens": float64(out),
	}
}

// rawCount returns the number of raw rows for a tenant/meter.
func (h *harness) rawCount(t *testing.T, tenant uuid.UUID, meter string) int {
	t.Helper()
	return h.queryInt(t, tenant, `SELECT COUNT(*) FROM usage_raw WHERE tenant_id=$1 AND meter_key=$2`, tenant, meter)
}

// outboxCount counts committed outbox rows (published or not — MarkPublished
// only sets published_at, never deletes) for a tenant/type/budget. Deterministic
// and free of Kafka timing, so it is the exactly-once oracle.
func (h *harness) outboxCount(t *testing.T, tenant uuid.UUID, eventType, budgetID string) int {
	t.Helper()
	return h.queryInt(t, tenant,
		`SELECT COUNT(*) FROM outbox WHERE tenant_id=$1 AND event_type=$2 AND payload->>'budget_id'=$3`,
		tenant, eventType, budgetID)
}

func (h *harness) queryInt(t *testing.T, tenant uuid.UUID, sql string, args ...any) int {
	t.Helper()
	ctx := context.Background()
	conn, err := h.appPool.Acquire(ctx)
	require.NoError(t, err)
	defer conn.Release()
	_, err = conn.Exec(ctx, `SELECT set_config('app.tenant_id', $1, false)`, tenant.String())
	require.NoError(t, err)
	var n int
	require.NoError(t, conn.QueryRow(ctx, sql, args...).Scan(&n))
	return n
}

// execTenant runs a write under a tenant-pinned RLS session (test seeding).
func (h *harness) execTenant(t *testing.T, tenant uuid.UUID, sql string, args ...any) {
	t.Helper()
	ctx := context.Background()
	conn, err := h.appPool.Acquire(ctx)
	require.NoError(t, err)
	defer conn.Release()
	_, err = conn.Exec(ctx, `SELECT set_config('app.tenant_id', $1, false)`, tenant.String())
	require.NoError(t, err)
	_, err = conn.Exec(ctx, sql, args...)
	require.NoError(t, err)
}

// makeEnv builds a master envelope for driving the pipeline directly.
func makeEnv(eventType string, tenant, eventID uuid.UUID, payload map[string]any) gcevent.Envelope {
	return gcevent.Envelope{
		EventID: eventID, EventType: eventType, TenantID: tenant,
		Actor: gcevent.Actor{Type: "service", ID: "ai-gateway"}, OccurredAt: time.Now().UTC(), Payload: payload,
	}
}

// waitRawSum polls until the summed quantity for (tenant, meter) reaches want
// (or times out). Returns the final sum.
func (h *harness) waitRawSum(t *testing.T, tenant uuid.UUID, meter string, want float64, d time.Duration) float64 {
	t.Helper()
	deadline := time.Now().Add(d)
	var got float64
	for time.Now().Before(deadline) {
		got, _ = h.st.RawSum(context.Background(), tenant, meter, time.Unix(0, 0).UTC(), time.Now().Add(48*time.Hour), nil, nil, nil)
		if got >= want {
			return got
		}
		time.Sleep(200 * time.Millisecond)
	}
	return got
}

// consumeUsageEvents reads usage.events.v1 for a tenant/type until it finds n
// matching events or times out (real Kafka read of the ai-gateway feedback).
func (h *harness) consumeUsageEvents(t *testing.T, tenant uuid.UUID, eventType string, n int, d time.Duration) []gcevent.Envelope {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), d)
	defer cancel()
	var found []gcevent.Envelope
	done := make(chan struct{})
	grp := gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
		Brokers: brokers,
		GroupID: "usage-events-it-" + uuid.NewString()[:8],
		Topics:  []string{events.EmitTopic},
		Handler: func(_ context.Context, env gcevent.Envelope) error {
			if env.TenantID == tenant && env.EventType == eventType {
				found = append(found, env)
				if len(found) >= n {
					select {
					case <-done:
					default:
						close(done)
					}
				}
			}
			return nil
		},
	})
	go grp.Run(ctx)
	select {
	case <-done:
	case <-ctx.Done():
	}
	_ = grp.Close()
	return found
}

func (h *harness) token(t *testing.T, tenant uuid.UUID, typ, sub string, scopes []string) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub, "tenant_id": tenant.String(), "typ": typ,
		"iss": "windrose-test", "aud": "windrose",
		"exp": time.Now().Add(5 * time.Minute).Unix(),
	}
	if len(scopes) > 0 {
		claims["scopes"] = scopes
	}
	signed, err := jwt.NewWithClaims(jwt.SigningMethodRS256, claims).SignedString(h.key)
	require.NoError(t, err)
	return signed
}

type httpResp struct {
	status int
	body   map[string]any
	raw    []byte
	header http.Header
}

func (h *harness) do(t *testing.T, method, path, token string, body any, hdrs map[string]string) httpResp {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
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
	defer func() { _ = res.Body.Close() }()
	raw, _ := io.ReadAll(res.Body)
	out := httpResp{status: res.StatusCode, raw: raw, header: res.Header}
	_ = json.Unmarshal(raw, &out.body)
	return out
}

func errCode(r httpResp) string {
	if e, ok := r.body["error"].(map[string]any); ok {
		c, _ := e["code"].(string)
		return c
	}
	return ""
}
