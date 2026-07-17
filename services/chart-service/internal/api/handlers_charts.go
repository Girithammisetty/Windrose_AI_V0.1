package api

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/go-common/event"
)

type chartWrite struct {
	Name        string               `json:"name"`
	ChartType   string               `json:"chart_type"`
	Description string               `json:"description"`
	Config      json.RawMessage      `json:"config"`
	DisplayMeta json.RawMessage      `json:"display_meta"`
	Sources     []domain.ChartSource `json:"sources"`
}

func (s *Server) handleCreateChart(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	dashID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("dashboard not found"))
		return
	}
	d, err := s.Store.GetDashboard(r.Context(), tenant, dashID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if !s.authorize(w, r, authz.ActionChartCreate, events.URN(tenant, "dashboard", dashID.String()), d.WorkspaceID.String()) {
		return
	}
	var in chartWrite
	if !decodeBody(w, r, &in) {
		return
	}
	if in.Name == "" || in.ChartType == "" {
		writeErr(w, r, domain.EValidation("name and chart_type are required"))
		return
	}
	if err := s.validateChartInput(r, &in); err != nil {
		writeErr(w, r, err)
		return
	}
	c := &domain.Chart{
		ID: newID(), TenantID: tenant, DashboardID: dashID, Name: in.Name, ChartType: in.ChartType,
		Description: in.Description, Config: rawOr(in.Config, "{}"), DisplayMeta: rawOr(in.DisplayMeta, "{}"),
		ChartVersion: 1, Custom: true, ConfigStatus: "ok", Sources: normalizeSources(in.Sources),
	}
	urn := events.URN(tenant, "chart", c.ID.String())
	ev := events.New(events.ChartCreated, tenant, "user", subject(r), urn, traceID(r.Context()),
		map[string]any{"chart_id": c.ID.String(), "dashboard_id": dashID.String(),
			"chart_type": c.ChartType, "chart_version": 1, "source_urns": sourceURNs(c.Sources)})
	if err := s.replayableCreate(w, r, tenant, http.StatusCreated, chartView(c), func() error {
		return s.Store.CreateChart(r.Context(), c, []event.Envelope{ev})
	}); err != nil {
		writeErr(w, r, err)
	}
}

func (s *Server) handleGetChart(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	c, err := s.loadChartAuthorized(w, r, tenant, authz.ActionChartRead)
	if err != nil {
		return
	}
	writeData(w, http.StatusOK, chartView(c))
}

func (s *Server) handleUpdateChart(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	c, err := s.loadChartAuthorized(w, r, tenant, authz.ActionChartUpdate)
	if err != nil {
		return
	}
	var in chartWrite
	if !decodeBody(w, r, &in) {
		return
	}
	versionBump := false
	if in.Name != "" {
		c.Name = in.Name
	}
	if in.Description != "" {
		c.Description = in.Description
	}
	if in.ChartType != "" && in.ChartType != c.ChartType {
		c.ChartType = in.ChartType
		versionBump = true
	}
	if in.Config != nil {
		c.Config = in.Config
		versionBump = true
	}
	if in.DisplayMeta != nil {
		c.DisplayMeta = in.DisplayMeta // display-only change: no version bump
	}
	if in.Sources != nil {
		c.Sources = normalizeSources(in.Sources)
		versionBump = true
	}
	// Re-validate config against (possibly new) type + sources.
	if err := s.validateChartInput(r, &chartWrite{ChartType: c.ChartType, Config: c.Config, Sources: c.Sources}); err != nil {
		writeErr(w, r, err)
		return
	}
	expect := 0
	if m := r.Header.Get("If-Match"); m != "" { // optimistic lock (BR-7)
		if v, err := strconv.Atoi(m); err == nil {
			expect = v
		}
	}
	urn := events.URN(tenant, "chart", c.ID.String())
	ev := events.New(events.ChartUpdated, tenant, "user", subject(r), urn, traceID(r.Context()),
		map[string]any{"chart_id": c.ID.String(), "chart_type": c.ChartType, "source_urns": sourceURNs(c.Sources)})
	if err := s.Store.UpdateChart(r.Context(), c, versionBump, expect, []event.Envelope{ev}); err != nil {
		writeErr(w, r, err)
		return
	}
	// Evict cache for this chart on any change (CHART-FR-031 own update).
	_ = s.Cache.InvalidateChart(r.Context(), tenant.String(), c.ID.String())
	writeData(w, http.StatusOK, chartView(c))
}

