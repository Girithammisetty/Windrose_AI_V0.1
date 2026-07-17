package api

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/authz"
	"github.com/windrose-ai/query-service/internal/datasets"
	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/engine"
	"github.com/windrose-ai/query-service/internal/events"
	"github.com/windrose-ai/query-service/internal/exec"
	"github.com/windrose-ai/query-service/internal/results"
	"github.com/windrose-ai/query-service/internal/store"
)

// fakeEngine mirrors the broker-tier fake for API-level tests.
type fakeEngine struct {
	name    string
	healthy bool
	cols    []engine.Column
	rows    [][]any
	block   chan struct{}

	mu      sync.Mutex
	lastSQL string
	calls   int
}

func (f *fakeEngine) Name() string                 { return f.name }
func (f *fakeEngine) Healthy(context.Context) bool { return f.healthy }
func (f *fakeEngine) Execute(ctx context.Context, q engine.Query, sink engine.Sink) (engine.Stats, error) {
	f.mu.Lock()
	f.lastSQL = q.SQL
	f.calls++
	f.mu.Unlock()
	if f.block != nil {
		select {
		case <-f.block:
		case <-ctx.Done():
			return engine.Stats{}, ctx.Err()
		}
	}
	cols := f.cols
	if cols == nil {
		cols = []engine.Column{{Name: "region", Type: "string"}, {Name: "c", Type: "integer"}}
	}
	if err := sink.Start(cols); err != nil {
		return engine.Stats{}, err
	}
	var st engine.Stats
	for _, r := range f.rows {
		if err := sink.Row(r); err != nil {
			return st, err
		}
		st.Rows++
	}
	st.ScanBytes = 1 << 20
	return st, nil
}

type apiFixture struct {
	srv      *httptest.Server
	mem      *store.Mem
	resolver *datasets.Static
	duck     *fakeEngine
	broker   *exec.Broker
	server   *Server
	key      *rsa.PrivateKey
	tenantA  uuid.UUID
	tenantB  uuid.UUID
}

func newAPIFixture(t *testing.T, az authz.Authorizer) *apiFixture {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	require.NoError(t, err)
	mem := store.NewMem()
	resolver := datasets.NewStatic()
	tenantA, tenantB := uuid.New(), uuid.New()
	for _, tenant := range []uuid.UUID{tenantA, tenantB} {
		resolver.Put(tenant, datasets.Meta{
			Name: "Orders", Version: 1,
			URN:           "wr:" + tenant.String() + ":dataset:dataset/orders",
			PhysicalIdent: `"bronze"."orders_v1"`, Namespace: "bronze",
			SizeBytes: 1 << 20, RowCount: 3,
			Columns: []datasets.Column{{Name: "region", Type: "string"}, {Name: "email", Type: "string", PIITag: "pii:email"}},
		}, true)
	}
	duck := &fakeEngine{name: engine.NameDuckDB, healthy: true,
		rows: [][]any{{"EMEA", int64(2)}, {"AMER", int64(1)}}}
	broker := &exec.Broker{
		Store:    mem,
		Resolver: resolver,
		Engines:  engine.NewRegistry(duck, &fakeEngine{name: engine.NameTrino, healthy: true}, &fakeEngine{name: engine.NameWarehouse}),
		Results:  results.NewStore(t.TempDir()),
		Slots:    exec.NewSlotManager(),
	}
	if az == nil {
		az = authz.AllowAll{}
	}
	server := &Server{
		Store: mem, Broker: broker, Results: broker.Results, Authz: az,
		Verifier:     NewVerifierStatic(&key.PublicKey, "windrose-test", "windrose"),
		ExportSecret: []byte("test-secret"),
	}
	ts := httptest.NewServer(server.Router())
	t.Cleanup(ts.Close)
	t.Cleanup(broker.Wait)
	return &apiFixture{srv: ts, mem: mem, resolver: resolver, duck: duck, broker: broker,
		server: server, key: key, tenantA: tenantA, tenantB: tenantB}
}

