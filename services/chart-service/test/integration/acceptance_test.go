package integration

import (
	"context"
	"net/http"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/go-common/event"
)

// TestAC01_EndToEndResolveThroughSemanticAndQuery: a saved bar chart resolves
// aggregated=true through REAL semantic-service compile → REAL query-service
// execute; semantic received a compile call for measure revenue grouped by
// region. Hits: semantic-service (HTTP), query-service (HTTP), Postgres, Redis.
func TestAC01_EndToEndResolveThroughSemanticAndQuery(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, _, chartID := h.seedChart(t, tenant, nil)

	beforeCompile := atomic.LoadInt64(&h.semantic.compileHits)
	r := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	if r.status != http.StatusOK {
		t.Fatalf("status %d %v", r.status, r.body)
	}
	d := dataMap(r)
	if d["aggregated"] != true {
		t.Fatalf("want aggregated=true, got %v", d["aggregated"])
	}
	rows, _ := d["rows"].([]any)
	if len(rows) != 2 {
		t.Fatalf("want one row per region (2), got %d", len(rows))
	}
	if atomic.LoadInt64(&h.semantic.compileHits) != beforeCompile+1 {
		t.Fatal("semantic-service compile was not called")
	}
	if len(h.semantic.lastMetrics) == 0 || h.semantic.lastMetrics[0] != "revenue" {
		t.Fatalf("compile metrics = %v, want [revenue]", h.semantic.lastMetrics)
	}
	if len(h.semantic.lastDims) == 0 || h.semantic.lastDims[0] != "region" {
		t.Fatalf("compile dims = %v, want [region]", h.semantic.lastDims)
	}
}

// TestAC02_RedisCacheHit: identical request within TTL → query-service receives
// no additional call and meta.cache="hit". Hits: Redis, Postgres.
func TestAC02_RedisCacheHit(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, _, chartID := h.seedChart(t, tenant, nil)

	r1 := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	if r1.body["meta"].(map[string]any)["cache"] != "miss" {
		t.Fatalf("first call should be a miss")
	}
	runsAfterFirst := atomic.LoadInt64(&h.semantic.runHits)
	r2 := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	if r2.body["meta"].(map[string]any)["cache"] != "hit" {
		t.Fatalf("second call should be a hit, got %v", r2.body["meta"])
	}
	if atomic.LoadInt64(&h.semantic.runHits) != runsAfterFirst {
		t.Fatal("query-service was called on a cache hit")
	}
}

