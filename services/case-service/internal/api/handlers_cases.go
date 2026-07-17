package api

import (
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
	"github.com/windrose-ai/case-service/internal/search"
	"github.com/windrose-ai/case-service/internal/store"
)

type createReq struct {
	QueryURN       string `json:"query_urn"`
	DashboardURN   string `json:"dashboard_urn"`
	DatasetURN     string `json:"dataset_urn"`
	DatasetVersion string `json:"dataset_version"`
	DueDate        time.Time `json:"due_date"`
	AssignedToID   string `json:"assigned_to_id"`
	Severity       string `json:"severity"`
	Description    string `json:"description"`
	CustomFields   map[string]any `json:"custom_fields"`
	Rows           []struct {
		RowPK             string            `json:"row_pk"`
		DisplayProjection map[string]string `json:"display_projection"`
	} `json:"rows"`
}

// handleCreateCases creates 1..500 cases from query rows, dedup-aware
// (CASE-FR-002/005, AC-1/AC-2).
func (s *Server) handleCreateCases(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("bad claims"))
		return
	}
	ws, ok := workspaceFromClaims(r)
	if !ok {
		writeErr(w, r, domain.EValidation("workspace_id claim required", nil))
		return
	}
	var req createReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.DatasetURN == "" {
		writeErr(w, r, domain.EValidation("dataset_urn is required", nil))
		return
	}
	if len(req.Rows) == 0 || len(req.Rows) > 500 {
		writeErr(w, r, domain.EBatchTooLarge("rows must be 1..500"))
		return
	}
	if req.DueDate.IsZero() {
		writeErr(w, r, domain.EValidation("due_date is required", nil))
		return
	}
	allowOverdue := r.URL.Query().Get("allow_overdue") == "true"
	if req.DueDate.Before(time.Now()) && !allowOverdue {
		writeErr(w, r, domain.EValidation("due_date must be in the future (BR-12); managers may pass ?allow_overdue=true", nil))
		return
	}
	severity := req.Severity
	if severity == "" {
		severity = domain.SeverityMedium
	}
	if !domain.ValidSeverity(severity) {
		writeErr(w, r, domain.EValidation("invalid severity", nil))
		return
	}
	// Validate custom fields against the create-mode catalog (CASE-FR-023).
	if err := s.validateCustomFields(r.Context(), op.Tenant, ws, req.QueryURN, req.CustomFields); err != nil {
		writeErr(w, r, err)
		return
	}

	var assignee *uuid.UUID
	status := domain.StatusUnassigned
	if req.AssignedToID != "" {
		a, err := uuid.Parse(req.AssignedToID)
		if err != nil {
			writeErr(w, r, domain.EValidation("assigned_to_id must be a uuid", nil))
			return
		}
		assignee = &a
		status = domain.StatusDraft
	}

	now := time.Now().UTC()
	sourceURNs := []string{}
	if req.QueryURN != "" {
		sourceURNs = []string{req.QueryURN}
	}
	cases := make([]*domain.Case, 0, len(req.Rows))
	for _, row := range req.Rows {
		proj, truncated := domain.TruncateProjection(row.DisplayProjection)
		var dedup *string
		if k, ok := domain.DedupKey(req.DatasetURN, row.RowPK); ok {
			dedup = &k
		}
		c := &domain.Case{
			ID: domain.NewID(), TenantID: op.Tenant, WorkspaceID: ws, Status: status, Severity: severity,
			AssignedToID: assignee, CreatedByID: op.Actor.ID, DatasetURN: req.DatasetURN, DatasetVersion: req.DatasetVersion,
			RowPK: row.RowPK, DedupKey: dedup, DisplayProjection: proj, ProjectionTruncated: truncated,
			SourceQueryURNs: append([]string{}, sourceURNs...), DashboardURN: req.DashboardURN, DueDate: req.DueDate,
			Description: req.Description, CustomFields: nonNilMap(req.CustomFields), CaseVersion: 1, CreatedAt: now, UpdatedAt: now,
		}
		if assignee != nil {
			c.AssignedToAt = &now
		}
		cases = append(cases, c)
	}

	policy, err := s.Store.GetSLAPolicy(r.Context(), op.Tenant, ws)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	created, deduped, err := s.Store.CreateCases(r.Context(), op, cases, req.QueryURN, policy.WarnBefore)
	if err != nil {
		writeErr(w, r, err)
		return
	}

	createdOut := make([]map[string]any, 0, len(created))
	for _, c := range created {
		createdOut = append(createdOut, map[string]any{
			"id": c.ID, "case_number": c.CaseNumber, "status": c.Status.String(), "dedup_key": derefStr(c.DedupKey),
			"recurrence_of": c.RecurrenceOf,
		})
	}
	dedupedOut := make([]map[string]any, 0, len(deduped))
	for _, d := range deduped {
		dedupedOut = append(dedupedOut, map[string]any{
			"id": d.Case.ID, "case_number": d.Case.CaseNumber, "row_pk": d.RowPK, "source_query_urns": d.Case.SourceQueryURNs,
		})
	}
	if len(deduped) > 0 {
		w.Header().Set("X-Case-Deduplicated", "true")
	}
	writeData(w, http.StatusCreated, map[string]any{"created": createdOut, "deduplicated": dedupedOut})
}