func (f *apiFixture) token(t *testing.T, tenant uuid.UUID, typ, sub string, extra map[string]any) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub, "tenant_id": tenant.String(), "typ": typ,
		"iss": "windrose-test", "aud": "windrose",
		"exp": time.Now().Add(5 * time.Minute).Unix(),
	}
	for k, v := range extra {
		claims[k] = v
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	signed, err := tok.SignedString(f.key)
	require.NoError(t, err)
	return signed
}

type resp struct {
	status  int
	body    map[string]any
	headers http.Header
}

func (f *apiFixture) do(t *testing.T, method, path, token string, body any, hdrs map[string]string) resp {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		require.NoError(t, err)
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, f.srv.URL+path, rdr)
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

func errCode(r resp) string {
	if e, ok := r.body["error"].(map[string]any); ok {
		c, _ := e["code"].(string)
		return c
	}
	return ""
}

func data(r resp) map[string]any {
	d, _ := r.body["data"].(map[string]any)
	return d
}

var savedQueryBody = map[string]any{
	"name":         "Orders by region",
	"module_names": []string{"insights"},
	"sql_text":     "SELECT region, count(*) c FROM {{dataset('Orders')}} WHERE region = :region AND order_date >= :since GROUP BY 1",
	"variables": []map[string]any{
		{"name": "region", "type": "string", "allowed_values": []string{"EMEA", "AMER", "APAC"}},
		{"name": "since", "type": "date", "required": false, "default": "2026-01-01"},
	},
}

func (f *apiFixture) createQuery(t *testing.T, token string) string {
	t.Helper()
	r := f.do(t, "POST", "/api/v1/queries", token, savedQueryBody, nil)
	require.Equal(t, http.StatusCreated, r.status, "%v", r.body)
	return data(r)["id"].(string)
}

