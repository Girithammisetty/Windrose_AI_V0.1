// Package integration is chart-service's Docker-backed tier. It stands up a
// real Postgres (Testcontainers) migrated with the shipped RLS + non-owner
// chart_app role, a real Redis for the result cache, and real HTTP servers that
// speak the semantic-service /compile and query-service /sql/run contracts
// (the resolver's HTTP clients are the production ones — nothing is mocked in
// the resolver). It auto-skips with a clear message when Docker is unavailable
// and is excluded from `make test-unit` via -short.
package integration

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"flag"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"github.com/windrose-ai/chart-service/internal/api"
	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/cache"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/chart-service/internal/export"
	"github.com/windrose-ai/chart-service/internal/resolve"
	"github.com/windrose-ai/chart-service/internal/store"
	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/redisx"
)

type harness struct {
	pg        *store.PG
	pool      *pgxpool.Pool
	redis     *redisx.Client
	redisAddr string
	cache     *cache.Redis
	server    *api.Server
	httpSrv   *httptest.Server
	key       *rsa.PrivateKey
	semantic  *contractServers
	inval     *events.Invalidator
}

type contractServers struct {
	sem         *httptest.Server
	qry         *httptest.Server
	compileHits int64
	runHits     int64
	lastMetrics []string
	lastDims    []string
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
		tcpostgres.WithDatabase("chart"),
		tcpostgres.WithUsername("postgres"),
		tcpostgres.WithPassword("postgres"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		skipReason = "Docker unavailable (" + err.Error() + ")"
		os.Exit(m.Run())
	}
	defer func() { _ = pgc.Terminate(ctx) }()

	ownerDSN, err := pgc.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		log.Fatalf("pg dsn: %v", err)
	}
	// Migrations run as the owner (postgres); they create the chart_app role.
	if err := store.Migrate(ownerDSN); err != nil {
		log.Fatalf("migrations: %v", err)
	}
	// Runtime pool connects as the SHIPPED non-owner chart_app role — this is
	// what proves RLS is authoritative (NOSUPERUSER NOBYPASSRLS).
	u, _ := url.Parse(ownerDSN)
	u.User = url.UserPassword("chart_app", "chart_app")
	pool, err := pgxpool.New(ctx, u.String())
	if err != nil {
		log.Fatalf("app pool: %v", err)
	}
	defer pool.Close()

	// Real Redis for the result cache.
	rc, err := tcredis.Run(ctx, "redis:7-alpine")
	if err != nil {
		skipReason = "Docker unavailable for redis (" + err.Error() + ")"
		os.Exit(m.Run())
	}
	defer func() { _ = rc.Terminate(ctx) }()
	redisEndpoint, _ := rc.Endpoint(ctx, "")
	rcli := redisx.New(redisEndpoint)
	resultCache := cache.NewRedis(rcli)

	cs := newContractServers()
	defer cs.sem.Close()
	defer cs.qry.Close()

	resolver := &resolve.Resolver{
		Semantic:     resolve.NewHTTPSemantic(cs.sem.URL),
		Query:        resolve.NewHTTPQuery(cs.qry.URL),
		DefaultModel: "sm-revenue",
	}

	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	pg := store.NewPG(pool)
	server := &api.Server{
		Store: pg, Cache: resultCache, Authz: authz.AllowAll{}, Resolver: resolver,
		Verifier:   authjwt.NewStatic(&key.PublicKey, "windrose-test", "windrose"),
		Exports:    export.NewFSStore(os.TempDir()+"/chart-exports", "http://test", []byte("it-secret")),
		PreviewSem: make(chan struct{}, 5),
	}
	httpSrv := httptest.NewServer(server.Router())
	defer httpSrv.Close()

	h = &harness{
		pg: pg, pool: pool, redis: rcli, redisAddr: redisEndpoint, cache: resultCache,
		server: server, httpSrv: httpSrv, key: key, semantic: cs,
		inval: &events.Invalidator{Store: pg, Cache: resultCache},
	}
	os.Exit(m.Run())
}