func (s *Server) handleDeleteChart(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("chart not found"))
		return
	}
	c, err := s.Store.GetChart(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	d, err := s.Store.GetDashboard(r.Context(), tenant, c.DashboardID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if !s.authorize(w, r, authz.ActionChartDelete, events.URN(tenant, "chart", id.String()), d.WorkspaceID.String()) {
		return
	}
	// CHART-FR-016 / AC-8: allow_cases guard → 412.
	allows, err := s.Store.ChartAllowsCases(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if allows {
		writeErr(w, r, domain.EChartHasCases(map[string]any{"chart_id": id.String()}))
		return
	}
	urn := events.URN(tenant, "chart", id.String())
	ev := events.New(events.ChartDeleted, tenant, "user", subject(r), urn, traceID(r.Context()),
		map[string]any{"chart_id": id.String()})
	if err := s.Store.DeleteChart(r.Context(), tenant, id, []event.Envelope{ev}); err != nil {
		writeErr(w, r, err)
		return
	}
	_ = s.Cache.InvalidateChart(r.Context(), tenant.String(), id.String())
	w.WriteHeader(http.StatusNoContent)
}

// --- helpers ---

// loadChartAuthorized loads a chart and authorizes action on its dashboard.
func (s *Server) loadChartAuthorized(w http.ResponseWriter, r *http.Request, tenant uuid.UUID, action string) (*domain.Chart, error) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("chart not found"))
		return nil, err
	}
	c, err := s.Store.GetChart(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return nil, err
	}
	d, err := s.Store.GetDashboard(r.Context(), tenant, c.DashboardID)
	if err != nil {
		writeErr(w, r, err)
		return nil, err
	}
	if !s.authorize(w, r, action, events.URN(tenant, "chart", id.String()), d.WorkspaceID.String()) {
		return nil, errAuthorized
	}
	return c, nil
}

var errAuthorized = domain.EPermission("denied")

func (s *Server) validateChartInput(r *http.Request, in *chartWrite) error {
	if _, ok := domain.LookupType(in.ChartType); !ok {
		return &domain.Error{Status: 422, Code: domain.CodeUnknownChartType, Message: "unknown chart_type " + in.ChartType}
	}
	for i, src := range in.Sources {
		switch src.SourceType {
		case domain.SourceSemanticMeasure, domain.SourceSavedQuery, domain.SourceDataset, domain.SourceMLRun:
		default:
			return domain.EValidation("invalid source_type", []domain.FieldDetail{{Field: "sources[" + strconv.Itoa(i) + "].source_type", Code: "INVALID"}})
		}
		if src.SourceURN == "" {
			return domain.EValidation("source_urn is required", []domain.FieldDetail{{Field: "sources[" + strconv.Itoa(i) + "].source_urn", Code: "REQUIRED"}})
		}
	}
	// Discover known fields from upstream metadata when a validator is wired
	// (CHART-FR-013); nil in dev falls back to structural validation only.
	var known map[string]bool
	if s.Fields != nil {
		if k, err := s.Fields.KnownFields(r.Context(), bearerToken(r), in.Sources); err == nil {
			known = k
		}
	}
	return domain.ValidateConfig(in.ChartType, in.Config, known)
}

func normalizeSources(in []domain.ChartSource) []domain.ChartSource {
	out := make([]domain.ChartSource, len(in))
	for i, s := range in {
		s.Position = i
		out[i] = s
	}
	return out
}

func sourceURNs(sources []domain.ChartSource) []string {
	out := make([]string, len(sources))
	for i, s := range sources {
		out[i] = s.SourceURN
	}
	return out
}

func chartView(c *domain.Chart) map[string]any {
	return map[string]any{
		"id": c.ID, "dashboard_id": c.DashboardID, "name": c.Name, "chart_type": c.ChartType,
		"description": c.Description, "config": rawOr(c.Config, "{}"), "display_meta": rawOr(c.DisplayMeta, "{}"),
		"chart_version": c.ChartVersion, "custom": c.Custom, "config_status": c.ConfigStatus,
		"link_type": c.LinkType, "linked_parent_id": c.LinkedParentID, "sources": c.Sources,
		"created_at": c.CreatedAt, "updated_at": c.UpdatedAt,
	}
}