func (f *apiFixture) waitStatus(t *testing.T, token, execID, want string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		r := f.do(t, "GET", "/api/v1/executions/"+execID, token, nil, nil)
		require.Equal(t, http.StatusOK, r.status)
		if data(r)["status"] == want {
			return data(r)
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("execution %s never reached %s", execID, want)
	return nil
}

// ---- CRUD & save-time validation ---------------------------------------------

func TestSavedQueryCRUD(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)

	r := f.do(t, "POST", "/api/v1/queries", tok, savedQueryBody, nil)
	require.Equal(t, http.StatusCreated, r.status)
	id := data(r)["id"].(string)
	assert.Equal(t, `"v1"`, r.headers.Get("ETag"))
	assert.EqualValues(t, 1, data(r)["version_no"])
	refs := data(r)["dataset_refs"].([]any)
	require.Len(t, refs, 1, "dataset refs resolved at save (QRY-FR-001)")

	// duplicate name in workspace → 409 (QRY-FR-001)
	r = f.do(t, "POST", "/api/v1/queries", tok, savedQueryBody, nil)
	assert.Equal(t, http.StatusConflict, r.status)
	assert.Equal(t, domain.CodeConflict, errCode(r))

	// PATCH bumps version under If-Match (BR-11)
	patch := map[string]any{"description": "now with description"}
	r = f.do(t, "PATCH", "/api/v1/queries/"+id, tok, patch, map[string]string{"If-Match": `"v1"`})
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.EqualValues(t, 2, data(r)["current_version_no"])

	// stale If-Match → 409
	r = f.do(t, "PATCH", "/api/v1/queries/"+id, tok, patch, map[string]string{"If-Match": `"v1"`})
	assert.Equal(t, http.StatusConflict, r.status)

	// versions listed
	r = f.do(t, "GET", "/api/v1/queries/"+id+"/versions", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	assert.Len(t, r.body["data"].([]any), 2)

	// list with page envelope (MASTER-FR-022)
	r = f.do(t, "GET", "/api/v1/queries", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	_, hasPage := r.body["page"].(map[string]any)
	assert.True(t, hasPage)

	// soft delete → 204, then 404
	r = f.do(t, "DELETE", "/api/v1/queries/"+id, tok, nil, nil)
	assert.Equal(t, http.StatusNoContent, r.status)
	r = f.do(t, "GET", "/api/v1/queries/"+id, tok, nil, nil)
	assert.Equal(t, http.StatusNotFound, r.status)
}

func TestSaveTimeValidation(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)

	post := func(mutate func(map[string]any)) resp {
		body := map[string]any{}
		for k, v := range savedQueryBody {
			body[k] = v
		}
		mutate(body)
		return f.do(t, "POST", "/api/v1/queries", tok, body, nil)
	}

	// module_names ≥ 1 (V1 rule, QRY-FR-001)
	r := post(func(b map[string]any) { b["module_names"] = []string{}; b["name"] = "q1" })
	assert.Equal(t, 422, r.status)

	// legacy {var} syntax rejected with migration hint (QRY-FR-002)
	r = post(func(b map[string]any) {
		b["name"] = "q2"
		b["sql_text"] = "SELECT * FROM {{dataset('Orders')}} WHERE region = {region}"
	})
	require.Equal(t, 422, r.status)
	assert.Equal(t, domain.CodeValidationFailed, errCode(r))
	details := r.body["error"].(map[string]any)["details"].(map[string]any)
	assert.Contains(t, details["hint"], ":region")

	// undeclared placeholder → 422 at save time (QRY-FR-004)
	r = post(func(b map[string]any) {
		b["name"] = "q3"
		b["sql_text"] = "SELECT * FROM {{dataset('Orders')}} WHERE region = :region AND x = :undeclared"
	})
	require.Equal(t, 422, r.status)
	assert.Equal(t, domain.CodeVariableInvalid, errCode(r))

	// DML rejected at save (AC-3 "run or saved")
	r = post(func(b map[string]any) {
		b["name"] = "q4"
		b["sql_text"] = "DELETE FROM {{dataset('Orders')}}"
		b["variables"] = []map[string]any{}
	})
	assert.Equal(t, http.StatusForbidden, r.status)
	assert.Equal(t, domain.CodeStatementNotAllowed, errCode(r))

	// unresolvable dataset → 422 DATASET_NOT_FOUND (QRY-FR-005)
	r = post(func(b map[string]any) {
		b["name"] = "q5"
		b["sql_text"] = "SELECT 1 FROM {{dataset('Ghost')}}"
		b["variables"] = []map[string]any{}
	})
	require.Equal(t, 422, r.status)
	assert.Equal(t, domain.CodeDatasetNotFound, errCode(r))
}

// ---- Run flows ----------------------------------------------------------------

func TestRunSavedQueryEndToEnd(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	id := f.createQuery(t, tok)

	r := f.do(t, "POST", "/api/v1/queries/"+id+"/run", tok,
		map[string]any{"variables": map[string]any{"region": "EMEA", "since": "2026-06-01"}}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	execID := data(r)["execution_id"].(string)
	plan := data(r)["plan"].(map[string]any)
	assert.Equal(t, "duckdb", plan["engine"])

	f.waitStatus(t, tok, execID, domain.StatusSucceeded)

	// results page (QRY-FR-061 shape)
	r = f.do(t, "GET", "/api/v1/executions/"+execID+"/results?limit=1", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	d := data(r)
	rows := d["rows"].([]any)
	require.Len(t, rows, 1)
	page := d["page"].(map[string]any)
	assert.Equal(t, true, page["has_more"])
	cols := d["columns"].([]any)
	assert.Equal(t, "region", cols[0].(map[string]any)["name"])
	stats := d["stats"].(map[string]any)
	assert.Equal(t, "duckdb", stats["engine"])

	// next page via cursor
	r = f.do(t, "GET", "/api/v1/executions/"+execID+"/results?limit=5&cursor="+page["next_cursor"].(string), tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	assert.Len(t, data(r)["rows"].([]any), 1)
	assert.Equal(t, false, data(r)["page"].(map[string]any)["has_more"])

	// history row queryable (QRY-FR-080)
	r = f.do(t, "GET", "/api/v1/executions?status=succeeded", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	assert.NotEmpty(t, r.body["data"].([]any))
}

// AC-4 endpoint-level: missing required + undeclared extra in one 422.
func TestRunVariableProblems(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	id := f.createQuery(t, tok)

	r := f.do(t, "POST", "/api/v1/queries/"+id+"/run", tok,
		map[string]any{"variables": map[string]any{"regoin": "EMEA"}}, nil)
	require.Equal(t, 422, r.status)
	assert.Equal(t, domain.CodeVariableInvalid, errCode(r))
	details := r.body["error"].(map[string]any)["details"].([]any)
	require.Len(t, details, 2, "both problems listed per-field (AC-4)")

	// injection attempt against allowed_values (defense stacked before bind)
	r = f.do(t, "POST", "/api/v1/queries/"+id+"/run", tok,
		map[string]any{"variables": map[string]any{"region": "x' OR '1'='1"}}, nil)
	require.Equal(t, 422, r.status, "allowed_values rejects it before it even binds")
}

func TestAdhocRunAndDryRun(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)

	// ad-hoc run (QRY-FR-006)
	r := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql":          "SELECT region FROM {{dataset('Orders')}} WHERE region = :r",
		"declarations": []map[string]any{{"name": "r", "type": "string"}},
		"variables":    map[string]any{"r": "EMEA"},
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	f.waitStatus(t, tok, data(r)["execution_id"].(string), domain.StatusSucceeded)

	// dry-run (QRY-FR-041)
	r = f.do(t, "POST", "/api/v1/sql/dry-run", tok, map[string]any{
		"sql": "SELECT region, count(*) FROM {{dataset('Orders')}} GROUP BY 1",
	}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	d := data(r)
	assert.Equal(t, "duckdb", d["engine"])
	assert.Equal(t, "ok", d["ceiling_verdict"])
	assert.NotNil(t, d["ceilings"])
	assert.EqualValues(t, 1<<20, d["estimated_scan_bytes"])

	// dry-run over the ceiling → 422 with estimate (QRY-FR-041/042)
	f.resolver.Put(f.tenantA, datasets.Meta{
		Name: "Huge", Version: 1, URN: "urn:huge", PhysicalIdent: `"bronze"."huge"`,
		Namespace: "bronze", SizeBytes: 60 << 30,
	}, true)
	r = f.do(t, "POST", "/api/v1/sql/dry-run", tok, map[string]any{
		"sql": "SELECT count(*) FROM {{dataset('Huge')}}",
	}, nil)
	require.Equal(t, 422, r.status)
	assert.Equal(t, domain.CodeCostCeilingExceeded, errCode(r))
	det := r.body["error"].(map[string]any)["details"].(map[string]any)
	assert.EqualValues(t, 60<<30, det["estimated_scan_bytes"])

	// statement safety at the API edge (AC-3)
	r = f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{"sql": "sElEcT 1; DeLeTe FROM t"}, nil)
	assert.Equal(t, http.StatusForbidden, r.status)
	assert.Equal(t, domain.CodeStatementNotAllowed, errCode(r))
}

func TestCancelEndpoint(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	f.duck.block = make(chan struct{})

	r := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}",
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	execID := data(r)["execution_id"].(string)
	f.waitStatus(t, tok, execID, domain.StatusRunning)

	r = f.do(t, "POST", "/api/v1/executions/"+execID+"/cancel", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.Equal(t, domain.StatusCancelled, data(r)["status"])

	// terminal cancel → 409 (BRD §4.4)
	r = f.do(t, "POST", "/api/v1/executions/"+execID+"/cancel", tok, nil, nil)
	assert.Equal(t, http.StatusConflict, r.status)
	close(f.duck.block)
}

// AC-13 unit variant: results 410 GONE after GC; history persists.
func TestResultsRetention(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	r := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}",
	}, nil)
	execID := data(r)["execution_id"].(string)
	f.waitStatus(t, tok, execID, domain.StatusSucceeded)

	// results not ready on a fresh (running) execution → 409
	f.duck.block = make(chan struct{})
	r2 := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
	}, nil)
	runningID := data(r2)["execution_id"].(string)
	f.waitStatus(t, tok, runningID, domain.StatusRunning)
	rr := f.do(t, "GET", "/api/v1/executions/"+runningID+"/results", tok, nil, nil)
	assert.Equal(t, http.StatusConflict, rr.status)
	close(f.duck.block)
	f.waitStatus(t, tok, runningID, domain.StatusSucceeded)

	// GC everything → 410 with re_run_hint; history row remains.
	_, err := f.server.Results.GC(0)
	require.NoError(t, err)
	rr = f.do(t, "GET", "/api/v1/executions/"+execID+"/results", tok, nil, nil)
	require.Equal(t, http.StatusGone, rr.status)
	assert.Equal(t, domain.CodeGone, errCode(rr))
	det := rr.body["error"].(map[string]any)["details"].(map[string]any)
	assert.NotEmpty(t, det["re_run_hint"], "BR-9 re_run_hint")
	rr = f.do(t, "GET", "/api/v1/executions/"+execID, tok, nil, nil)
	assert.Equal(t, http.StatusOK, rr.status, "history row persists (AC-13)")
}