// handleGetCase reads a case from Postgres (source of truth), optionally
// hydrating the live row (CASE-FR-001, BR-5).
func (s *Server) handleGetCase(w http.ResponseWriter, r *http.Request) {
	tenant, id, ok := s.pathCase(w, r)
	if !ok {
		return
	}
	c, err := s.Store.GetCase(r.Context(), tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	out := caseView(c)
	if r.URL.Query().Get("with_row") == "true" {
		row, ferr := s.fetchRow(r, c)
		if ferr != nil {
			out["row"] = nil
			out["row_error"] = ferr.Error()
		} else {
			out["row"] = row
		}
	}
	writeData(w, http.StatusOK, out)
}

// fetchRow hydrates the live row, forwarding the CALLER's bearer token to
// query-service so the fetch is authorized as the end user (CASE-FR-001).
func (s *Server) fetchRow(r *http.Request, c *domain.Case) (map[string]any, error) {
	ctx := r.Context()
	if c.SnapshotRef != "" && s.Snapshots != nil {
		// Closed case: serve the archived snapshot, immune to dataset changes (AC-8).
		return s.Snapshots.Get(ctx, c.SnapshotRef)
	}
	if s.RowFetcher == nil {
		return nil, errRowUnconfigured
	}
	bearer := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	return s.RowFetcher.FetchRow(ctx, bearer, c.TenantID, c.DatasetURN, c.DatasetVersion, c.RowPK)
}

var errRowUnconfigured = &domain.Error{Code: domain.CodeRowFetchFailed, HTTP: http.StatusBadGateway, Message: "live row fetch unavailable"}

type patchReq struct {
	Description  *string        `json:"description"`
	DueDate      *time.Time     `json:"due_date"`
	Severity     *string        `json:"severity"`
	CustomFields map[string]any `json:"custom_fields"`
}

// handlePatchCase updates description/due_date/severity/custom_fields with
// optimistic concurrency (If-Match case_version, §5).
func (s *Server) handlePatchCase(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("bad claims"))
		return
	}
	_, id, ok := s.pathCase(w, r)
	if !ok {
		return
	}
	var req patchReq
	if !decodeBody(w, r, &req) {
		return
	}
	expect := ifMatchVersion(r)
	if req.Severity != nil && !domain.ValidSeverity(*req.Severity) {
		writeErr(w, r, domain.EValidation("invalid severity", nil))
		return
	}
	c, err := s.Store.MutateCase(r.Context(), op, id, expect, func(c *domain.Case) (store.Mutation, error) {
		var acts []domain.Activity
		var evs []events.Envelope
		var timers store.TimerPlan
		urn := events.CaseURN(op.Tenant, c.ID)
		if req.Description != nil {
			c.Description = *req.Description
		}
		if req.CustomFields != nil {
			if err := s.validateCustomFields(r.Context(), op.Tenant, c.WorkspaceID, "", req.CustomFields); err != nil {
				return store.Mutation{}, err
			}
			c.CustomFields = req.CustomFields
		}
		if req.Severity != nil && *req.Severity != c.Severity {
			old := c.Severity
			c.Severity = *req.Severity
			acts = append(acts, mkActivity(op, events.EvSeverityChanged, map[string]any{"severity": old}, map[string]any{"severity": c.Severity}))
			evs = append(evs, events.NewEnvelope(events.EvSeverityChanged, op, urn, map[string]any{"case_number": c.CaseNumber, "severity": c.Severity}))
		}
		if req.DueDate != nil {
			if req.DueDate.Before(time.Now()) && r.URL.Query().Get("allow_overdue") != "true" {
				return store.Mutation{}, domain.EValidation("due_date must be in the future (BR-12)", nil)
			}
			c.DueDate = *req.DueDate
			// due_date change resets SLA timers if the case is assigned (CASE-FR-013).
			if c.AssignedToID != nil {
				policy, _ := s.Store.GetSLAPolicy(r.Context(), op.Tenant, c.WorkspaceID)
				timers = store.TimerPlan{Set: []store.Timer{
					{Kind: "warn", FireAt: c.DueDate.Add(-policy.WarnBefore)},
					{Kind: "due", FireAt: c.DueDate},
				}}
			}
		}
		return store.Mutation{Activities: acts, Timers: timers, Events: evs}, nil
	})
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, caseView(c))
}

