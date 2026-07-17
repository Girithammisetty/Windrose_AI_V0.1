package api

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/export"
	"github.com/windrose-ai/chart-service/internal/resolve"
	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/event"
)

// --- in-memory doubles (unit tier only; never wired from cmd/server) ---

type memStore struct {
	mu    sync.Mutex
	dash  map[uuid.UUID]*domain.Dashboard
	chart map[uuid.UUID]*domain.Chart
	ops   map[uuid.UUID]*domain.Operation
	idem  map[string][]byte
}

func newMemStore() *memStore {
	return &memStore{dash: map[uuid.UUID]*domain.Dashboard{}, chart: map[uuid.UUID]*domain.Chart{},
		ops: map[uuid.UUID]*domain.Operation{}, idem: map[string][]byte{}}
}

func (m *memStore) CreateDashboard(_ context.Context, d *domain.Dashboard, _ []event.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	d.CreatedAt, d.UpdatedAt = time.Now(), time.Now()
	cp := *d
	m.dash[d.ID] = &cp
	return nil
}
func (m *memStore) GetDashboard(_ context.Context, tenant, id uuid.UUID) (*domain.Dashboard, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	d, ok := m.dash[id]
	if !ok || d.TenantID != tenant {
		return nil, domain.ENotFound("dashboard not found")
	}
	cp := *d
	return &cp, nil
}
func (m *memStore) ListDashboards(_ context.Context, tenant, ws uuid.UUID, module string, archived bool, tag string, limit int, after *uuid.UUID) ([]domain.Dashboard, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []domain.Dashboard
	for _, d := range m.dash {
		if d.TenantID == tenant && d.WorkspaceID == ws && d.Archived == archived {
			out = append(out, *d)
		}
	}
	return out, nil
}
func (m *memStore) UpdateDashboard(_ context.Context, d *domain.Dashboard, _ []event.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.dash[d.ID]; !ok {
		return domain.ENotFound("dashboard not found")
	}
	d.UpdatedAt = time.Now()
	cp := *d
	m.dash[d.ID] = &cp
	return nil
}
func (m *memStore) SetDashboardArchived(_ context.Context, tenant, id uuid.UUID, archived bool, _ []event.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	d, ok := m.dash[id]
	if !ok || d.TenantID != tenant {
		return domain.ENotFound("dashboard not found")
	}
	d.Archived = archived
	return nil
}
func (m *memStore) DeleteDashboard(_ context.Context, tenant, id uuid.UUID, _ []event.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if d, ok := m.dash[id]; !ok || d.TenantID != tenant {
		return domain.ENotFound("dashboard not found")
	}
	delete(m.dash, id)
	return nil
}
func (m *memStore) DashboardBlockingCharts(_ context.Context, tenant, dashboardID uuid.UUID) ([]uuid.UUID, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var ids []uuid.UUID
	for _, c := range m.chart {
		if c.DashboardID == dashboardID && allowsCases(c) {
			ids = append(ids, c.ID)
		}
	}
	return ids, nil
}
func (m *memStore) CreateChart(_ context.Context, c *domain.Chart, _ []event.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.dash[c.DashboardID]; !ok {
		return domain.ENotFound("dashboard not found")
	}
	c.CreatedAt, c.UpdatedAt = time.Now(), time.Now()
	cp := *c
	m.chart[c.ID] = &cp
	return nil
}
func (m *memStore) GetChart(_ context.Context, tenant, id uuid.UUID) (*domain.Chart, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	c, ok := m.chart[id]
	if !ok || c.TenantID != tenant {
		return nil, domain.ENotFound("chart not found")
	}
	cp := *c
	return &cp, nil
}
func (m *memStore) ListCharts(_ context.Context, tenant, dashboardID uuid.UUID) ([]domain.Chart, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []domain.Chart
	for _, c := range m.chart {
		if c.TenantID == tenant && c.DashboardID == dashboardID {
			out = append(out, *c)
		}
	}
	return out, nil
}
func (m *memStore) UpdateChart(_ context.Context, c *domain.Chart, versionBump bool, expectVersion int, _ []event.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	cur, ok := m.chart[c.ID]
	if !ok {
		return domain.ENotFound("chart not found")
	}
	if expectVersion > 0 && expectVersion != cur.ChartVersion {
		return domain.EConflict("stale version")
	}
	if versionBump {
		c.ChartVersion = cur.ChartVersion + 1
	} else {
		c.ChartVersion = cur.ChartVersion
	}
	cp := *c
	m.chart[c.ID] = &cp
	return nil
}
func (m *memStore) DeleteChart(_ context.Context, tenant, id uuid.UUID, _ []event.Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if c, ok := m.chart[id]; !ok || c.TenantID != tenant {
		return domain.ENotFound("chart not found")
	}
	delete(m.chart, id)
	return nil
}
func (m *memStore) ChartAllowsCases(_ context.Context, tenant, id uuid.UUID) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	c, ok := m.chart[id]
	if !ok || c.TenantID != tenant {
		return false, domain.ENotFound("chart not found")
	}
	return allowsCases(c), nil
}
func (m *memStore) CreateLink(_ context.Context, tenant, parentID, childID uuid.UUID, cols []domain.ColumnPair, linkType int, _ []event.Envelope) error {
	if parentID == childID {
		return domain.ECircularLink("self link")
	}
	return nil
}
func (m *memStore) RemoveLink(context.Context, uuid.UUID, uuid.UUID, uuid.UUID, []event.Envelope) error {
	return nil
}
func (m *memStore) CreateOperation(_ context.Context, op *domain.Operation, tenant uuid.UUID) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	cp := *op
	m.ops[op.ID] = &cp
	return nil
}
func (m *memStore) GetOperation(_ context.Context, tenant, id uuid.UUID) (*domain.Operation, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	op, ok := m.ops[id]
	if !ok {
		return nil, domain.ENotFound("operation not found")
	}
	cp := *op
	return &cp, nil
}
func (m *memStore) UpdateOperation(_ context.Context, tenant, id uuid.UUID, status, url, urn, errMsg string, expires *time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if op, ok := m.ops[id]; ok {
		op.Status, op.ArtifactURL, op.ArtifactURN, op.Error, op.ExpiresAt = status, url, urn, errMsg, expires
	}
	return nil
}
func (m *memStore) ConcurrentExports(context.Context, uuid.UUID) (int, error) { return 0, nil }
func (m *memStore) GetIdempotent(_ context.Context, tenant uuid.UUID, key, method, path string) (int, []byte, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if b, ok := m.idem[key+method+path]; ok {
		return http.StatusCreated, b, true, nil
	}
	return 0, nil, false, nil
}
func (m *memStore) PutIdempotent(_ context.Context, tenant uuid.UUID, key, method, path string, status int, body []byte) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.idem[key+method+path] = body
	return nil
}