func TestExportAndDownload(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	r := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}",
	}, nil)
	execID := data(r)["execution_id"].(string)
	f.waitStatus(t, tok, execID, domain.StatusSucceeded)

	// parquet → honest Should-stub
	r = f.do(t, "POST", "/api/v1/executions/"+execID+"/export", tok, map[string]any{"format": "parquet"}, nil)
	assert.Equal(t, http.StatusNotImplemented, r.status)

	// csv → signed URL (QRY-FR-062)
	r = f.do(t, "POST", "/api/v1/executions/"+execID+"/export", tok, map[string]any{"format": "csv"}, nil)
	require.Equal(t, http.StatusCreated, r.status, "%v", r.body)
	url := data(r)["url"].(string)
	require.NotEmpty(t, url)

	// signed link needs no bearer token
	res, err := http.Get(f.srv.URL + url)
	require.NoError(t, err)
	defer res.Body.Close()
	require.Equal(t, http.StatusOK, res.StatusCode)
	body, _ := io.ReadAll(res.Body)
	assert.Contains(t, string(body), "region")
	assert.Contains(t, string(body), "EMEA")

	// tampered token → rejected
	res, err = http.Get(f.srv.URL + url + "x")
	require.NoError(t, err)
	res.Body.Close()
	assert.Equal(t, http.StatusGone, res.StatusCode)
}

