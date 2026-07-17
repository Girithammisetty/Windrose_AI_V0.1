package api

import (
	"net/http"
	"testing"

	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/go-common/authjwt"
)

// TestAC07_ChartTypesEndpoint: GET /chart-types returns 30 types.
func TestAC07_ChartTypesEndpoint(t *testing.T) {
	h := newHarness(t)
	tok := h.token(t, uuid.New())
	r := h.do(t, "GET", "/api/v1/chart-types", tok, nil, nil)
	if r.status != http.StatusOK {
		t.Fatalf("status %d", r.status)
	}
	list, _ := r.body["data"].([]any)
	if len(list) != 30 {
		t.Fatalf("want 30, got %d", len(list))
	}
}

// TestAC02_CacheHitSkipsResolver: identical request within TTL → resolver not
// called and meta.cache="hit".
func TestAC02_CacheHitSkipsResolver(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, chartID := h.seedChart(t, tenant, "")

	r1 := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	if r1.status != http.StatusOK {
		t.Fatalf("first status %d %v", r1.status, r1.body)
	}
	if meta := r1.body["meta"].(map[string]any); meta["cache"] != "miss" {
		t.Fatalf("first call want miss, got %v", meta["cache"])
	}
	if h.resolver.callCount() != 1 {
		t.Fatalf("resolver should be called once, got %d", h.resolver.callCount())
	}
	r2 := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	if meta := r2.body["meta"].(map[string]any); meta["cache"] != "hit" {
		t.Fatalf("second call want hit, got %v", meta["cache"])
	}
	if h.resolver.callCount() != 1 {
		t.Fatalf("resolver must not be called on cache hit; calls=%d", h.resolver.callCount())
	}
}

// TestAC03_ETag304: If-None-Match with the returned ETag → 304 and no resolver.
func TestAC03_ETag304(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, chartID := h.seedChart(t, tenant, "")
	r1 := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	etag := r1.headers.Get("ETag")
	if etag == "" {
		t.Fatal("no ETag returned")
	}
	before := h.resolver.callCount()
	r2 := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, map[string]string{"If-None-Match": etag})
	if r2.status != http.StatusNotModified {
		t.Fatalf("want 304, got %d", r2.status)
	}
	if h.resolver.callCount() != before {
		t.Fatal("resolver called on 304 path")
	}
}

// TestAC05_AggValidationOnCreate: agg_fn:"median" → 422 VALIDATION_FAILED.
func TestAC05_AggValidationOnCreate(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	ws := uuid.New()
	cr := h.do(t, "POST", "/api/v1/dashboards", tok, map[string]any{"name": "D", "module": "insights", "workspace_id": ws.String()}, nil)
	dashID := dataMap(cr)["id"].(string)
	r := h.do(t, "POST", "/api/v1/dashboards/"+dashID+"/charts", tok, map[string]any{
		"name": "bad", "chart_type": "vertical_bar_chart",
		"config": map[string]any{"x": map[string]any{"dimension": "region"}, "y": []map[string]any{{"measure": "revenue", "agg_fn": "median"}}},
	}, nil)
	if r.status != http.StatusUnprocessableEntity || errCode(r) != "VALIDATION_FAILED" {
		t.Fatalf("want 422 VALIDATION_FAILED, got %d %s", r.status, errCode(r))
	}
}

// TestAC08_AllowCasesBlocksDelete: display_meta.allow_cases=true → DELETE 412.
func TestAC08_AllowCasesBlocksDelete(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, chartID := h.seedChart(t, tenant, `{"allow_cases":true}`)
	r := h.do(t, "DELETE", "/api/v1/charts/"+chartID.String(), tok, nil, nil)
	if r.status != http.StatusPreconditionFailed || errCode(r) != "CHART_HAS_CASES" {
		t.Fatalf("want 412 CHART_HAS_CASES, got %d %s", r.status, errCode(r))
	}
	// chart still exists.
	g := h.do(t, "GET", "/api/v1/charts/"+chartID.String(), tok, nil, nil)
	if g.status != http.StatusOK {
		t.Fatalf("chart should still exist, got %d", g.status)
	}
}

