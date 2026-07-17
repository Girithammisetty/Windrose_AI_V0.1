package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/domain"
)

type scopeBody struct {
	WorkspaceID *string `json:"workspace_id,omitempty"`
	UserID      *string `json:"user_id,omitempty"`
	AgentID     *string `json:"agent_id,omitempty"`
}

type createBudgetBody struct {
	Scope       scopeBody `json:"scope"`
	MeterKey    string    `json:"meter_key"`
	Window      string    `json:"window"`
	LimitValue  float64   `json:"limit_value"`
	ActionAt100 string    `json:"action_at_100"`
}

func budgetView(b domain.Budget) map[string]any {
	scope := map[string]any{"tenant_id": b.TenantID.String()}
	if b.WorkspaceID != nil {
		scope["workspace_id"] = *b.WorkspaceID
	}
	if b.UserID != nil {
		scope["user_id"] = *b.UserID
	}
	if b.AgentID != nil {
		scope["agent_id"] = *b.AgentID
	}
	return map[string]any{
		"id": b.ID.String(), "scope": scope, "meter_key": b.MeterKey,
		"window": b.Window, "limit_value": b.LimitValue,
		"thresholds": domain.Thresholds, "action_at_100": b.ActionAt100,
		"status": b.Status, "created_at": b.CreatedAt.Format(time.RFC3339),
		"updated_at": b.UpdatedAt.Format(time.RFC3339),
	}
}

func validWindow(w string) bool {
	return w == domain.WindowCalendarMonth || w == domain.WindowCalendarDay || w == domain.WindowRolling7d
}

func validMeter(mk string) bool {
	if mk == "usd_total" {
		return true
	}
	_, ok := domain.CatalogKeys()[mk]
	return ok
}

// handleCreateBudget creates a budget (USG-FR-030) with Idempotency-Key replay.
func (s *Server) handleCreateBudget(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	key := r.Header.Get("Idempotency-Key")
	if rec, err := s.Store.GetIdempotent(r.Context(), op.Tenant, key); err == nil && rec != nil {
		w.Header().Set("Idempotency-Replayed", "true")
		writeJSON(w, rec.Status, json.RawMessage(rec.Response))
		return
	}

	var body createBudgetBody
	if !decodeBody(w, r, &body) {
		return
	}
	ve := &domain.ValidationError{}
	if !validMeter(body.MeterKey) {
		ve.Fields = append(ve.Fields, domain.FieldError{Field: "meter_key", Message: "unknown meter"})
	}
	if body.LimitValue <= 0 {
		ve.Fields = append(ve.Fields, domain.FieldError{Field: "limit_value", Message: "must be > 0"})
	}
	if !validWindow(body.Window) {
		ve.Fields = append(ve.Fields, domain.FieldError{Field: "window", Message: "invalid window"})
	}
	if body.ActionAt100 == "" {
		body.ActionAt100 = domain.ActionAlertOnly
	}
	if body.ActionAt100 != domain.ActionAlertOnly && body.ActionAt100 != domain.ActionHardStop {
		ve.Fields = append(ve.Fields, domain.FieldError{Field: "action_at_100", Message: "invalid action"})
	}
	if len(ve.Fields) > 0 {
		writeErr(w, r, ve)
		return
	}

	b := domain.Budget{
		WorkspaceID: body.Scope.WorkspaceID, UserID: body.Scope.UserID, AgentID: body.Scope.AgentID,
		MeterKey: body.MeterKey, Window: body.Window, LimitValue: body.LimitValue, ActionAt100: body.ActionAt100,
	}
	created, err := s.Store.CreateBudget(r.Context(), op, b)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	view := budgetView(created)
	var buf bytes.Buffer
	_ = json.NewEncoder(&buf).Encode(DataBody{Data: view})
	_ = s.Store.PutIdempotent(r.Context(), op.Tenant, key, r.Method, r.URL.Path, http.StatusCreated, buf.Bytes())
	writeData(w, http.StatusCreated, view)
}

func (s *Server) handleListBudgets(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	after := uuid.Nil
	if c := r.URL.Query().Get("cursor"); c != "" {
		if id, err := uuid.Parse(c); err == nil {
			after = id
		}
	}
	limit := 50
	budgets, err := s.Store.ListBudgets(r.Context(), op.Tenant, after, limit)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	views := make([]map[string]any, len(budgets))
	for i, b := range budgets {
		views[i] = budgetView(b)
	}
	next := ""
	hasMore := len(budgets) == limit
	if hasMore {
		next = budgets[len(budgets)-1].ID.String()
	}
	writePage(w, views, next, hasMore)
}

