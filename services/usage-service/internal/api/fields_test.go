package api

// Field-drift regression tests (bff-graphql contract):
//   - report rows must serialize the dollar figure as `cost_usd` (bff's
//     UsageRowDTO), with the legacy `usd` kept for back-compat;
//   - budget state rows must carry a `scope` string ("<kind>/<id>") and
//     GET /budget-states must honor the ?scope= filter the bff sends.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
	"github.com/windrose-ai/usage-service/internal/store"
)

func TestRollupRowEmitsCostUSDAndLegacyUSD(t *testing.T) {
	usd := 12.5
	b, err := json.Marshal(domain.RollupRow{MeterKey: "api_calls", Unit: "count", Quantity: 3, USD: &usd})
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	if m["cost_usd"] != 12.5 {
		t.Fatalf("cost_usd missing/wrong: %s", string(b))
	}
	if m["usd"] != 12.5 {
		t.Fatalf("legacy usd dropped: %s", string(b))
	}

	// No price resolved: neither key appears.
	b, _ = json.Marshal(domain.RollupRow{MeterKey: "api_calls", Quantity: 3})
	m = map[string]any{} // fresh map: Unmarshal merges into existing keys
	_ = json.Unmarshal(b, &m)
	if _, ok := m["cost_usd"]; ok {
		t.Fatalf("cost_usd must be omitted when unpriced: %s", string(b))
	}
	if _, ok := m["usd"]; ok {
		t.Fatalf("usd must be omitted when unpriced: %s", string(b))
	}
}

// budgetStore stubs the api.Store surface handleBudgetStates touches.
type budgetStore struct {
	Store // embed the interface: untouched methods panic if called
	budgets []domain.Budget
}

func (s *budgetStore) ListBudgets(_ context.Context, _ uuid.UUID, _ uuid.UUID, _ int) ([]domain.Budget, error) {
	return s.budgets, nil
}

func (s *budgetStore) GetBudgetState(_ context.Context, _ uuid.UUID, id uuid.UUID) (domain.Budget, domain.BudgetState, error) {
	for _, b := range s.budgets {
		if b.ID == id {
			return b, domain.BudgetState{BudgetID: id, WindowStart: time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC), Consumed: 4}, nil
		}
	}
	return domain.Budget{}, domain.BudgetState{}, domain.ErrNotFound
}

func strp(s string) *string { return &s }

func TestBudgetStatesScopeFieldAndFilter(t *testing.T) {
	tenant := uuid.New()
	mk := func(mut func(*domain.Budget)) domain.Budget {
		b := domain.Budget{
			ID: domain.NewID(), TenantID: tenant, MeterKey: "usd_total",
			Window: domain.WindowCalendarMonth, LimitValue: 100,
			ActionAt100: domain.ActionAlertOnly, Status: domain.BudgetActive,
		}
		mut(&b)
		return b
	}
	wsBudget := mk(func(b *domain.Budget) { b.WorkspaceID = strp("ws-1") })
	userBudget := mk(func(b *domain.Budget) { b.UserID = strp("u-1") })
	agentBudget := mk(func(b *domain.Budget) { b.WorkspaceID = strp("ws-1"); b.AgentID = strp("ag-1") })
	tenantBudget := mk(func(*domain.Budget) {})
	deleted := mk(func(b *domain.Budget) { b.WorkspaceID = strp("ws-1"); b.Status = domain.BudgetDeleted })

	srv := &Server{Store: &budgetStore{budgets: []domain.Budget{wsBudget, userBudget, agentBudget, tenantBudget, deleted}}}

	call := func(query string) []map[string]any {
		t.Helper()
		req := httptest.NewRequest(http.MethodGet, "/api/v1/budget-states"+query, nil)
		req = req.WithContext(context.WithValue(req.Context(), ctxKeyClaims,
			&Claims{Sub: "u-1", TenantID: tenant.String(), Typ: "user"}))
		w := httptest.NewRecorder()
		srv.handleBudgetStates(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("budget-states%s: status %d body %s", query, w.Code, w.Body.String())
		}
		var body struct {
			Data []map[string]any `json:"data"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("decode: %v (%s)", err, w.Body.String())
		}
		return body.Data
	}

	// Unfiltered: 4 active rows, each with the canonical scope string
	// (agent > user > workspace > tenant precedence).
	rows := call("")
	if len(rows) != 4 {
		t.Fatalf("want 4 active rows, got %d", len(rows))
	}
	want := map[string]string{
		wsBudget.ID.String():     "workspace/ws-1",
		userBudget.ID.String():   "user/u-1",
		agentBudget.ID.String():  "agent/ag-1",
		tenantBudget.ID.String(): "tenant/" + tenant.String(),
	}
	for _, row := range rows {
		id := row["budget_id"].(string)
		if row["scope"] != want[id] {
			t.Fatalf("budget %s scope=%v want %s", id, row["scope"], want[id])
		}
	}

	// scope filter narrows to the matching budget only (bff workspaceCostPanel).
	rows = call("?scope=workspace/ws-1")
	if len(rows) != 1 || rows[0]["budget_id"] != wsBudget.ID.String() {
		t.Fatalf("scope=workspace/ws-1: got %v", rows)
	}
	if rows = call("?scope=tenant/" + tenant.String()); len(rows) != 1 || rows[0]["budget_id"] != tenantBudget.ID.String() {
		t.Fatalf("tenant scope filter: got %v", rows)
	}
	if rows = call("?scope=workspace/other"); len(rows) != 0 {
		t.Fatalf("non-matching scope: want 0 rows, got %v", rows)
	}
}

// Compile-time interface checks the embedded-store trick relies on.
var (
	_ = events.Envelope{}
	_ = store.ShowbackQuery{}
)