// MASTER-FR-025 / BR-10: Idempotency-Key replay.
func TestIdempotencyReplay(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	key := map[string]string{"Idempotency-Key": "idem-123"}

	r1 := f.do(t, "POST", "/api/v1/queries", tok, savedQueryBody, key)
	require.Equal(t, http.StatusCreated, r1.status)
	assert.Empty(t, r1.headers.Get("Idempotency-Replayed"))

	r2 := f.do(t, "POST", "/api/v1/queries", tok, savedQueryBody, key)
	assert.Equal(t, http.StatusCreated, r2.status, "replayed original, not a 409")
	assert.Equal(t, "true", r2.headers.Get("Idempotency-Replayed"))
	assert.Equal(t, data(r1)["id"], data(r2)["id"])
}

// ---- Security tiers -----------------------------------------------------------

func TestAuthentication(t *testing.T) {
	f := newAPIFixture(t, nil)
	r := f.do(t, "GET", "/api/v1/queries", "", nil, nil)
	assert.Equal(t, http.StatusUnauthorized, r.status)

	// alg=none forbidden (MASTER-FR-014)
	none := jwt.NewWithClaims(jwt.SigningMethodNone, jwt.MapClaims{
		"sub": "u1", "tenant_id": f.tenantA.String(),
		"exp": time.Now().Add(time.Minute).Unix(), "iss": "windrose-test", "aud": "windrose",
	})
	tok, err := none.SignedString(jwt.UnsafeAllowNoneSignatureType)
	require.NoError(t, err)
	r = f.do(t, "GET", "/api/v1/queries", tok, nil, nil)
	assert.Equal(t, http.StatusUnauthorized, r.status)

	// expired token
	expired := f.token(t, f.tenantA, domain.TypUser, "u1", map[string]any{"exp": time.Now().Add(-time.Minute).Unix()})
	r = f.do(t, "GET", "/api/v1/queries", expired, nil, nil)
	assert.Equal(t, http.StatusUnauthorized, r.status)
}