// handleSearchCases serves list/search/facets from OpenSearch (CASE-FR-042,
// AC-9). OpenSearch down → 503 SEARCH_UNAVAILABLE (BR-10, AC-14).
func (s *Server) handleSearchCases(w http.ResponseWriter, r *http.Request) {
	c := ClaimsFrom(r.Context())
	tenant, _ := c.Tenant()
	q := r.URL.Query()
	p := search.Params{
		Q:                   q.Get("q"),
		Statuses:            search.ExpandStatus(q.Get("filter[status]")),
		Severity:            q.Get("filter[severity]"),
		DispositionCategory: q.Get("filter[disposition_category]"),
		QueryURN:            q.Get("filter[query_urn]"),
		Due:                 q.Get("filter[due]"),
		Cursor:              q.Get("cursor"),
		Limit:               store.ClampLimit(atoiDefault(q.Get("limit"), 50)),
	}
	if a := q.Get("filter[assignee]"); a != "" {
		if a == "me" {
			p.AssigneeID = c.EffectiveUser()
		} else {
			p.AssigneeID = a
		}
	}
	if f := q.Get("facets"); f != "" {
		p.Facets = splitComma(f)
	}
	res, err := s.Search.Search(r.Context(), tenant, p)
	if err != nil {
		writeErr(w, r, domain.ESearchUnavailable())
		return
	}
	data := make([]any, 0, len(res.Docs))
	for _, d := range res.Docs {
		data = append(data, d)
	}
	writeJSON(w, http.StatusOK, PageEnvelope{
		Data:   data,
		Page:   PageInfo{NextCursor: res.NextCursor, HasMore: res.HasMore},
		Facets: res.Facets,
		Meta:   map[string]any{"projection_lag_ms": res.TookMS},
	})
}

// ---- helpers ----------------------------------------------------------------

func (s *Server) pathCase(w http.ResponseWriter, r *http.Request) (uuid.UUID, uuid.UUID, bool) {
	c := ClaimsFrom(r.Context())
	tenant, err := c.Tenant()
	if err != nil {
		writeErr(w, r, domain.EUnauthenticated("bad tenant claim"))
		return uuid.Nil, uuid.Nil, false
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		s.notFound(w, r)
		return uuid.Nil, uuid.Nil, false
	}
	return tenant, id, true
}

func caseView(c *domain.Case) map[string]any {
	return map[string]any{
		"id": c.ID, "workspace_id": c.WorkspaceID, "case_number": c.CaseNumber, "status": c.Status.String(),
		"severity": c.Severity, "assigned_to_id": c.AssignedToID, "assigned_to_at": c.AssignedToAt,
		"created_by_id": c.CreatedByID, "dataset_urn": c.DatasetURN, "dataset_version": c.DatasetVersion,
		"row_pk": c.RowPK, "dedup_key": derefStr(c.DedupKey), "display_projection": c.DisplayProjection,
		"projection_truncated": c.ProjectionTruncated, "source_query_urns": c.SourceQueryURNs, "dashboard_urn": c.DashboardURN,
		"due_date": c.DueDate, "description": c.Description, "custom_fields": c.CustomFields, "disposition_id": c.DispositionID,
		"resolution_note": c.ResolutionNote, "resolved_at": c.ResolvedAt, "closed_at": c.ClosedAt, "snapshot_ref": c.SnapshotRef,
		"recurrence_of": c.RecurrenceOf, "reassign_count": c.ReassignCount, "row_unavailable": c.RowUnavailable,
		"case_version": c.CaseVersion, "created_at": c.CreatedAt, "updated_at": c.UpdatedAt,
	}
}

func nonNilMap(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	return m
}

func derefStr(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