func allowsCases(c *domain.Chart) bool {
	var dm struct {
		AllowCases bool `json:"allow_cases"`
	}
	_ = json.Unmarshal(c.DisplayMeta, &dm)
	return dm.AllowCases
}

// memCache is an in-memory result cache double.
type memCache struct {
	mu    sync.Mutex
	data  map[string]*domain.ShapedResult
	locks map[string]bool
}

func newMemCache() *memCache {
	return &memCache{data: map[string]*domain.ShapedResult{}, locks: map[string]bool{}}
}
func (m *memCache) Get(_ context.Context, key string) (*domain.ShapedResult, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	r, ok := m.data[key]
	return r, ok, nil
}
func (m *memCache) Set(_ context.Context, key, tenant, chartID string, urns []string, res *domain.ShapedResult) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.data[key] = res
	return nil
}
func (m *memCache) InvalidateChart(_ context.Context, tenant, chartID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for k := range m.data {
		delete(m.data, k)
	}
	return nil
}
func (m *memCache) AcquireLock(_ context.Context, key string) (bool, error) { return true, nil }
func (m *memCache) ReleaseLock(_ context.Context, key string) error         { return nil }

// stubResolver returns a canned result and counts calls (proves cache short-
// circuits the resolver, AC-2).
type stubResolver struct {
	mu    sync.Mutex
	calls int
	res   *domain.ShapedResult
	err   error
}

func (s *stubResolver) Resolve(_ context.Context, _ string, chart *domain.Chart, _ domain.ResolveRequest) (*domain.ShapedResult, error) {
	s.mu.Lock()
	s.calls++
	s.mu.Unlock()
	if s.err != nil {
		return nil, s.err
	}
	r := *s.res
	r.ChartID = chart.ID.String()
	r.ChartVersion = chart.ChartVersion
	return &r, nil
}
func (s *stubResolver) Drilldown(_ context.Context, _ string, queryURN string, dr resolve.DrilldownRequest) (resolve.ExecResult, error) {
	return resolve.ExecResult{Columns: []domain.ExecColumn{{Name: "region"}}, Rows: [][]any{{"EMEA"}}, NextCursor: "next"}, nil
}
func (s *stubResolver) callCount() int { s.mu.Lock(); defer s.mu.Unlock(); return s.calls }

// --- harness ---

type harness struct {
	srv      *Server
	http     *httptest.Server
	key      *rsa.PrivateKey
	store    *memStore
	cache    *memCache
	resolver *stubResolver
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	st := newMemStore()
	c := newMemCache()
	res := &stubResolver{res: &domain.ShapedResult{ChartType: "vertical_bar_chart", Aggregated: true,
		Columns: []string{"region", "sum_revenue"}, Rows: [][]any{{"EMEA", 1250000.5}}, RowCount: 1}}
	srv := &Server{
		Store: st, Cache: c, Authz: authz.AllowAll{}, Resolver: res,
		Verifier:   authjwt.NewStatic(&key.PublicKey, "windrose-test", "windrose"),
		Exports:    export.NewFSStore(t.TempDir(), "http://test", []byte("secret")),
		PreviewSem: make(chan struct{}, 5),
	}
	ts := httptest.NewServer(srv.Router())
	t.Cleanup(ts.Close)
	return &harness{srv: srv, http: ts, key: key, store: st, cache: c, resolver: res}
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
	req, err := http.NewRequest(method, h.http.URL+path, rdr)
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
	raw, _ := io.ReadAll(r.Body)
	out := resp{status: r.StatusCode, headers: r.Header}
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

// seed creates a dashboard + chart and returns their ids.
func (h *harness) seedChart(t *testing.T, tenant uuid.UUID, displayMeta string) (uuid.UUID, uuid.UUID) {
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
	if displayMeta != "" {
		var dm map[string]any
		_ = json.Unmarshal([]byte(displayMeta), &dm)
		body["display_meta"] = dm
	}
	ccr := h.do(t, "POST", "/api/v1/dashboards/"+dashID.String()+"/charts", tok, body, nil)
	if ccr.status != http.StatusCreated {
		t.Fatalf("create chart: %d %v", ccr.status, ccr.body)
	}
	chartID := uuid.MustParse(dataMap(ccr)["id"].(string))
	return dashID, chartID
}
