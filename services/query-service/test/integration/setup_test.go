// Package integration is the Docker-backed tier (Testcontainers Postgres
// for metadata + a real in-process DuckDB engine for execution). It
// auto-skips with a clear message when Docker is unavailable and is
// excluded from `make test-unit` via -short.
package integration

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/require"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	"github.com/windrose-ai/query-service/internal/api"
	"github.com/windrose-ai/query-service/internal/authz"
	"github.com/windrose-ai/query-service/internal/datasets"
	"github.com/windrose-ai/query-service/internal/engine"
	"github.com/windrose-ai/query-service/internal/exec"
	"github.com/windrose-ai/query-service/internal/results"
	"github.com/windrose-ai/query-service/internal/store"
)

// blockingEngine is a controllable engine registered as "warehouse" for
// concurrency/cancel/ceiling scenarios.
type blockingEngine struct {
	mu     sync.Mutex
	blocks map[uuid.UUID]chan struct{} // per-execution holds
	hold   bool
}

func (b *blockingEngine) Name() string                 { return engine.NameWarehouse }
func (b *blockingEngine) Healthy(context.Context) bool { return true }

func (b *blockingEngine) SetHold(hold bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.hold = hold
	if b.blocks == nil {
		b.blocks = map[uuid.UUID]chan struct{}{}
	}
}

func (b *blockingEngine) ReleaseAll() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.hold = false
	for _, ch := range b.blocks {
		close(ch)
	}
	b.blocks = map[uuid.UUID]chan struct{}{}
}

func (b *blockingEngine) ReleaseOne() {
	b.mu.Lock()
	defer b.mu.Unlock()
	for id, ch := range b.blocks {
		close(ch)
		delete(b.blocks, id)
		return
	}
}

func (b *blockingEngine) Execute(ctx context.Context, q engine.Query, sink engine.Sink) (engine.Stats, error) {
	b.mu.Lock()
	hold := b.hold
	var ch chan struct{}
	if hold {
		ch = make(chan struct{})
		if b.blocks == nil {
			b.blocks = map[uuid.UUID]chan struct{}{}
		}
		b.blocks[q.ExecutionID] = ch
	}
	b.mu.Unlock()
	if hold {
		select {
		case <-ch:
		case <-ctx.Done():
			b.mu.Lock()
			delete(b.blocks, q.ExecutionID)
			b.mu.Unlock()
			return engine.Stats{ScanBytes: 512}, ctx.Err() // partial accounting
		}
	}
	if err := sink.Start([]engine.Column{{Name: "n", Type: "integer"}}); err != nil {
		return engine.Stats{}, err
	}
	if err := sink.Row([]any{int64(1)}); err != nil {
		return engine.Stats{}, err
	}
	return engine.Stats{Rows: 1, ScanBytes: 1024}, nil
}

