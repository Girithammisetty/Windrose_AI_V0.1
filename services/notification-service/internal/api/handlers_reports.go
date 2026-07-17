package api

import (
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/domain"
)

var emailRe = regexp.MustCompile(`^[^@\s]+@[^@\s]+\.[^@\s]+$`)

type reportBody struct {
	DashboardID string   `json:"dashboard_id"`
	WorkspaceID string   `json:"workspace_id"`
	Name        string   `json:"name"`
	Recipients  []string `json:"recipients"`
	Cadence     string   `json:"cadence"`
	SendHour    *int     `json:"send_hour"`
	SendWeekday *int     `json:"send_weekday"`
	Timezone    string   `json:"timezone"`
	Format      string   `json:"format"`
	Enabled     *bool    `json:"enabled"`
}

func validateReportBody(body reportBody, requireDashboard bool) error {
	if requireDashboard {
		if _, err := uuid.Parse(body.DashboardID); err != nil {
			return domain.EValidation("dashboard_id must be a valid uuid", nil)
		}
		if _, err := uuid.Parse(body.WorkspaceID); err != nil {
			return domain.EValidation("workspace_id must be a valid uuid", nil)
		}
	}
	if body.Name == "" {
		return domain.EValidation("name is required", nil)
	}
	if len(body.Recipients) == 0 || len(body.Recipients) > 50 {
		return domain.EValidation("recipients must have between 1 and 50 addresses", nil)
	}
	for _, r := range body.Recipients {
		if !emailRe.MatchString(r) {
			return domain.EValidation("invalid recipient email: "+r, map[string]any{"recipient": r})
		}
	}
	if body.Cadence != domain.CadenceDaily && body.Cadence != domain.CadenceWeekly {
		return domain.EValidation("cadence must be 'daily' or 'weekly'", nil)
	}
	if body.Cadence == domain.CadenceWeekly && (body.SendWeekday == nil || *body.SendWeekday < 0 || *body.SendWeekday > 6) {
		return domain.EValidation("send_weekday (0-6) is required for a weekly cadence", nil)
	}
	if body.SendHour != nil && (*body.SendHour < 0 || *body.SendHour > 23) {
		return domain.EValidation("send_hour must be between 0 and 23", nil)
	}
	if body.Format != "" && body.Format != domain.ReportFormatHTML && body.Format != domain.ReportFormatText {
		return domain.EValidation("format must be 'html' or 'text'", nil)
	}
	return nil
}

func (s *Server) handleCreateReport(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	var body reportBody
	if !decodeBody(w, r, &body) {
		return
	}
	body.Recipients = trimAll(body.Recipients)
	if err := validateReportBody(body, true); err != nil {
		writeErr(w, r, err)
		return
	}
	dashboardID, _ := uuid.Parse(body.DashboardID)
	workspaceID, _ := uuid.Parse(body.WorkspaceID)
	hour := 8
	if body.SendHour != nil {
		hour = *body.SendHour
	}
	tz := defStr(body.Timezone, "UTC")
	format := defStr(body.Format, domain.ReportFormatHTML)
	enabled := true
	if body.Enabled != nil {
		enabled = *body.Enabled
	}
	now := time.Now().UTC()
	sub := &domain.ReportSubscription{
		ID: domain.NewID(), TenantID: o.Tenant, WorkspaceID: workspaceID, DashboardID: dashboardID,
		Name: body.Name, Recipients: body.Recipients, Cadence: body.Cadence, SendHour: hour,
		SendWeekday: body.SendWeekday, Timezone: tz, Format: format, Enabled: enabled,
		CreatedBy: o.UserID, CreatedAt: now, UpdatedAt: now,
	}
	if err := s.Store.CreateReportSubscription(r.Context(), sub); err != nil {
		writeErr(w, r, err)
		return
	}
	if s.Reports != nil {
		scheduleID, err := s.Reports.Ensure(r.Context(), sub)
		if err != nil {
			writeErr(w, r, domain.EValidation("subscription saved but the schedule could not be created: "+err.Error(), nil))
			return
		}
		sub.TemporalScheduleID = scheduleID
		_ = s.Store.SetReportScheduleID(r.Context(), o.Tenant, sub.ID, scheduleID)
	}
	writeData(w, http.StatusCreated, sub)
}

