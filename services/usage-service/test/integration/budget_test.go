package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
)

// TestAC03_BudgetThresholdEmitsRealKafkaEvent is the budget-threshold→event
// proof: an 80% crossing emits exactly one budget.threshold on real Kafka
// (usage.events.v1, the ai-gateway feedback loop), and re-evaluation emits none
// (AC-3, BR-1). REAL: Kafka, Postgres.
func TestAC03_BudgetThresholdEmitsRealKafkaEvent(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()
	ws := "ws-7"

	b, err := h.st.CreateBudget(ctx, domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "admin"}},
		domain.Budget{WorkspaceID: &ws, MeterKey: domain.MeterLLMInputTokens,
			Window: domain.WindowCalendarMonth, LimitValue: 1_000_000, ActionAt100: domain.ActionAlertOnly})
	require.NoError(t, err)

	// Ingest 800,000 input tokens via real Kafka → 80% crossing.
	h.publish(t, events.TopicAITokenUsage, tenant, uuid.New(), time.Now().UTC(),
		h.tokenUsagePayload(ws, "u-1", "", "qwen2.5", 800_000, 0))
	require.Equal(t, 800_000.0, h.waitRawSum(t, tenant, domain.MeterLLMInputTokens, 800_000, 20*time.Second))

	// The pipeline evaluated the budget on ingest; the relay ships the event.
	evs := h.consumeUsageEvents(t, tenant, events.EvBudgetThreshold, 1, 20*time.Second)
	require.Len(t, evs, 1, "exactly one budget.threshold on real Kafka")
	require.EqualValues(t, 80, evs[0].Payload["threshold"])
	require.Equal(t, b.ID.String(), evs[0].Payload["budget_id"])

	// Re-evaluation is idempotent: no new threshold-80 event.
	require.NoError(t, h.st.EvaluateAll(ctx, tenant))
	_, st, err := h.st.GetBudgetState(ctx, tenant, b.ID)
	require.NoError(t, err)
	require.Equal(t, 80, st.LastThreshold)
	require.InDelta(t, 800_000, st.Consumed, 1)
}

// TestAC04_HardStopExhausted: a hard-stop budget hitting 100% records
// exhausted_at and emits budget.exhausted with action=hard_stop (AC-4). REAL:
// Kafka, Postgres.
func TestAC04_HardStopExhausted(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenant := uuid.New()

	b, err := h.st.CreateBudget(ctx, domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "admin"}},
		domain.Budget{MeterKey: domain.MeterLLMInputTokens, Window: domain.WindowCalendarMonth,
			LimitValue: 1000, ActionAt100: domain.ActionHardStop})
	require.NoError(t, err)

	h.publish(t, events.TopicAITokenUsage, tenant, uuid.New(), time.Now().UTC(),
		h.tokenUsagePayload("", "u-1", "", "qwen2.5", 1500, 0))
	require.Equal(t, 1500.0, h.waitRawSum(t, tenant, domain.MeterLLMInputTokens, 1500, 20*time.Second))

	evs := h.consumeUsageEvents(t, tenant, events.EvBudgetExhausted, 1, 20*time.Second)
	require.GreaterOrEqual(t, len(evs), 1)
	require.Equal(t, domain.ActionHardStop, evs[0].Payload["action"])

	_, st, err := h.st.GetBudgetState(ctx, tenant, b.ID)
	require.NoError(t, err)
	require.Equal(t, 100, st.LastThreshold)
	require.NotNil(t, st.ExhaustedAt)
}
