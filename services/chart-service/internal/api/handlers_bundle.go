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
)

// bundle is the portable dashboard export shape (CHART-FR-005 / AC-14).
type bundle struct {
	Dashboard struct {
		Name        string          `json:"name"`
		Module      string          `json:"module"`
		Description string          `json:"description"`
		Layout      json.RawMessage `json:"layout"`
		Meta        json.RawMessage `json:"meta"`
		Tags        []string        `json:"tags"`
	} `json:"dashboard"`
	Charts []bundleChart `json:"charts"`
}

type bundleChart struct {
	Name        string               `json:"name"`
	ChartType   string               `json:"chart_type"`
	Description string               `json:"description"`
	Config      json.RawMessage      `json:"config"`
	DisplayMeta json.RawMessage      `json:"display_meta"`
	Sources     []domain.ChartSource `json:"sources"`
}

// handleExportBundle emits a self-contained JSON bundle (CHART-FR-005).
func (s *Server) handleExportBundle(w http.ResponseWriter, r *http.Request) {
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
	if !s.authorize(w, r, authz.ActionDashboardExport, events.URN(tenant, "dashboard", id.String()), d.WorkspaceID.String()) {
		return
	}
	charts, err := s.Store.ListCharts(r.Context(), tenant, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	var b bundle
	b.Dashboard.Name = d.Name
	b.Dashboard.Module = d.Module
	b.Dashboard.Description = d.Description
	b.Dashboard.Layout = rawOr(d.Layout, "[]")
	b.Dashboard.Meta = rawOr(d.Meta, "{}")
	b.Dashboard.Tags = orEmpty(d.Tags)
	for i := range charts {
		c := &charts[i]
		b.Charts = append(b.Charts, bundleChart{
			Name: c.Name, ChartType: c.ChartType, Description: c.Description,
			Config: rawOr(c.Config, "{}"), DisplayMeta: rawOr(c.DisplayMeta, "{}"), Sources: c.Sources,
		})
	}
	writeData(w, http.StatusOK, b)
}

type importReq struct {
	Bundle      bundle            `json:"bundle"`
	WorkspaceID string            `json:"workspace_id"`
	URNMapping  map[string]string `json:"url_mapping"`
}

// handleImportBundle imports a bundle into a workspace, remapping source URNs;
// unmapped URNs fail atomically (CHART-FR-005 / BR-10 / AC-14).
func (s *Server) handleImportBundle(w http.ResponseWriter, r *http.Request) {
	c, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	var in importReq
	if !decodeBody(w, r, &in) {
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
	// Collect + validate URN remapping BEFORE any write (BR-10 atomicity).
	var unmapped []string
	remap := func(urn string) string {
		if urn == "" {
			return ""
		}
		if to, ok := in.URNMapping[urn]; ok {
			return to
		}
		unmapped = append(unmapped, urn)
		return urn
	}
	type preparedChart struct {
		bc          bundleChart
		displayMeta json.RawMessage
	}
	var prepared []preparedChart
	for _, bc := range in.Bundle.Charts {
		for i := range bc.Sources {
			bc.Sources[i].SourceURN = remap(bc.Sources[i].SourceURN)
		}
		dm := remapDrilldown(bc.DisplayMeta, remap)
		prepared = append(prepared, preparedChart{bc: bc, displayMeta: dm})
	}
	if len(unmapped) > 0 {
		writeErr(w, r, &domain.Error{Status: http.StatusUnprocessableEntity, Code: domain.CodeUnmappedURN,
			Message: "bundle references URNs not present in url_mapping", Details: map[string]any{"unmapped": unmapped}})
		return
	}

	// Create the dashboard, then its charts (best-effort atomic: dashboard
	// first; a chart failure leaves the dashboard, mirroring V1 importer).
	d := &domain.Dashboard{
		ID: newID(), TenantID: tenant, WorkspaceID: wsID, Name: in.Bundle.Dashboard.Name,
		Module: in.Bundle.Dashboard.Module, Description: in.Bundle.Dashboard.Description,
		Layout: rawOr(in.Bundle.Dashboard.Layout, "[]"), Meta: rawOr(in.Bundle.Dashboard.Meta, "{}"),
		Tags: orEmpty(in.Bundle.Dashboard.Tags), OwnerUserID: c.EffectiveUser(), Status: "active",
	}
	durn := events.URN(tenant, "dashboard", d.ID.String())
	dev := events.New(events.DashboardCreated, tenant, "user", c.EffectiveUser(), durn, traceID(r.Context()),
		map[string]any{"dashboard_id": d.ID.String(), "imported": true})
	if err := s.Store.CreateDashboard(r.Context(), d, []event.Envelope{dev}); err != nil {
		writeErr(w, r, err)
		return
	}
	created := 0
	for _, pc := range prepared {
		chart := &domain.Chart{
			ID: newID(), TenantID: tenant, DashboardID: d.ID, Name: pc.bc.Name, ChartType: pc.bc.ChartType,
			Description: pc.bc.Description, Config: rawOr(pc.bc.Config, "{}"), DisplayMeta: rawOr(pc.displayMeta, "{}"),
			ChartVersion: 1, Custom: true, ConfigStatus: "ok", Sources: normalizeSources(pc.bc.Sources),
		}
		curn := events.URN(tenant, "chart", chart.ID.String())
		cev := events.New(events.ChartCreated, tenant, "user", c.EffectiveUser(), curn, traceID(r.Context()),
			map[string]any{"chart_id": chart.ID.String(), "dashboard_id": d.ID.String(), "source_urns": sourceURNs(chart.Sources)})
		if err := s.Store.CreateChart(r.Context(), chart, []event.Envelope{cev}); err != nil {
			writeErr(w, r, err)
			return
		}
		created++
	}
	writeData(w, http.StatusCreated, map[string]any{"dashboard_id": d.ID.String(), "charts_created": created})
}

// remapDrilldown rewrites display_meta.drilldown.{query_urn,dataset_urn}.
func remapDrilldown(displayMeta json.RawMessage, remap func(string) string) json.RawMessage {
	var m map[string]any
	if len(displayMeta) == 0 || json.Unmarshal(displayMeta, &m) != nil {
		return displayMeta
	}
	if dd, ok := m["drilldown"].(map[string]any); ok {
		if q, ok := dd["query_urn"].(string); ok && q != "" {
			dd["query_urn"] = remap(q)
		}
		if ds, ok := dd["dataset_urn"].(string); ok && ds != "" {
			dd["dataset_urn"] = remap(ds)
		}
		m["drilldown"] = dd
	}
	out, _ := json.Marshal(m)
	return out
}