// AC-12 unit variant (in-memory policy fake): tenant A's resources are 404
// for tenant B on every id-addressed endpoint, with audit events.
func TestIsolationSuiteUnit(t *testing.T) {
	f := newAPIFixture(t, nil)
	tokA := f.token(t, f.tenantA, domain.TypUser, "alice", nil)
	tokB := f.token(t, f.tenantB, domain.TypUser, "bob", nil)

	qid := f.createQuery(t, tokA)
	r := f.do(t, "POST", "/api/v1/queries/"+qid+"/run", tokA,
		map[string]any{"variables": map[string]any{"region": "EMEA"}}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	execID := data(r)["execution_id"].(string)
	f.waitStatus(t, tokA, execID, domain.StatusSucceeded)

	endpoints := []struct {
		method, path string
		body         any
	}{
		{"GET", "/api/v1/queries/" + qid, nil},
		{"PATCH", "/api/v1/queries/" + qid, map[string]any{"description": "x"}},
		{"DELETE", "/api/v1/queries/" + qid, nil},
		{"GET", "/api/v1/queries/" + qid + "/versions", nil},
		{"POST", "/api/v1/queries/" + qid + "/run", map[string]any{"variables": map[string]any{"region": "EMEA"}}},
		{"GET", "/api/v1/executions/" + execID, nil},
		{"GET", "/api/v1/executions/" + execID + "/results", nil},
		{"POST", "/api/v1/executions/" + execID + "/cancel", nil},
		{"POST", "/api/v1/executions/" + execID + "/export", map[string]any{"format": "csv"}},
	}
	for _, ep := range endpoints {
		r := f.do(t, ep.method, ep.path, tokB, ep.body, nil)
		assert.Equal(t, http.StatusNotFound, r.status,
			"MASTER-FR-003: %s %s must be 404 (not 403) for tenant B", ep.method, ep.path)
		assert.Equal(t, domain.CodeNotFound, errCode(r))
	}

	// audit events emitted for the denied attempts (MASTER-FR-003)
	envs, err := f.mem.OutboxEventsByType(context.Background(), f.tenantB, events.EvCrossTenantDenied)
	require.NoError(t, err)
	assert.GreaterOrEqual(t, len(envs), len(endpoints))

	// A's data untouched and still visible to A
	r = f.do(t, "GET", "/api/v1/queries/"+qid, tokA, nil, nil)
	assert.Equal(t, http.StatusOK, r.status)
}

// Authz matrix (MASTER-FR-071): every endpoint × denied action → 403 with
// audit; permitted actions pass.
func TestAuthzMatrix(t *testing.T) {
	matrix := []struct {
		action string
		method string
		path   func(f *apiFixture, qid, eid string) string
		body   any
	}{
		{authz.ActionQueryCreate, "POST", func(*apiFixture, string, string) string { return "/api/v1/queries" }, savedQueryBody},
		{authz.ActionQueryRead, "GET", func(*apiFixture, string, string) string { return "/api/v1/queries" }, nil},
		{authz.ActionQueryUpdate, "PATCH", func(_ *apiFixture, qid, _ string) string { return "/api/v1/queries/" + qid }, map[string]any{"description": "x"}},
		{authz.ActionQueryDelete, "DELETE", func(_ *apiFixture, qid, _ string) string { return "/api/v1/queries/" + qid }, nil},
		{authz.ActionExecRun, "POST", func(*apiFixture, string, string) string { return "/api/v1/sql/dry-run" }, map[string]any{"sql": "SELECT 1"}},
		{authz.ActionExecRead, "GET", func(*apiFixture, string, string) string { return "/api/v1/executions" }, nil},
		// Cancel is guarded by the execute capability ("cancel" is not a
		// canonical rbac verb; MASTER-FR-016).
		{authz.ActionExecRun, "POST", func(_ *apiFixture, _, eid string) string { return "/api/v1/executions/" + eid + "/cancel" }, nil},
		{authz.ActionExecExport, "POST", func(_ *apiFixture, _, eid string) string { return "/api/v1/executions/" + eid + "/export" }, map[string]any{"format": "csv"}},
		{authz.ActionStatsRead, "GET", func(*apiFixture, string, string) string { return "/api/v1/stats/queries" }, nil},
		{authz.ActionLimitsUpdate, "PUT", func(*apiFixture, string, string) string { return "/api/v1/limits" }, map[string]any{}},
	}
	for _, tc := range matrix {
		t.Run(tc.action, func(t *testing.T) {
			f := newAPIFixture(t, authz.Static{Denied: map[string]bool{tc.action: true}})
			tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
			qid := ""
			if tc.action != authz.ActionQueryCreate {
				qid = f.createQuery(t, tok)
			}
			eid := uuid.NewString()
			r := f.do(t, tc.method, tc.path(f, qid, eid), tok, tc.body, nil)
			assert.Equal(t, http.StatusForbidden, r.status, "denied action %s must 403", tc.action)
			assert.Equal(t, domain.CodePermissionDenied, errCode(r))
			envs, err := f.mem.OutboxEventsByType(context.Background(), f.tenantA, events.EvPermissionDenied)
			require.NoError(t, err)
			assert.NotEmpty(t, envs, "denial audited (MASTER-FR-040)")
		})
	}
}

// ---- Envelope & misc ----------------------------------------------------------

func TestErrorEnvelopeAndTraceID(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	r := f.do(t, "GET", "/api/v1/queries/"+uuid.NewString(), tok, nil, map[string]string{"X-Trace-Id": "trace-abc"})
	require.Equal(t, http.StatusNotFound, r.status)
	e := r.body["error"].(map[string]any)
	assert.Equal(t, "NOT_FOUND", e["code"])
	assert.NotEmpty(t, e["message"])
	assert.Equal(t, "trace-abc", e["trace_id"], "trace id propagated (MASTER-FR-028)")
	assert.Equal(t, "trace-abc", r.headers.Get("X-Trace-Id"))
}

func TestLimitsEndpoints(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "admin", nil)

	r := f.do(t, "PUT", "/api/v1/limits", tok, map[string]any{"max_scan_bytes": 1 << 30, "concurrent_slots": 5}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)

	// over platform max → 422
	r = f.do(t, "PUT", "/api/v1/limits", tok, map[string]any{"max_scan_bytes": int64(domain.DefaultMaxScanBytes) * 2}, nil)
	assert.Equal(t, 422, r.status)

	r = f.do(t, "GET", "/api/v1/limits", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	eff := data(r)["effective_user"].(map[string]any)
	assert.EqualValues(t, 1<<30, eff["max_scan_bytes"], "override reflected in effective ceilings")
}

func TestStatsEndpoint(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	r := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{"sql": "SELECT region FROM {{dataset('Orders')}}"}, nil)
	f.waitStatus(t, tok, data(r)["execution_id"].(string), domain.StatusSucceeded)

	r = f.do(t, "GET", "/api/v1/stats/queries", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	top := data(r)["top_queries"].([]any)
	assert.NotEmpty(t, top)
}

func TestHealthEndpoints(t *testing.T) {
	f := newAPIFixture(t, nil)
	for _, p := range []string{"/healthz", "/readyz"} {
		res, err := http.Get(f.srv.URL + p)
		require.NoError(t, err)
		res.Body.Close()
		assert.Equal(t, http.StatusOK, res.StatusCode, p)
	}
	res, err := http.Get(f.srv.URL + "/metrics")
	require.NoError(t, err)
	res.Body.Close()
	assert.Equal(t, http.StatusOK, res.StatusCode)
}

// Agent OBO attribution lands in history (MASTER-FR-041).
func TestAgentOBOAttribution(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypAgentOBO, "agent-principal", map[string]any{
		"agent_id": "analytics-agent", "agent_version": "2", "obo_sub": "alice",
	})
	r := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{"sql": "SELECT region FROM {{dataset('Orders')}}"}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	execID := data(r)["execution_id"].(string)
	d := f.waitStatus(t, tok, execID, domain.StatusSucceeded)
	assert.Equal(t, "agent", d["caller_class"], "agent-class governance applied")
	assert.Equal(t, "alice", d["created_by"], "attributed to the OBO user")

	f.duck.mu.Lock()
	lastSQL := f.duck.lastSQL
	f.duck.mu.Unlock()
	assert.Contains(t, lastSQL, fmt.Sprintf("LIMIT %d", domain.AgentInjectedLimit), "AC-6 LIMIT injection via API")

	envs, err := f.mem.OutboxEventsByType(context.Background(), f.tenantA, events.EvExecutionStarted)
	require.NoError(t, err)
	require.NotEmpty(t, envs)
	found := false
	for _, env := range envs {
		if env.ViaAgent != nil && env.ViaAgent.AgentID == "analytics-agent" {
			found = true
			assert.Equal(t, "user", env.Actor.Type)
			assert.Equal(t, "alice", env.Actor.ID)
		}
	}
	assert.True(t, found, "dual attribution on events (MASTER-FR-041)")
}