// newContractServers stands up real HTTP servers that implement the
// semantic-service /compile and query-service /sql/run + /executions/{id}/results
// contracts. They record calls so tests can assert the resolution path.
func newContractServers() *contractServers {
	cs := &contractServers{}

	semMux := http.NewServeMux()
	semMux.HandleFunc("/api/v1/compile", func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&cs.compileHits, 1)
		var req struct {
			Metrics    []string `json:"metrics"`
			Dimensions []string `json:"dimensions"`
			Filters    []struct {
				Dimension string `json:"dimension"`
				Op        string `json:"op"`
				Values    []any  `json:"values"`
			} `json:"filters"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		cs.lastMetrics = req.Metrics
		cs.lastDims = req.Dimensions
		// Build a representative SQL string + positional params for the filters.
		params := []map[string]any{}
		for _, f := range req.Filters {
			for _, v := range f.Values {
				params = append(params, map[string]any{"type": "text", "value": v})
			}
		}
		writeJSON(w, 200, map[string]any{"data": map[string]any{
			"sql":    "SELECT region, sum(revenue) FROM sales GROUP BY region",
			"params": params,
			"output_schema": []map[string]any{
				{"name": "region", "type": "string", "role": "dimension"},
				{"name": "sum_revenue", "type": "number", "role": "measure"},
			},
		}})
	})
	cs.sem = httptest.NewServer(semMux)

	qryMux := http.NewServeMux()
	// POST /sql/run → returns an execution id (sync).
	qryMux.HandleFunc("/api/v1/sql/run", func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&cs.runHits, 1)
		writeJSON(w, 200, map[string]any{"data": map[string]any{"execution_id": uuid.NewString(), "status": "succeeded"}})
	})
	// GET /executions/{id}/results → aggregated rows, one per region (AC-1).
	qryMux.HandleFunc("/api/v1/executions/", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"data": map[string]any{
			"columns": []map[string]any{{"name": "region", "type": "string"}, {"name": "sum_revenue", "type": "number"}},
			"rows":    [][]any{{"EMEA", 1250000.5}, {"APAC", 990321.0}},
			"page":    map[string]any{"next_cursor": "c2", "has_more": true},
		}})
	})
	// GET /queries/{id} → SQL for drilldown wrapping (AC-6).
	qryMux.HandleFunc("/api/v1/queries/", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"data": map[string]any{"sql": "SELECT * FROM sales"}})
	})
	cs.qry = httptest.NewServer(qryMux)
	return cs
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func (h *harness) token(t *testing.T, tenant uuid.UUID) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": "user-1", "tenant_id": tenant.String(), "typ": "user",
		"iss": "windrose-test", "aud": "windrose", "exp": time.Now().Add(5 * time.Minute).Unix(),
	}
	s, err := jwt.NewWithClaims(jwt.SigningMethodRS256, claims).SignedString(h.key)
	if err != nil {
		t.Fatal(err)
	}
	return s
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
		b, _ := json.Marshal(body)
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, h.httpSrv.URL+path, rdr)
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdrs {
		req.Header.Set(k, v)
	}
	r, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = r.Body.Close() }()
	out := resp{status: r.StatusCode, headers: r.Header}
	_ = json.NewDecoder(r.Body).Decode(&out.body)
	return out
}

func dataMap(r resp) map[string]any {
	d, _ := r.body["data"].(map[string]any)
	return d
}

// seedChart creates a dashboard + a semantic-bound bar chart, returns ids.
func (h *harness) seedChart(t *testing.T, tenant uuid.UUID, displayMeta map[string]any) (uuid.UUID, uuid.UUID, uuid.UUID) {
	t.Helper()
	tok := h.token(t, tenant)
	ws := uuid.New()
	cr := h.do(t, "POST", "/api/v1/dashboards", tok, map[string]any{
		"name": "D" + uuid.NewString(), "module": "insights", "workspace_id": ws.String(),
	}, nil)
	if cr.status != http.StatusCreated {
		t.Fatalf("create dashboard: %d %v", cr.status, cr.body)
	}
	dashID := uuid.MustParse(dataMap(cr)["id"].(string))
	body := map[string]any{
		"name": "C" + uuid.NewString(), "chart_type": "vertical_bar_chart",
		"sources": []map[string]any{{"source_type": "semantic_measure", "source_urn": "wr:t:semantic:measure/revenue"}},
		"config":  map[string]any{"x": map[string]any{"dimension": "region"}, "y": []map[string]any{{"measure": "revenue", "agg_fn": "sum"}}},
	}
	if displayMeta != nil {
		body["display_meta"] = displayMeta
	}
	ccr := h.do(t, "POST", "/api/v1/dashboards/"+dashID.String()+"/charts", tok, body, nil)
	if ccr.status != http.StatusCreated {
		t.Fatalf("create chart: %d %v", ccr.status, ccr.body)
	}
	chartID := uuid.MustParse(dataMap(ccr)["id"].(string))
	return ws, dashID, chartID
}