// TestAC06_DrilldownPaginated: drilldown returns a paginated envelope with a
// next_cursor.
func TestAC06_DrilldownPaginated(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, chartID := h.seedChart(t, tenant, `{"drilldown":{"query_urn":"wr:t:query:query/q-1","dataset_urn":"wr:t:dataset:dataset/d-1"}}`)
	r := h.do(t, "POST", "/api/v1/charts/"+chartID.String()+"/drilldown", tok,
		map[string]any{"clicked": map[string]any{"dimension": "region", "value": "EMEA"}, "limit": 50}, nil)
	if r.status != http.StatusOK {
		t.Fatalf("want 200, got %d %v", r.status, r.body)
	}
	page, _ := r.body["page"].(map[string]any)
	if page["next_cursor"] != "next" {
		t.Fatalf("expected next_cursor, got %v", page)
	}
}

// TestNoDrilldownConfigured returns 404 NO_DRILLDOWN_CONFIGURED.
func TestNoDrilldownConfigured(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, chartID := h.seedChart(t, tenant, "")
	r := h.do(t, "POST", "/api/v1/charts/"+chartID.String()+"/drilldown", tok,
		map[string]any{"clicked": map[string]any{"dimension": "region", "value": "EMEA"}}, nil)
	if r.status != http.StatusNotFound || errCode(r) != "NO_DRILLDOWN_CONFIGURED" {
		t.Fatalf("want 404 NO_DRILLDOWN_CONFIGURED, got %d %s", r.status, errCode(r))
	}
}

// TestAC12Unit_CrossTenantIsNotFound: tenant B cannot read tenant A's chart.
func TestAC12Unit_CrossTenantIsNotFound(t *testing.T) {
	h := newHarness(t)
	tenantA := uuid.New()
	_, chartID := h.seedChart(t, tenantA, "")
	tokB := h.token(t, uuid.New())
	r := h.do(t, "GET", "/api/v1/charts/"+chartID.String(), tokB, nil, nil)
	if r.status != http.StatusNotFound {
		t.Fatalf("cross-tenant want 404, got %d", r.status)
	}
}

// TestAuthzDenyMatrix: a denied action → 403 (authz-matrix unit variant).
func TestAuthzDenyMatrix(t *testing.T) {
	h := newHarness(t)
	h.srv.Authz = authz.Static{Denied: map[string]bool{authz.ActionChartRead: true}}
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, chartID := h.seedChart(t, tenant, "")
	r := h.do(t, "GET", "/api/v1/charts/"+chartID.String(), tok, nil, nil)
	if r.status != http.StatusForbidden || errCode(r) != "PERMISSION_DENIED" {
		t.Fatalf("want 403 PERMISSION_DENIED, got %d %s", r.status, errCode(r))
	}
}

// TestUnauthenticatedRejected: no token → 401.
func TestUnauthenticatedRejected(t *testing.T) {
	h := newHarness(t)
	r := h.do(t, "GET", "/api/v1/chart-types", "", nil, nil)
	if r.status != http.StatusUnauthorized {
		t.Fatalf("want 401, got %d", r.status)
	}
}

// TestPreviewNeverCached: preview resolves inline and returns data.
func TestPreviewNeverCached(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	r := h.do(t, "POST", "/api/v1/charts/preview", tok, map[string]any{
		"chart_type": "vertical_bar_chart",
		"config":     map[string]any{"x": map[string]any{"dimension": "region"}, "y": []map[string]any{{"measure": "revenue", "agg_fn": "sum"}}},
		"sources":    []map[string]any{{"source_type": "semantic_measure", "source_urn": "wr:t:semantic:measure/revenue"}},
	}, nil)
	if r.status != http.StatusOK {
		t.Fatalf("preview status %d %v", r.status, r.body)
	}
}

// TestIdempotentReplay: same Idempotency-Key returns the original response.
func TestIdempotentReplay(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	ws := uuid.New()
	body := map[string]any{"name": "Didem", "module": "insights", "workspace_id": ws.String()}
	r1 := h.do(t, "POST", "/api/v1/dashboards", tok, body, map[string]string{"Idempotency-Key": "k1"})
	r2 := h.do(t, "POST", "/api/v1/dashboards", tok, body, map[string]string{"Idempotency-Key": "k1"})
	if r2.headers.Get("Idempotency-Replayed") != "true" {
		t.Fatal("expected replay header")
	}
	if dataMap(r1)["id"] != dataMap(r2)["id"] {
		t.Fatal("replay should return the original id")
	}
}

var _ = authjwt.NewStatic
