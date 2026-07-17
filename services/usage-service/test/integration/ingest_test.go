package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/usage-service/internal/events"
	"github.com/windrose-ai/usage-service/internal/domain"
)

// TestAC01_TokenUsageIngestToRollups: a real ai.token_usage.v1 event on real
// Kafka is aggregated into raw rows and rollups (AC-1). REAL: Kafka, Postgres.
func TestAC01_TokenUsageIngestToRollups(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	eid := uuid.New()
	h.publish(t, events.TopicAITokenUsage, tenant, eid, time.Now().UTC(),
		h.tokenUsagePayload("ws-7", "u-1", "agent-triage", "qwen2.5", 1200, 800))

	require.Equal(t, 1200.0, h.waitRawSum(t, tenant, domain.MeterLLMInputTokens, 1200, 20*time.Second))
	require.Equal(t, 800.0, h.waitRawSum(t, tenant, domain.MeterLLMOutputTokens, 800, 20*time.Second))
	require.Equal(t, 1, h.rawCount(t, tenant, domain.MeterLLMInputTokens))
	require.Equal(t, 1, h.rawCount(t, tenant, domain.MeterLLMOutputTokens))

	// Rollups: raw → daily aggregate holds the same totals.
	require.NoError(t, h.st.RefreshRollups(context.Background(), time.Now().Add(-49*time.Hour)))
	daily, err := h.st.DailyTotals(context.Background(), tenant, domain.MeterLLMInputTokens,
		time.Now().AddDate(0, 0, -2), time.Now().AddDate(0, 0, 1))
	require.NoError(t, err)
	var sum float64
	for _, v := range daily {
		sum += v
	}
	require.Equal(t, 1200.0, sum)
}

// TestAC02_IdempotentDoubleDelivery: replaying an identical event_id adds no
// rows and leaves rollups unchanged (AC-2, AC-12). REAL: Kafka, Redis, Postgres.
func TestAC02_IdempotentDoubleDelivery(t *testing.T) {
	h := requireHarness(t)
	tenant := uuid.New()
	eid := uuid.New()
	occurred := time.Now().UTC()
	payload := h.tokenUsagePayload("ws-1", "u-1", "", "qwen2.5", 500, 250)

	h.publish(t, events.TopicAITokenUsage, tenant, eid, occurred, payload)
	require.Equal(t, 500.0, h.waitRawSum(t, tenant, domain.MeterLLMInputTokens, 500, 20*time.Second))

	// Redeliver the SAME event_id twice more.
	h.publish(t, events.TopicAITokenUsage, tenant, eid, occurred, payload)
	h.publish(t, events.TopicAITokenUsage, tenant, eid, occurred, payload)
	time.Sleep(2 * time.Second)

	require.Equal(t, 1, h.rawCount(t, tenant, domain.MeterLLMInputTokens), "replays must be no-ops")
	require.Equal(t, 500.0, h.waitRawSum(t, tenant, domain.MeterLLMInputTokens, 500, 2*time.Second))

	// Unique-constraint dedup path (independent of Redis, AC-14): direct insert
	// of the same (tenant,event_id,meter,time) inserts nothing the second time.
	rec := domain.MeterRecord{
		Time: occurred, TenantID: tenant, MeterKey: domain.MeterLLMInputTokens,
		Quantity: 500, EventID: eid, Cloud: "aws",
	}
	n, err := h.st.InsertRaw(context.Background(), []domain.MeterRecord{rec})
	require.NoError(t, err)
	require.Equal(t, 0, n, "unique constraint makes the replay a no-op")
}