func (s *Server) handleGetBudget(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "budget not found", nil)
		return
	}
	b, err := s.Store.GetBudget(r.Context(), op.Tenant, id)
	if err != nil {
		s.budgetLookupErr(w, r, id, err)
		return
	}
	writeData(w, http.StatusOK, budgetView(b))
}

func (s *Server) handlePatchBudget(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "budget not found", nil)
		return
	}
	var body struct {
		LimitValue  *float64 `json:"limit_value"`
		ActionAt100 *string  `json:"action_at_100"`
	}
	if !decodeBody(w, r, &body) {
		return
	}
	if body.LimitValue != nil && *body.LimitValue <= 0 {
		ve := &domain.ValidationError{Fields: []domain.FieldError{{Field: "limit_value", Message: "must be > 0"}}}
		writeErr(w, r, ve)
		return
	}
	b, err := s.Store.UpdateBudget(r.Context(), op, id, body.LimitValue, body.ActionAt100)
	if err != nil {
		s.budgetLookupErr(w, r, id, err)
		return
	}
	writeData(w, http.StatusOK, budgetView(b))
}

func (s *Server) handleDeleteBudget(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "budget not found", nil)
		return
	}
	if err := s.Store.DeleteBudget(r.Context(), op, id); err != nil {
		s.budgetLookupErr(w, r, id, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// handleBudgetState returns the current window state (gateway resync,
// USG-FR-032).
func (s *Server) handleBudgetState(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "budget not found", nil)
		return
	}
	b, st, err := s.Store.GetBudgetState(r.Context(), op.Tenant, id)
	if err != nil {
		s.budgetLookupErr(w, r, id, err)
		return
	}
	writeData(w, http.StatusOK, budgetStateView(b, st))
}

// handleBudgetStates is the bulk resync endpoint (USG-FR-032). An optional
// ?scope=<kind>/<id> filter (e.g. workspace/<uuid>, tenant/<uuid>) narrows the
// rows — bff-graphql's workspaceCostPanel sends scope=workspace/{id}.
func (s *Server) handleBudgetStates(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	scopeFilter := r.URL.Query().Get("scope")
	budgets, err := s.Store.ListBudgets(r.Context(), op.Tenant, uuid.Nil, 200)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	var out []map[string]any
	for _, b := range budgets {
		if b.Status != domain.BudgetActive {
			continue
		}
		if scopeFilter != "" && budgetScope(b) != scopeFilter {
			continue
		}
		bb, st, err := s.Store.GetBudgetState(r.Context(), op.Tenant, b.ID)
		if err != nil {
			continue
		}
		out = append(out, budgetStateView(bb, st))
	}
	writePage(w, out, "", false)
}

// budgetScope renders a budget's scope as the canonical "<kind>/<id>" string
// (most specific dimension wins: agent > user > workspace > tenant) — the
// format bff-graphql keys its budgetStateByScope loader by.
func budgetScope(b domain.Budget) string {
	switch {
	case b.AgentID != nil && *b.AgentID != "":
		return "agent/" + *b.AgentID
	case b.UserID != nil && *b.UserID != "":
		return "user/" + *b.UserID
	case b.WorkspaceID != nil && *b.WorkspaceID != "":
		return "workspace/" + *b.WorkspaceID
	default:
		return "tenant/" + b.TenantID.String()
	}
}

func budgetStateView(b domain.Budget, st domain.BudgetState) map[string]any {
	v := map[string]any{
		"budget_id":      b.ID.String(),
		"scope":          budgetScope(b),
		"window_start":   st.WindowStart.Format("2006-01-02"),
		"consumed":       st.Consumed,
		"limit":          b.LimitValue,
		"last_threshold": st.LastThreshold,
		"action":         b.ActionAt100,
	}
	if st.ExhaustedAt != nil {
		v["exhausted_at"] = st.ExhaustedAt.Format(time.RFC3339)
	}
	return v
}

// budgetLookupErr maps ErrNotFound to an audited cross-tenant 404
// (MASTER-FR-003, AC-10).
func (s *Server) budgetLookupErr(w http.ResponseWriter, r *http.Request, id uuid.UUID, err error) {
	op, _ := opFrom(r)
	if err == domain.ErrNotFound {
		s.auditCrossTenant(r, domain.BudgetURN(op.Tenant, id))
		writeErrCode(w, r, http.StatusNotFound, "NOT_FOUND", "budget not found", nil)
		return
	}
	writeErr(w, r, err)
}