type harness struct {
	pg        *store.PG
	resolver  *datasets.Static
	broker    *exec.Broker
	server    *api.Server
	httpSrv   *httptest.Server
	warehouse *blockingEngine
	key       *rsa.PrivateKey
	duckPath  string
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
	ctx := context.Background()

	pgc, err := tcpostgres.Run(ctx, "postgres:16-alpine",
		tcpostgres.WithDatabase("query"),
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

	// The app pool connects as a NOSUPERUSER/NOBYPASSRLS role — superusers
	// bypass RLS, which would fake out the isolation suite (same pattern as
	// identity-service/rbac-service harnesses).
	adminPool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		log.Fatalf("pg admin pool: %v", err)
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
	adminPool.Close()
	appDSN, err := pgc.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		log.Fatalf("pg dsn: %v", err)
	}
	u, err := url.Parse(appDSN)
	if err != nil {
		log.Fatalf("dsn parse: %v", err)
	}
	u.User = url.UserPassword("app_user", "app_pw")
	pool, err := pgxpool.New(ctx, u.String())
	if err != nil {
		log.Fatalf("pg pool: %v", err)
	}
	defer pool.Close()

	// Seed the DuckDB file once; the engine opens read-only workers on it.
	tmp, err := os.MkdirTemp("", "query-service-it-*")
	if err != nil {
		log.Fatalf("tmp: %v", err)
	}
	defer os.RemoveAll(tmp)
	duckPath := filepath.Join(tmp, "lake.db")
	db, err := sql.Open("duckdb", duckPath)
	if err != nil {
		log.Fatalf("duckdb open: %v", err)
	}
	if _, err := db.Exec(`
		CREATE TABLE orders (id INTEGER, region VARCHAR, email VARCHAR, order_total DECIMAL(18,2), order_date DATE);
		INSERT INTO orders VALUES
			(1, 'EMEA', 'a@x.com', 100.50, DATE '2026-06-01'),
			(2, 'AMER', 'b@x.com', 250.25, DATE '2026-06-02'),
			(3, 'EMEA', 'c@x.com', 75.00,  DATE '2026-05-01'),
			(4, 'APAC', 'd@x.com', 10.00,  DATE '2026-04-01');
		CREATE TABLE users (id INTEGER, email VARCHAR);
		INSERT INTO users VALUES (1, 'admin@x.com');
		CREATE TABLE big AS SELECT range AS n, 'v' || (range % 100) AS val FROM range(120000);
	`); err != nil {
		log.Fatalf("duckdb seed: %v", err)
	}
	if err := db.Close(); err != nil {
		log.Fatalf("duckdb close: %v", err)
	}

	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		log.Fatalf("rsa: %v", err)
	}

	pg := store.NewPG(pool)
	resolver := datasets.NewStatic()
	warehouse := &blockingEngine{}
	duck := &engine.DuckDB{Path: duckPath, ReadOnly: true}
	broker := &exec.Broker{
		Store:    pg,
		Resolver: resolver,
		Engines:  engine.NewRegistry(duck, &engine.Trino{}, warehouse),
		Results:  results.NewStore(filepath.Join(tmp, "results")),
		Slots:    exec.NewSlotManager(),
	}
	server := &api.Server{
		Store: pg, Broker: broker, Results: broker.Results, Authz: authz.AllowAll{},
		Verifier:     api.NewVerifierStatic(&key.PublicKey, "windrose-test", "windrose"),
		ExportSecret: []byte("integration-secret"),
	}
	httpSrv := httptest.NewServer(server.Router())
	defer httpSrv.Close()

	h = &harness{pg: pg, resolver: resolver, broker: broker, server: server,
		httpSrv: httpSrv, warehouse: warehouse, key: key, duckPath: duckPath}
	code := m.Run()
	broker.Wait()
	os.Exit(code)
}

// newTenant registers a tenant with the seeded datasets resolved into the
// DuckDB namespace.
func (h *harness) newTenant() uuid.UUID {
	tenant := uuid.New()
	h.resolver.Put(tenant, datasets.Meta{
		Name: "Orders", Version: 1,
		URN:           "wr:" + tenant.String() + ":dataset:dataset/orders",
		PhysicalIdent: `"main"."orders"`, Namespace: "main",
		SizeBytes: 1 << 20, RowCount: 4,
		Columns: []datasets.Column{
			{Name: "region", Type: "string"},
			{Name: "email", Type: "string", PIITag: "pii:email"},
			{Name: "order_total", Type: "decimal"},
			{Name: "order_date", Type: "date"},
		},
	}, true)
	h.resolver.Put(tenant, datasets.Meta{
		Name: "Big", Version: 1,
		URN:           "wr:" + tenant.String() + ":dataset:dataset/big",
		PhysicalIdent: `"main"."big"`, Namespace: "main",
		SizeBytes: 2 << 20, RowCount: 120000,
	}, true)
	return tenant
}

func (h *harness) token(t *testing.T, tenant uuid.UUID, typ, sub string, extra map[string]any) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub, "tenant_id": tenant.String(), "typ": typ,
		"iss": "windrose-test", "aud": "windrose",
		"exp": time.Now().Add(5 * time.Minute).Unix(),
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
	raw, err := io.ReadAll(res.Body)
	require.NoError(t, err)
	out := resp{status: res.StatusCode, headers: res.Header}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &out.body)
	}
	return out
}

func data(r resp) map[string]any {
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

func (h *harness) waitStatus(t *testing.T, token, execID, want string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		r := h.do(t, "GET", "/api/v1/executions/"+execID, token, nil, nil)
		require.Equal(t, http.StatusOK, r.status, "%v", r.body)
		if data(r)["status"] == want {
			return data(r)
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("execution %s never reached %s", execID, want)
	return nil
}

func (h *harness) waitTerminal(t *testing.T, token, execID string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		r := h.do(t, "GET", "/api/v1/executions/"+execID, token, nil, nil)
		require.Equal(t, http.StatusOK, r.status)
		s, _ := data(r)["status"].(string)
		switch s {
		case "succeeded", "failed", "cancelled", "rejected", "ceiling_exceeded":
			return data(r)
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("execution %s never terminal", execID)
	return nil
}

var _ = fmt.Sprintf