// TestAC04_EventDrivenInvalidation: a semantic measure.updated event runs the
// REAL invalidation consumer (Postgres reverse-lookup + Redis delete); the next
// request re-resolves (miss). Hits: Kafka-consumer logic, Postgres, Redis.
func TestAC04_EventDrivenInvalidation(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, _, chartID := h.seedChart(t, tenant, nil)

	// prime the cache
	_ = h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	hit := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
	if hit.body["meta"].(map[string]any)["cache"] != "hit" {
		t.Fatal("expected a warm cache before invalidation")
	}

	// Real consumer handler processing a semantic.measure.updated envelope.
	env := events.New("measure.updated", tenant, "service", "svc:semantic",
		"wr:t:semantic:measure/revenue", "trace-1", map[string]any{})
	if err := h.inval.Handle(context.Background(), env); err != nil {
		t.Fatalf("invalidator: %v", err)
	}
	// within 5s the entry is gone → next request is a miss.
	deadline := time.Now().Add(5 * time.Second)
	for {
		r := h.do(t, "GET", "/api/v1/charts/"+chartID.String()+"/data", tok, nil, nil)
		if r.body["meta"].(map[string]any)["cache"] == "miss" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("cache was not invalidated within 5s")
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// TestAC06_DrilldownThroughQueryService: drilldown wraps the saved query SQL
// with a bind predicate and paginates. Hits: query-service (HTTP), Postgres.
func TestAC06_DrilldownThroughQueryService(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, _, chartID := h.seedChart(t, tenant, map[string]any{
		"drilldown": map[string]any{"query_urn": "wr:t:query:query/q-1", "dataset_urn": "wr:t:dataset:dataset/d-1"},
	})
	r := h.do(t, "POST", "/api/v1/charts/"+chartID.String()+"/drilldown", tok,
		map[string]any{"clicked": map[string]any{"dimension": "region", "value": "EMEA"}, "limit": 50}, nil)
	if r.status != http.StatusOK {
		t.Fatalf("drilldown status %d %v", r.status, r.body)
	}
	page, _ := r.body["page"].(map[string]any)
	if page["next_cursor"] != "c2" {
		t.Fatalf("expected paginated drilldown, got %v", page)
	}
}

// TestAC12_RLSCrossTenantEmpty proves the shipped chart_app role enforces RLS:
// tenant B cannot read tenant A's chart (HTTP 404) and a direct read scoped to
// the wrong tenant returns nothing. Hits: Postgres (RLS via non-owner role).
func TestAC12_RLSCrossTenantEmpty(t *testing.T) {
	h := requireHarness(t)
	tenantA := uuid.New()
	_, _, chartID := h.seedChart(t, tenantA, nil)

	// HTTP: tenant B's token → 404 (not 403), no existence leak.
	tokB := h.token(t, uuid.New())
	r := h.do(t, "GET", "/api/v1/charts/"+chartID.String(), tokB, nil, nil)
	if r.status != http.StatusNotFound {
		t.Fatalf("cross-tenant read want 404, got %d", r.status)
	}

	// Store layer: reading tenant A's chart under tenant B's GUC yields empty.
	if _, err := h.pg.GetChart(context.Background(), uuid.New(), chartID); err == nil {
		t.Fatal("RLS should hide the chart from another tenant")
	}
	// And tenant A can read it.
	if _, err := h.pg.GetChart(context.Background(), tenantA, chartID); err != nil {
		t.Fatalf("owner tenant should read its chart: %v", err)
	}
}

// TestAC09_DashboardNameConflict: a second active insights dashboard with the
// same name → 409; after archiving the first, creation succeeds. Hits: Postgres
// (unique partial index).
func TestAC09_DashboardNameConflict(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	ws := uuid.New()
	mk := func() resp {
		return h.do(t, "POST", "/api/v1/dashboards", tok, map[string]any{
			"name": "Same Name", "module": "insights", "workspace_id": ws.String(),
		}, nil)
	}
	first := mk()
	if first.status != http.StatusCreated {
		t.Fatalf("first create: %d", first.status)
	}
	dup := mk()
	if dup.status != http.StatusConflict {
		t.Fatalf("dup create want 409, got %d", dup.status)
	}
	id := dataMap(first)["id"].(string)
	if a := h.do(t, "POST", "/api/v1/dashboards/"+id+"/archive", tok, nil, nil); a.status != http.StatusOK {
		t.Fatalf("archive: %d", a.status)
	}
	if again := mk(); again.status != http.StatusCreated {
		t.Fatalf("create after archive want 201, got %d", again.status)
	}
}

// TestAC10_CircularLinkAndCleanup: A→B then B→A is rejected 409 CIRCULAR_LINK;
// deleting A removes B's back-reference. Hits: Postgres (transactional link).
func TestAC10_CircularLinkAndCleanup(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, dashID, chartA := h.seedChart(t, tenant, nil)
	// second chart in same dashboard.
	bResp := h.do(t, "POST", "/api/v1/dashboards/"+dashID.String()+"/charts", tok, map[string]any{
		"name": "child", "chart_type": "grid_chart",
		"config": map[string]any{"columns": []string{"region"}},
	}, nil)
	if bResp.status != http.StatusCreated {
		t.Fatalf("create child chart: %d %v", bResp.status, bResp.body)
	}
	chartB := uuid.MustParse(dataMap(bResp)["id"].(string))

	if l := h.do(t, "PUT", "/api/v1/charts/"+chartA.String()+"/link", tok,
		map[string]any{"child_chart_id": chartB.String(), "link_type": 1}, nil); l.status != http.StatusOK {
		t.Fatalf("A→B link: %d %v", l.status, l.body)
	}
	// B→A should be a cycle.
	cyc := h.do(t, "PUT", "/api/v1/charts/"+chartB.String()+"/link", tok,
		map[string]any{"child_chart_id": chartA.String(), "link_type": 1}, nil)
	if cyc.status != http.StatusConflict {
		t.Fatalf("B→A want 409 CIRCULAR_LINK, got %d %v", cyc.status, cyc.body)
	}
	// delete A → B back-reference cleared.
	if del := h.do(t, "DELETE", "/api/v1/charts/"+chartA.String(), tok, nil, nil); del.status != http.StatusNoContent {
		t.Fatalf("delete A: %d", del.status)
	}
	gb := h.do(t, "GET", "/api/v1/charts/"+chartB.String(), tok, nil, nil)
	if lp := dataMap(gb)["linked_parent_id"]; lp != nil {
		t.Fatalf("B's linked_parent_id should be cleared, got %v", lp)
	}
}

// TestAC11_CSVExport: an export completes with a signed, expiring artifact URL.
// Hits: Postgres (operations), object store, query-service (HTTP).
func TestAC11_CSVExport(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, _, chartID := h.seedChart(t, tenant, nil)
	start := h.do(t, "POST", "/api/v1/charts/"+chartID.String()+"/export", tok, map[string]any{"format": "csv"}, nil)
	if start.status != http.StatusAccepted {
		t.Fatalf("export start want 202, got %d %v", start.status, start.body)
	}
	opID := dataMap(start)["operation_id"].(string)
	deadline := time.Now().Add(10 * time.Second)
	for {
		op := h.do(t, "GET", "/api/v1/operations/"+opID, tok, nil, nil)
		status, _ := dataMap(op)["status"].(string)
		if status == "completed" {
			url, _ := dataMap(op)["artifact_url"].(string)
			if url == "" {
				t.Fatal("completed export has no artifact_url")
			}
			return
		}
		if status == "failed" {
			t.Fatalf("export failed: %v", dataMap(op)["error"])
		}
		if time.Now().After(deadline) {
			t.Fatal("export did not complete in 10s")
		}
		time.Sleep(150 * time.Millisecond)
	}
}

// TestAC14_BundleExportImportRemap: export a dashboard, import into another
// workspace with a URN remap; unmapped URNs fail atomically. Hits: Postgres.
func TestAC14_BundleExportImportRemap(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	tok := h.token(t, tenant)
	_, dashID, _ := h.seedChart(t, tenant, nil)

	bundle := h.do(t, "POST", "/api/v1/dashboards/"+dashID.String()+"/export-bundle", tok, nil, nil)
	if bundle.status != http.StatusOK {
		t.Fatalf("export-bundle: %d %v", bundle.status, bundle.body)
	}
	b := dataMap(bundle)

	// import without a mapping → 422 UNMAPPED_URN.
	noMap := h.do(t, "POST", "/api/v1/dashboards/import", tok, map[string]any{
		"bundle": b, "workspace_id": uuid.New().String(),
	}, nil)
	if noMap.status != http.StatusUnprocessableEntity {
		t.Fatalf("import without mapping want 422, got %d %v", noMap.status, noMap.body)
	}
	// import with mapping → 201.
	withMap := h.do(t, "POST", "/api/v1/dashboards/import", tok, map[string]any{
		"bundle": b, "workspace_id": uuid.New().String(),
		"url_mapping": map[string]string{"wr:t:semantic:measure/revenue": "wr:t:semantic:measure/revenue2"},
	}, nil)
	if withMap.status != http.StatusCreated {
		t.Fatalf("import with mapping want 201, got %d %v", withMap.status, withMap.body)
	}
}

// TestAC_OPAAuthzRealSidecar exercises the REAL OPA authorization path
// (go-common opaclient → OPA sidecar reading the Redis projection) for the
// ACTUAL guarded actions — including the reconciled archive/restore→update,
// link→update, and export actions that previously 403'd for everyone. It skips
// cleanly when no OPA sidecar is reachable (OPA_URL, default localhost:8281).
//
// This is the coverage that would have caught the non-canonical-verb regression
// masked by authz.AllowAll in the other ACs: a granted admin must be ALLOWED on
// every registered guarded action, and an unknown/unregistered action must DENY.
func TestAC_OPAAuthzRealSidecar(t *testing.T) {
	h := requireHarness(t)
	opaURL := os.Getenv("OPA_URL")
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if !opaReachable(ctx, opaURL) {
		t.Skip("OPA sidecar not reachable at " + opaURL + "; skipping real-OPA authz test")
	}
	az := authz.NewOPA(opaURL, endpointOf(h))
	tenant := uuid.New().String()
	user := "admin-" + uuid.NewString()
	ws := uuid.New().String()

	// 1. Before seeding: a guarded action is denied (fail-closed, unknown_action).
	deny := authz.Input{
		Subject: authz.Subject{ID: user, Typ: "user"}, Action: authz.ActionChartUpdate,
		Tenant: tenant, WorkspaceID: ws, ResourceURN: "wr:t:chart:chart/x",
	}
	if az.Allow(ctx, deny) {
		t.Fatal("guarded action must be denied before any grant (fail-closed)")
	}

	// 2. Seed a tenant-admin projection over the FULL registered manifest.
	var actions []string
	for _, e := range authz.Manifest() {
		actions = append(actions, e.Action)
	}
	seedAdminProjection(t, h, tenant, user, ws, actions)

	// 3. Every registered guarded action must now be ALLOWED for the admin —
	// this is what proves the reconciled verbs actually register + decide allow.
	for _, action := range actions {
		in := authz.Input{
			Subject: authz.Subject{ID: user, Typ: "user"}, Action: action,
			Tenant: tenant, WorkspaceID: ws, ResourceURN: "wr:t:chart:chart/x",
		}
		if !az.Allow(ctx, in) {
			t.Errorf("granted admin was DENIED for registered action %q (real OPA)", action)
		}
	}

	// 4. An unregistered action (non-canonical verb) must DENY even for admin —
	// exactly the failure mode of the archive/link regression.
	for _, bad := range []string{"chart.dashboard.archive", "chart.chart.link"} {
		in := authz.Input{
			Subject: authz.Subject{ID: user, Typ: "user"}, Action: bad,
			Tenant: tenant, WorkspaceID: ws, ResourceURN: "wr:t:chart:chart/x",
		}
		if az.Allow(ctx, in) {
			t.Errorf("unregistered action %q must be denied (unknown_action)", bad)
		}
	}
}

var _ = event.Envelope{}