func (s *Server) handleListReports(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	limit, cursor := parsePage(r)
	var dashboardID *uuid.UUID
	if v := r.URL.Query().Get("dashboard_id"); v != "" {
		if id, err := uuid.Parse(v); err == nil {
			dashboardID = &id
		}
	}
	list, err := s.Store.ListReportSubscriptions(r.Context(), o.Tenant, dashboardID, limit+1, cursor)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	page := PageInfo{}
	if len(list) > limit {
		list = list[:limit]
		page = PageInfo{NextCursor: list[len(list)-1].ID.String(), HasMore: true}
	}
	if list == nil {
		list = []*domain.ReportSubscription{}
	}
	writeJSON(w, http.StatusOK, PageEnvelope[*domain.ReportSubscription]{Data: list, Page: page})
}

func (s *Server) handleGetReport(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	sub, err := s.Store.GetReportSubscription(r.Context(), o.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, sub)
}

func (s *Server) handleUpdateReport(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	sub, err := s.Store.GetReportSubscription(r.Context(), o.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	var body reportBody
	if !decodeBody(w, r, &body) {
		return
	}
	if body.Name != "" {
		sub.Name = body.Name
	}
	if len(body.Recipients) > 0 {
		sub.Recipients = trimAll(body.Recipients)
	}
	if body.Cadence != "" {
		sub.Cadence = body.Cadence
	}
	if body.SendHour != nil {
		sub.SendHour = *body.SendHour
	}
	if body.SendWeekday != nil {
		sub.SendWeekday = body.SendWeekday
	}
	if body.Timezone != "" {
		sub.Timezone = body.Timezone
	}
	if body.Format != "" {
		sub.Format = body.Format
	}
	if body.Enabled != nil {
		sub.Enabled = *body.Enabled
	}
	if err := validateReportBody(reportBody{
		DashboardID: sub.DashboardID.String(), WorkspaceID: sub.WorkspaceID.String(), Name: sub.Name,
		Recipients: sub.Recipients, Cadence: sub.Cadence, SendHour: &sub.SendHour, SendWeekday: sub.SendWeekday,
		Timezone: sub.Timezone, Format: sub.Format,
	}, false); err != nil {
		writeErr(w, r, err)
		return
	}
	if s.Reports != nil {
		scheduleID, err := s.Reports.Ensure(r.Context(), sub)
		if err != nil {
			writeErr(w, r, domain.EValidation("could not sync the schedule: "+err.Error(), nil))
			return
		}
		sub.TemporalScheduleID = scheduleID
	}
	if err := s.Store.UpdateReportSubscription(r.Context(), sub); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, sub)
}

func (s *Server) handleDeleteReport(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	sub, err := s.Store.GetReportSubscription(r.Context(), o.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if err := s.Store.DeleteReportSubscription(r.Context(), o.Tenant, id); err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if s.Reports != nil && sub.TemporalScheduleID != "" {
		_ = s.Reports.Delete(r.Context(), sub.TemporalScheduleID)
	}
	w.WriteHeader(http.StatusNoContent)
}

// handleTriggerReport fires one immediate real Temporal run outside the cron
// cadence — a "send now" action, and the mechanism used to verify delivery
// live without waiting for the schedule to tick.
func (s *Server) handleTriggerReport(w http.ResponseWriter, r *http.Request) {
	o, ok := op(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing claims"))
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return
	}
	sub, err := s.Store.GetReportSubscription(r.Context(), o.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	if s.Reports == nil {
		writeErr(w, r, domain.EValidation("temporal scheduling is not configured on this deployment", nil))
		return
	}
	if err := s.Reports.TriggerNow(r.Context(), sub.TemporalScheduleID); err != nil {
		writeErr(w, r, domain.EValidation("trigger failed: "+err.Error(), nil))
		return
	}
	w.WriteHeader(http.StatusAccepted)
}

func trimAll(ss []string) []string {
	out := make([]string, 0, len(ss))
	for _, s := range ss {
		if t := strings.TrimSpace(s); t != "" {
			out = append(out, t)
		}
	}
	return out
}
