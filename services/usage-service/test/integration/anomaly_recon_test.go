package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
	"github.com/windrose-ai/usage-service/internal/recon"
)

// TestAC08_AnomalyZScore: 28 days of history (mean 100, stddev 5) then a 130
// day (z=6) records a usage.anomaly_detected event and an open anomaly (AC-8).
// REAL: Postgres, Kafka.
func TestAC08_AnomalyZScore(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()
	meter := domain.MeterAPICalls
	today := time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC)

	// 28 days of ~100 (alternating 95/105 → mean 100, stddev 5).
	var recs []domain.MeterRecord
	for i := 1; i <= 28; i++ {
		q := 105.0
		if i%2 == 0 {
			q = 95.0
		}
		recs = append(recs, domain.MeterRecord{
			Time: today.AddDate(0, 0, -i).Add(12 * time.Hour), TenantID: tenant, MeterKey: meter,
			Quantity: q, EventID: uuid.New(), Cloud: "aws",
		})
	}
	// Observed day = 130.
	recs = append(recs, domain.MeterRecord{Time: today.Add(12 * time.Hour), TenantID: tenant, MeterKey: meter,
		Quantity: 130, EventID: uuid.New(), Cloud: "aws"})
	_, err := h.st.InsertRaw(ctx, recs)
	require.NoError(t, err)
	require.NoError(t, h.st.RefreshRollups(ctx, today.AddDate(0, 0, -30)))

	n, err := h.runner.AnomalyScan(ctx, today)
	require.NoError(t, err)
	require.GreaterOrEqual(t, n, 1)

	anoms, err := h.st.ListAnomalies(ctx, tenant, domain.AnomalyOpen, 10)
	require.NoError(t, err)
	require.GreaterOrEqual(t, len(anoms), 1)
	require.Equal(t, meter, anoms[0].MeterKey)
	require.Greater(t, anoms[0].Z, 3.0)

	evs := h.consumeUsageEvents(t, tenant, events.EvAnomalyDetected, 1, 15*time.Second)
	require.GreaterOrEqual(t, len(evs), 1)
}

// TestAC09_ReconVarianceBlocksChargeback: an 8% LLM-token overbill marks the
// month variance, emits usage.reconciliation_variance, and blocks chargeback
// with CONFLICT until acknowledged (AC-9). REAL: Postgres, Kafka.
func TestAC09_ReconVarianceBlocksChargeback(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()
	month := "2026-05"
	monthStart := time.Date(2026, 5, 15, 12, 0, 0, 0, time.UTC)

	rec := domain.MeterRecord{Time: monthStart, TenantID: tenant, MeterKey: domain.MeterLLMInputTokens,
		Quantity: 1_000_000, WorkspaceID: ptr("ws-1"), EventID: uuid.New(), Cloud: "aws"}
	_, err := h.st.InsertRaw(ctx, []domain.MeterRecord{rec})
	require.NoError(t, err)
	require.NoError(t, h.st.RefreshRollups(ctx, monthStart.AddDate(0, 0, -1)))
	require.NoError(t, h.st.FinalizeMonth(ctx, month))

	// A default rate card so chargeback prices the tokens.
	op := domain.Op{Platform: true, Actor: domain.Actor{Type: "service", ID: "ops"}}
	card, err := h.st.CreateRateCard(ctx, op, domain.RateCard{Version: 1,
		EffectiveFrom: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		Items:         map[string]float64{domain.MeterLLMInputTokens: 0.000002}})
	require.NoError(t, err)
	_, err = h.st.ActivateRateCard(ctx, op, card.ID)
	require.NoError(t, err)

	metered, err := h.st.MeteredMonthly(ctx, month)
	require.NoError(t, err)
	billed := map[string]float64{domain.MeterLLMInputTokens: metered[domain.MeterLLMInputTokens] * 1.08}
	lines, blocking := recon.Compute(metered, billed)
	require.True(t, blocking, "8%% LLM variance exceeds the 5%% threshold")

	var vm []map[string]any
	for _, l := range lines {
		if l.Blocking {
			vm = append(vm, map[string]any{"meter_key": l.MeterKey, "metered": l.Metered, "billed": l.Billed, "variance_pct": l.VariancePct})
		}
	}
	r, err := h.st.UpsertReconciliation(ctx, domain.Reconciliation{Month: month, Provider: "openai", Status: domain.ReconVariance}, map[string]any{"lines": lines}, vm)
	require.NoError(t, err)

	evs := h.consumeUsageEvents(t, uuid.Nil, events.EvReconciliationVariance, 1, 15*time.Second)
	require.GreaterOrEqual(t, len(evs), 1)

	// Chargeback blocked while variance is unresolved.
	tok := h.token(t, tenant, "user", "u", nil)
	rb := h.do(t, "GET", "/api/v1/reports/chargeback?month="+month, tok, nil, nil)
	require.Equal(t, 409, rb.status)
	require.Equal(t, "CONFLICT", errCode(rb))

	// Acknowledge → chargeback unblocked and priced with the override card.
	require.NoError(t, h.st.AcknowledgeReconciliation(ctx, r.ID))
	ra := h.do(t, "GET", "/api/v1/reports/chargeback?month="+month, tok, nil, nil)
	require.Equal(t, 200, ra.status)
	data, _ := ra.body["data"].([]any)
	require.GreaterOrEqual(t, len(data), 1)
	line := data[0].(map[string]any)
	require.InDelta(t, 2.0, line["usd"].(float64), 0.001) // 1,000,000 * 0.000002
}
