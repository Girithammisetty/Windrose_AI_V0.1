package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/go-common/event"
)

type linkWrite struct {
	ChildChartID  string              `json:"child_chart_id"`
	LinkedColumns []domain.ColumnPair `json:"linked_columns"`
	LinkType      *int                `json:"link_type"`
}

// handleCreateLink creates a cross-module link parent→child transactionally
// with cycle detection (CHART-FR-015 / BR-9 / AC-10).
func (s *Server) handleCreateLink(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	// link/unlink mutate the chart's persisted back-reference → authorized as update.
	parent, err := s.loadChartAuthorized(w, r, tenant, authz.ActionChartUpdate)
	if err != nil {
		return
	}
	var in linkWrite
	if !decodeBody(w, r, &in) {
		return
	}
	childID, err := uuid.Parse(in.ChildChartID)
	if err != nil {
		writeErr(w, r, domain.EValidation("valid child_chart_id is required"))
		return
	}
	linkType := domain.LinkMainSecondary
	if in.LinkType != nil {
		if *in.LinkType != domain.LinkSharedSource && *in.LinkType != domain.LinkMainSecondary {
			writeErr(w, r, domain.EValidation("link_type must be 0 or 1"))
			return
		}
		linkType = *in.LinkType
	}
	ev := events.New(events.ChartLinkCreated, tenant, "user", subject(r),
		events.URN(tenant, "chart", parent.ID.String()), traceID(r.Context()),
		map[string]any{"parent_chart_id": parent.ID.String(), "child_chart_id": childID.String(), "link_type": linkType})
	if err := s.Store.CreateLink(r.Context(), tenant, parent.ID, childID, in.LinkedColumns, linkType, []event.Envelope{ev}); err != nil {
		writeErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, map[string]any{
		"parent_chart_id": parent.ID, "child_chart_id": childID, "link_type": linkType, "linked_columns": in.LinkedColumns,
	})
}

// handleRemoveLink removes a link and clears the child back-reference (AC-10).
func (s *Server) handleRemoveLink(w http.ResponseWriter, r *http.Request) {
	_, tenant, ok := s.claims(w, r)
	if !ok {
		return
	}
	parentID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.ENotFound("chart not found"))
		return
	}
	parent, err := s.Store.GetChart(r.Context(), tenant, parentID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	d, err := s.Store.GetDashboard(r.Context(), tenant, parent.DashboardID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if !s.authorize(w, r, authz.ActionChartUpdate, events.URN(tenant, "chart", parentID.String()), d.WorkspaceID.String()) {
		return
	}
	var in linkWrite
	if !decodeBody(w, r, &in) {
		return
	}
	childID, err := uuid.Parse(in.ChildChartID)
	if err != nil {
		writeErr(w, r, domain.EValidation("valid child_chart_id is required"))
		return
	}
	ev := events.New(events.ChartLinkRemoved, tenant, "user", subject(r),
		events.URN(tenant, "chart", parentID.String()), traceID(r.Context()),
		map[string]any{"parent_chart_id": parentID.String(), "child_chart_id": childID.String()})
	if err := s.Store.RemoveLink(r.Context(), tenant, parentID, childID, []event.Envelope{ev}); err != nil {
		writeErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
