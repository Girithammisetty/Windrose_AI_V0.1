package integration

import (
	"context"
	"encoding/csv"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/store"
)

func ptr(s string) *string { return &s }

func store_ShowbackQuery(groupBy string, now time.Time) store.ShowbackQuery {
	return store.ShowbackQuery{
		GroupBy: []string{groupBy},
		From:    now.AddDate(0, 0, -1),
		To:      now.AddDate(0, 0, 1),
		Limit:   200,
	}
}

// TestAC06_ShowbackCSVMatchesJSON: the streamed RFC-4180 CSV totals equal the
// JSON report totals (AC-6). REAL: Postgres.
func TestAC06_ShowbackCSVMatchesJSON(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()
	now := time.Now().UTC()

	recs := []domain.MeterRecord{
		{Time: now, TenantID: tenant, MeterKey: domain.MeterLLMInputTokens, Quantity: 1000, WorkspaceID: ptr("ws-a"), EventID: uuid.New(), Cloud: "aws"},
		{Time: now, TenantID: tenant, MeterKey: domain.MeterLLMInputTokens, Quantity: 2000, WorkspaceID: ptr("ws-b"), EventID: uuid.New(), Cloud: "aws"},
		{Time: now, TenantID: tenant, MeterKey: domain.MeterLLMOutputTokens, Quantity: 500, WorkspaceID: ptr("ws-a"), EventID: uuid.New(), Cloud: "aws"},
	}
	_, err := h.st.InsertRaw(ctx, recs)
	require.NoError(t, err)
	require.NoError(t, h.st.RefreshRollups(ctx, now.Add(-49*time.Hour)))

	tok := h.token(t, tenant, "user", "u", nil)
	from := now.AddDate(0, 0, -1).Format("2006-01-02")
	to := now.AddDate(0, 0, 1).Format("2006-01-02")
	path := "/api/v1/reports/usage?group_by=workspace,meter&from=" + from + "&to=" + to

	// JSON total.
	rj := h.do(t, "GET", path, tok, nil, nil)
	require.Equal(t, 200, rj.status)
	data, _ := rj.body["data"].([]any)
	var jsonTotal float64
	for _, row := range data {
		m := row.(map[string]any)
		jsonTotal += m["quantity"].(float64)
	}
	require.Equal(t, 3500.0, jsonTotal)

	// CSV total.
	rc := h.do(t, "GET", path, tok, nil, map[string]string{"Accept": "text/csv"})
	require.Equal(t, 200, rc.status)
	require.Contains(t, rc.header.Get("Content-Type"), "text/csv")
	reader := csv.NewReader(strings.NewReader(string(rc.raw)))
	rows, err := reader.ReadAll()
	require.NoError(t, err)
	require.Equal(t, []string{"workspace", "meter", "meter_key", "unit", "quantity", "usd"}, rows[0])
	var csvTotal float64
	for _, row := range rows[1:] {
		q, _ := strconv.ParseFloat(row[4], 64)
		csvTotal += q
	}
	require.Equal(t, jsonTotal, csvTotal, "CSV totals equal JSON totals")
}

// TestAC13_AgentAttributionReconciles: agent-OBO usage carries both user_id and
// agent_id; group_by=user and group_by=agent both include it and grand totals
// match (AC-13, BR-11). REAL: Postgres.
func TestAC13_AgentAttributionReconciles(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()
	now := time.Now().UTC()

	rec := domain.MeterRecord{Time: now, TenantID: tenant, MeterKey: domain.MeterLLMInputTokens,
		Quantity: 10000, UserID: ptr("u-77"), AgentID: ptr("triage-copilot"), EventID: uuid.New(), Cloud: "aws"}
	_, err := h.st.InsertRaw(ctx, []domain.MeterRecord{rec})
	require.NoError(t, err)
	require.NoError(t, h.st.RefreshRollups(ctx, now.Add(-49*time.Hour)))

	byUser, err := h.st.QueryUsage(ctx, tenant, store_ShowbackQuery("user", now))
	require.NoError(t, err)
	byAgent, err := h.st.QueryUsage(ctx, tenant, store_ShowbackQuery("agent", now))
	require.NoError(t, err)

	require.Equal(t, sumRows(byUser), sumRows(byAgent))
	require.Equal(t, 10000.0, sumRows(byUser))
}

func sumRows(rows []domain.RollupRow) float64 {
	var s float64
	for _, r := range rows {
		s += r.Quantity
	}
	return s
}
