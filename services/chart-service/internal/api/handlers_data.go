package api

import (
	"context"
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/cache"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/chart-service/internal/resolve"
)

func parseResolveRequest(r *http.Request) domain.ResolveRequest {
	var req domain.ResolveRequest
	if r.Body != nil && r.ContentLength != 0 {
		_ = json.NewDecoder(r.Body).Decode(&req)
	}
	if v := r.URL.Query().Get("aggregated"); v != "" {
		agg := v != "false"
		req.Aggregated = &agg
	}
	if c := r.URL.Query().Get("cursor"); c != "" {
		req.Cursor = c
	}
	return req
}

func (s *Server) handleChartData(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("chart not found"))
		return
	}
	chart, err := s.Store.GetChart(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	d, err := s.Store.GetDashboard(r.Context(), tenant, chart.DashboardID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	// BR-5: authorize BEFORE any cache lookup — a cache hit must not bypass OPA.
	if !s.authorize(w, r, authz.ActionChartRead, events.URN(tenant, "chart", id.String()), d.WorkspaceID.String()) {
		return
	}
	req := parseResolveRequest(r)
	keyIn := cache.KeyInput{Variables: req.Variables, Filters: req.Filters, Aggregated: req.AggregatedDefault(), Page: req.Cursor}
	key := cache.Key(tenant.String(), id.String(), chart.ChartVersion, keyIn)
	etag := cache.ETag(tenant.String(), id.String(), chart.ChartVersion, keyIn)

	// CHART-FR-032: If-None-Match hit → 304 with no upstream calls.
	if r.Header.Get("If-None-Match") == etag {
		w.Header().Set("ETag", etag)
		w.Header().Set("Cache-Control", "private, max-age=300")
		w.WriteHeader(http.StatusNotModified)
		return
	}

	if res, hit, _ := s.Cache.Get(r.Context(), key); hit {
		s.writeChartData(w, res, etag, "hit")
		return
	}

	// CHART-FR-033: singleflight lock; the leader resolves, others fall through.
	leader, _ := s.Cache.AcquireLock(r.Context(), key)
	if leader {
		defer func() { _ = s.Cache.ReleaseLock(context.Background(), key) }()
	} else {
		// brief wait for the leader, then re-check cache.
		time.Sleep(50 * time.Millisecond)
		if res, hit, _ := s.Cache.Get(r.Context(), key); hit {
			s.writeChartData(w, res, etag, "hit")
			return
		}
	}

	res, err := s.Resolver.Resolve(r.Context(), bearerToken(r), chart, req)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	_ = s.Cache.Set(r.Context(), key, tenant.String(), id.String(), sourceURNs(chart.Sources), res)
	s.writeChartData(w, res, etag, "miss")
}

func (s *Server) writeChartData(w http.ResponseWriter, res *domain.ShapedResult, etag, cacheState string) {
	w.Header().Set("ETag", etag)
	w.Header().Set("Cache-Control", "private, max-age=300")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"data": res,
		"meta": map[string]any{"cache": cacheState, "etag": etag},
	})
}

// handlePreview resolves an unsaved chart definition inline (CHART-FR-023 /
// BR-11): never cached, per-tenant concurrency cap, row cap 1,000.
func (s *Server) handlePreview(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	var in struct {
		ChartType string                `json:"chart_type"`
		Config    json.RawMessage       `json:"config"`
		Display   json.RawMessage       `json:"display_meta"`
		Sources   []domain.ChartSource  `json:"sources"`
		Request   domain.ResolveRequest `json:"request"`
	}
	if !decodeBody(w, r, &in) {
		return
	}
	// Preview resolves an UNSAVED spec, so there is no persisted resource to
	// derive the workspace from. chart.chart.read is workspace-scoped, and the
	// platform JWT carries no workspace claim, so authorize against the
	// workspace declared in display_meta.workspace_id. This is safe: OPA still
	// verifies the caller actually holds chart.chart.read in that workspace, so
	// a spoofed/foreign workspace simply denies (admin's `*` bypasses as before).
	if !s.authorize(w, r, authz.ActionChartRead, "", displayWorkspaceID(in.Display)) {
		return
	}
	select {
	case s.PreviewSem <- struct{}{}:
		defer func() { <-s.PreviewSem }()
	default:
		writeErr(w, r, domain.ERateLimited("preview concurrency cap reached (5/tenant)"))
		return
	}
	if err := domain.ValidateConfig(in.ChartType, in.Config, nil); err != nil {
		writeErr(w, r, err)
		return
	}
	chart := &domain.Chart{
		ID: newID(), TenantID: tenant, ChartType: in.ChartType, Config: rawOr(in.Config, "{}"),
		DisplayMeta: rawOr(in.Display, "{}"), ChartVersion: 0, Sources: normalizeSources(in.Sources),
	}
	req := in.Request
	req.Limit = 1000 // BR-11 row cap
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	res, err := s.Resolver.Resolve(ctx, bearerToken(r), chart, req)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, res)
}

