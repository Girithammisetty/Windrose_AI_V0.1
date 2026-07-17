package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/go-common/httpx"
)

func newID() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.New()
	}
	return id
}

type dashboardWrite struct {
	Name        string          `json:"name"`
	Module      string          `json:"module"`
	WorkspaceID string          `json:"workspace_id"`
	Description string          `json:"description"`
	Layout      json.RawMessage `json:"layout"`
	Meta        json.RawMessage `json:"meta"`
	Tags        []string        `json:"tags"`
}

func (s *Server) handleCreateDashboard(w http.ResponseWriter, r *http.Request) {
	c, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	var in dashboardWrite
	if !decodeBody(w, r, &in) {
		return
	}
	if in.Name == "" || in.Module == "" {
		writeErr(w, r, domain.EValidation("name and module are required"))
		return
	}
	switch in.Module {
	case domain.ModuleInsights, domain.ModuleCaseManagement, domain.ModuleInspector:
	default:
		writeErr(w, r, domain.EValidation("module must be insights|case_management|inspector"))
		return
	}
	wsID, err := uuid.Parse(in.WorkspaceID)
	if err != nil {
		writeErr(w, r, domain.EValidation("valid workspace_id is required"))
		return
	}
	if !s.authorize(w, r, authz.ActionDashboardCreate, "", wsID.String()) {
		return
	}
	if err := validateLayout(in.Layout); err != nil {
		writeErr(w, r, err)
		return
	}

	d := &domain.Dashboard{
		ID: newID(), TenantID: tenant, WorkspaceID: wsID, Name: in.Name, Module: in.Module,
		Description: in.Description, Layout: in.Layout, Meta: in.Meta, Tags: orEmpty(in.Tags),
		OwnerUserID: c.EffectiveUser(), Status: "active",
	}
	urn := events.URN(tenant, "dashboard", d.ID.String())
	ev := events.New(events.DashboardCreated, tenant, "user", c.EffectiveUser(), urn, traceID(r.Context()),
		map[string]any{"dashboard_id": d.ID.String(), "module": d.Module})
	if err := s.replayableCreate(w, r, tenant, http.StatusCreated, dashboardView(d), func() error {
		return s.Store.CreateDashboard(r.Context(), d, []event.Envelope{ev})
	}); err != nil {
		writeErr(w, r, err)
	}
}

func (s *Server) handleListDashboards(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	q := r.URL.Query()
	wsID, err := uuid.Parse(q.Get("workspace_id"))
	if err != nil {
		writeErr(w, r, domain.EValidation("workspace_id query param is required"))
		return
	}
	if !s.authorize(w, r, authz.ActionDashboardRead, "", wsID.String()) {
		return
	}
	page, err := httpx.ParsePage(q.Get("limit"), q.Get("cursor"))
	if err != nil {
		writeErr(w, r, domain.EValidation(err.Error()))
		return
	}
	after, err := decodeCursor(q.Get("cursor"))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	archived := q.Get("filter[archived]") == "true"
	module := q.Get("filter[module]")
	tag := q.Get("filter[tag]")
	rows, err := s.Store.ListDashboards(r.Context(), tenant, wsID, module, archived, tag, page.Limit+1, after)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	var next *string
	hasMore := false
	if len(rows) > page.Limit {
		hasMore = true
		rows = rows[:page.Limit]
		cur := encodeCursor(rows[len(rows)-1].ID)
		next = &cur
	}
	views := make([]any, len(rows))
	for i := range rows {
		views[i] = dashboardView(&rows[i])
	}
	writePage(w, views, next, hasMore)
}

func (s *Server) handleGetDashboard(w http.ResponseWriter, r *http.Request) {
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
	writeData(w, http.StatusOK, dashboardView(d))
}

// handleListDashboardCharts returns the dashboard's child charts (full
// chartView objects) ordered by creation. This is the metadata enumerator the
// BFF pairs with the batch /data endpoint: one list call for name/type/config,
// one batch call for the rows (no N+1). Guarded by chart.chart.read scoped to
// the dashboard's workspace, mirroring the other dashboard routes.
func (s *Server) handleListDashboardCharts(w http.ResponseWriter, r *http.Request) {
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
	if !s.authorize(w, r, authz.ActionChartRead, events.URN(tenant, "dashboard", id.String()), d.WorkspaceID.String()) {
		return
	}
	charts, err := s.Store.ListCharts(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	views := make([]any, len(charts))
	for i := range charts {
		views[i] = chartView(&charts[i])
	}
	writeData(w, http.StatusOK, views)
}

func (s *Server) handleUpdateDashboard(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("dashboard not found"))
		return
	}
	cur, err := s.Store.GetDashboard(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if !s.authorize(w, r, authz.ActionDashboardUpdate, events.URN(tenant, "dashboard", id.String()), cur.WorkspaceID.String()) {
		return
	}
	var in dashboardWrite
	if !decodeBody(w, r, &in) {
		return
	}
	if in.Name != "" {
		cur.Name = in.Name
	}
	if in.Description != "" {
		cur.Description = in.Description
	}
	if in.Layout != nil {
		if err := validateLayout(in.Layout); err != nil {
			writeErr(w, r, err)
			return
		}
		cur.Layout = in.Layout
	}
	if in.Meta != nil {
		cur.Meta = in.Meta
	}
	if in.Tags != nil {
		cur.Tags = in.Tags
	}
	urn := events.URN(tenant, "dashboard", id.String())
	ev := events.New(events.DashboardUpdated, tenant, "user", subject(r), urn, traceID(r.Context()),
		map[string]any{"dashboard_id": id.String()})
	if err := s.Store.UpdateDashboard(r.Context(), cur, []event.Envelope{ev}); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, dashboardView(cur))
}