// Sync mode at the API tier (QRY-FR-043, BR-5).
func TestSyncModeAPI(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	r := f.do(t, "POST", "/api/v1/sql/run?mode=sync", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}",
	}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.Equal(t, domain.StatusSucceeded, data(r)["status"])

	// saturate the single slot → sync refused with USE_ASYNC
	one := 1
	op := domain.Op{Tenant: f.tenantA, Actor: domain.Actor{Type: "user", ID: "admin"}, UserID: "admin"}
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), op, &domain.TenantLimits{ConcurrentSlots: &one}))
	f.duck.block = make(chan struct{})
	rBg := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
	}, nil)
	require.Equal(t, http.StatusAccepted, rBg.status)
	f.waitStatus(t, tok, data(rBg)["execution_id"].(string), domain.StatusRunning)

	tok2 := f.token(t, f.tenantA, domain.TypUser, "u2", nil)
	r = f.do(t, "POST", "/api/v1/sql/run?mode=sync", tok2, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
	}, nil)
	assert.Equal(t, http.StatusConflict, r.status)
	assert.Equal(t, domain.CodeUseAsync, errCode(r))
	close(f.duck.block)
	f.waitStatus(t, tok, data(rBg)["execution_id"].(string), domain.StatusSucceeded)
}

func TestListExecutionsPagination(t *testing.T) {
	f := newAPIFixture(t, nil)
	tok := f.token(t, f.tenantA, domain.TypUser, "u1", nil)
	for i := 0; i < 5; i++ {
		r := f.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
			"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
		}, nil)
		require.Equal(t, http.StatusAccepted, r.status)
		f.waitStatus(t, tok, data(r)["execution_id"].(string), domain.StatusSucceeded)
	}
	r := f.do(t, "GET", "/api/v1/executions?limit=2", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	assert.Len(t, r.body["data"].([]any), 2)
	page := r.body["page"].(map[string]any)
	require.Equal(t, true, page["has_more"])
	r = f.do(t, "GET", "/api/v1/executions?limit=200&cursor="+page["next_cursor"].(string), tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	assert.Len(t, r.body["data"].([]any), 3)

	// sort=-cost accepted (QRY-FR-080)
	r = f.do(t, "GET", "/api/v1/executions?sort=-cost", tok, nil, nil)
	assert.Equal(t, http.StatusOK, r.status)
}

// Suppress unused warning for strings import used conditionally.
var _ = strings.Contains