// handleDrilldown executes a separate paginated query with the clicked
// dimension injected as a bind predicate (CHART-FR-040 / AC-6).
func (s *Server) handleDrilldown(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	chart, err := s.loadChartAuthorized(w, r, tenant, authz.ActionChartRead)
	if err != nil {
		return
	}
	queryURN := drilldownQueryURN(chart.DisplayMeta)
	if queryURN == "" {
		writeErr(w, r, domain.ENoDrilldown())
		return
	}
	var dr resolve.DrilldownRequest
	if !decodeBody(w, r, &dr) {
		return
	}
	for _, f := range dr.Filters {
		if !domain.AllowedFilterOps[f.Op] {
			writeErr(w, r, domain.EValidation("filter op not allowed: "+f.Op))
			return
		}
	}
	exec, err := s.Resolver.Drilldown(r.Context(), bearerToken(r), queryURN, dr)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	var next *string
	if exec.NextCursor != "" {
		next = &exec.NextCursor
	}
	writePage(w, map[string]any{"columns": exec.Columns, "rows": exec.Rows}, next, next != nil)
}

// handleBatchData resolves all charts of a dashboard with per-chart isolation
// (CHART-FR-024 / BR-8), fan-out ≤ 8.
func (s *Server) handleBatchData(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("dashboard not found"))
		return
	}
	d, err := s.Store.GetDashboard(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if !s.authorize(w, r, authz.ActionDashboardRead, events.URN(tenant, "dashboard", id.String()), d.WorkspaceID.String()) {
		return
	}
	var req domain.ResolveRequest
	_ = json.NewDecoder(r.Body).Decode(&req)
	charts, err := s.Store.ListCharts(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	token := bearerToken(r)

	// Cross-filter scoping (CHART-FR-041): a filter emitted by a chart selection
	// carries its origin chart id. Apply such a filter to a target chart only when
	// the two share a semantic model — so the filtered dimension is guaranteed to
	// exist — and never to the origin chart itself. Origin-less filters (manual
	// dashboard filters) apply to every chart, preserving prior behavior.
	modelByID := make(map[string]string, len(charts))
	for i := range charts {
		modelByID[charts[i].ID.String()] = resolve.ChartModel(&charts[i])
	}
	scopedFilters := func(ch *domain.Chart) []domain.Filter {
		return scopeCrossFilters(req.Filters, ch.ID.String(), modelByID)
	}
	type result struct {
		ChartID string      `json:"chart_id"`
		Data    interface{} `json:"data,omitempty"`
		Error   *errBody    `json:"error,omitempty"`
	}
	results := make([]result, len(charts))
	sem := make(chan struct{}, 8)
	var wg sync.WaitGroup
	for i := range charts {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			ch := &charts[i]
			ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second) // per-chart budget (BR-8)
			defer cancel()
			creq := req
			creq.Filters = scopedFilters(ch)
			res, err := s.Resolver.Resolve(ctx, token, ch, creq)
			results[i].ChartID = ch.ID.String()
			if err != nil {
				results[i].Error = toErrBody(err)
				return
			}
			results[i].Data = res
		}(i)
	}
	wg.Wait()
	writeData(w, http.StatusOK, map[string]any{"results": results})
}

// scopeCrossFilters selects which of a dashboard's batch filters apply to one
// target chart (CHART-FR-041). An origin-tagged filter (emitted by a chart
// selection) applies only to same-model siblings and never to its own origin,
// so the filtered dimension is guaranteed to exist and the source chart keeps
// its full view. Origin-less filters (manual dashboard filters) apply to all.
func scopeCrossFilters(filters []domain.Filter, targetID string, modelByID map[string]string) []domain.Filter {
	if len(filters) == 0 {
		return nil
	}
	targetModel := modelByID[targetID]
	out := make([]domain.Filter, 0, len(filters))
	for _, f := range filters {
		switch {
		case f.Origin == "":
			out = append(out, f)
		case f.Origin == targetID:
			// a chart never filters itself
		case targetModel != "" && modelByID[f.Origin] == targetModel:
			out = append(out, f)
		}
	}
	return out
}

type errBody struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func toErrBody(err error) *errBody {
	if de, ok := domain.AsError(err); ok {
		return &errBody{Code: de.Code, Message: de.Message}
	}
	return &errBody{Code: domain.CodeInternal, Message: "internal error"}
}

// displayWorkspaceID extracts display_meta.workspace_id, used to scope the
// preview authorization when there is no persisted resource to key off.
func displayWorkspaceID(displayMeta json.RawMessage) string {
	var meta struct {
		WorkspaceID string `json:"workspace_id"`
	}
	_ = json.Unmarshal(displayMeta, &meta)
	return meta.WorkspaceID
}

func drilldownQueryURN(displayMeta json.RawMessage) string {
	var meta struct {
		Drilldown struct {
			QueryURN string `json:"query_urn"`
		} `json:"drilldown"`
	}
	_ = json.Unmarshal(displayMeta, &meta)
	return meta.Drilldown.QueryURN
}