func (s *Server) handleDeleteDashboard(w http.ResponseWriter, r *http.Request) {
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
	if !s.authorize(w, r, authz.ActionDashboardDelete, events.URN(tenant, "dashboard", id.String()), d.WorkspaceID.String()) {
		return
	}
	// BR-4: no chart with allow_cases=true may block deletion.
	blocking, err := s.Store.DashboardBlockingCharts(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if len(blocking) > 0 {
		writeErr(w, r, domain.EChartHasCases(map[string]any{"blocking_chart_ids": idStrings(blocking)}))
		return
	}
	urn := events.URN(tenant, "dashboard", id.String())
	ev := events.New(events.DashboardDeleted, tenant, "user", subject(r), urn, traceID(r.Context()),
		map[string]any{"dashboard_id": id.String()})
	if err := s.Store.DeleteDashboard(r.Context(), tenant, id, []event.Envelope{ev}); err != nil {
		writeErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleArchiveDashboard(w http.ResponseWriter, r *http.Request) {
	s.setArchived(w, r, true)
}
func (s *Server) handleRestoreDashboard(w http.ResponseWriter, r *http.Request) {
	s.setArchived(w, r, false)
}

func (s *Server) setArchived(w http.ResponseWriter, r *http.Request, archived bool) {
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
	// archive/restore flip a persisted flag → authorized as an update (canonical verb).
	if !s.authorize(w, r, authz.ActionDashboardUpdate, events.URN(tenant, "dashboard", id.String()), d.WorkspaceID.String()) {
		return
	}
	et := events.DashboardArchived
	if !archived {
		et = events.DashboardRestored
	}
	ev := events.New(et, tenant, "user", subject(r), events.URN(tenant, "dashboard", id.String()), traceID(r.Context()),
		map[string]any{"dashboard_id": id.String()})
	if err := s.Store.SetDashboardArchived(r.Context(), tenant, id, archived, []event.Envelope{ev}); err != nil {
		writeErr(w, r, err)
		return
	}
	d.Archived = archived
	writeData(w, http.StatusOK, dashboardView(d))
}

// --- helpers ---

func (s *Server) replayableCreate(w http.ResponseWriter, r *http.Request, tenant uuid.UUID, status int, view any, create func() error) error {
	key := r.Header.Get("Idempotency-Key")
	if key != "" {
		if st, body, found, err := s.Store.GetIdempotent(r.Context(), tenant, key, r.Method, r.URL.Path); err == nil && found {
			w.Header().Set("Idempotency-Replayed", "true")
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(st)
			_, _ = w.Write(body)
			return nil
		}
	}
	if err := create(); err != nil {
		return err
	}
	body, _ := json.Marshal(map[string]any{"data": view})
	if key != "" {
		_ = s.Store.PutIdempotent(r.Context(), tenant, key, r.Method, r.URL.Path, status, body)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
	return nil
}

func subject(r *http.Request) string {
	if c, ok := authClaims(r); ok {
		return c.EffectiveUser()
	}
	return ""
}

func validateLayout(raw json.RawMessage) error {
	if len(raw) == 0 {
		return nil
	}
	var placements []domain.LayoutPlacement
	if err := json.Unmarshal(raw, &placements); err != nil {
		return domain.EValidation("layout must be an array of grid placements")
	}
	// overlap detection (CHART-FR-002).
	type rect struct{ x, y, w, h int }
	rects := make([]rect, 0, len(placements))
	for _, p := range placements {
		if p.W <= 0 || p.H <= 0 {
			return domain.EValidation("layout placement w/h must be positive")
		}
		nr := rect{p.X, p.Y, p.W, p.H}
		for _, e := range rects {
			if nr.x < e.x+e.w && e.x < nr.x+nr.w && nr.y < e.y+e.h && e.y < nr.y+nr.h {
				return domain.EValidation("layout placements overlap")
			}
		}
		rects = append(rects, nr)
	}
	return nil
}

func orEmpty(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func idStrings(ids []uuid.UUID) []string {
	out := make([]string, len(ids))
	for i, id := range ids {
		out[i] = id.String()
	}
	return out
}

func dashboardView(d *domain.Dashboard) map[string]any {
	v := map[string]any{
		"id": d.ID, "workspace_id": d.WorkspaceID, "name": d.Name, "module": d.Module,
		"description": d.Description, "layout": rawOr(d.Layout, "[]"), "meta": rawOr(d.Meta, "{}"),
		"tags": orEmpty(d.Tags), "owner_user_id": d.OwnerUserID, "status": d.Status,
		"archived": d.Archived, "created_at": d.CreatedAt, "updated_at": d.UpdatedAt,
	}
	if d.LastContent != nil {
		v["last_content_updated_at"] = d.LastContent
	}
	return v
}

func rawOr(raw json.RawMessage, def string) json.RawMessage {
	if len(raw) == 0 {
		return json.RawMessage(def)
	}
	return raw
}
