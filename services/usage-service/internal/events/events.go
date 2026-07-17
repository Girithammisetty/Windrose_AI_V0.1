// Package events implements usage-service's event layer: the platform
// envelope (MASTER-FR-031), the outbound Publisher port with in-memory and
// real-Kafka adapters, the transactional-outbox relay (MASTER-FR-034), and the
// inbound metering-event consumer group (BRD 17 §6).
package events

import (
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/domain"
)

// EmitTopic is usage-service's own event topic (MASTER-FR-030): budget,
// anomaly, reconciliation and rate-card events land here. ai-gateway,
// notification-service and realtime-hub consume it.
const EmitTopic = "usage.events.v1"

// Consumed topics (BRD 17 §6, USG-FR-010).
const (
	TopicUsageMetering = "usage.metering.v1"
	TopicQueryEvents   = "query.events.v1"
	TopicPipeline      = "pipeline.events.v1"
	TopicAIToolInvoked = "ai.tool_invoked.v1"
	TopicAIAgentRun    = "ai.agent_run.v1"
	TopicAITokenUsage  = "ai.token_usage.v1" // ai-gateway LLM token metering

	IngestGroup = "usage-ingest"
)

// ConsumedTopics is the full inbound topic list for the ingest consumer group.
func ConsumedTopics() []string {
	return []string{
		TopicUsageMetering, TopicQueryEvents, TopicPipeline,
		TopicAIToolInvoked, TopicAIAgentRun, TopicAITokenUsage,
	}
}

// Emitted event types (BRD 17 §6, MASTER-FR-035 naming).
const (
	EvBudgetThreshold = "budget.threshold"
	EvBudgetExhausted = "budget.exhausted"
	EvBudgetReset     = "budget.reset"
	EvBudgetCreated   = "budget.created"
	EvBudgetUpdated   = "budget.updated"
	EvBudgetDeleted   = "budget.deleted"

	EvAnomalyDetected       = "usage.anomaly_detected"
	EvReconciliationVariance = "usage.reconciliation_variance"
	EvMonthRefinalized      = "usage.month_refinalized"
	EvRateCardActivated     = "ratecard.activated"
	EvAdjustmentRecorded    = "adjustment.recorded"

	EvCrossTenantDenied = "security.cross_tenant_denied" // MASTER-FR-003
	EvPermissionDenied  = "security.permission_denied"   // MASTER-FR-040
)

// Envelope is the platform event envelope (MASTER-FR-031); partition key is
// tenant_id.
type Envelope struct {
	EventID     uuid.UUID        `json:"event_id"`
	EventType   string           `json:"event_type"`
	TenantID    uuid.UUID        `json:"tenant_id"`
	Actor       domain.Actor     `json:"actor"`
	ViaAgent    *domain.ViaAgent `json:"via_agent"`
	ResourceURN string           `json:"resource_urn"`
	OccurredAt  time.Time        `json:"occurred_at"`
	TraceID     string           `json:"trace_id"`
	Payload     map[string]any   `json:"payload"`
}

// NewEnvelope builds an envelope with a fresh uuidv7 event id. Payloads carry
// resource references, never PII values (MASTER-FR-042).
func NewEnvelope(eventType string, op domain.Op, resourceURN string, payload map[string]any) Envelope {
	if payload == nil {
		payload = map[string]any{}
	}
	return Envelope{
		EventID:     domain.NewID(),
		EventType:   eventType,
		TenantID:    op.Tenant,
		Actor:       op.Actor,
		ViaAgent:    op.ViaAgent,
		ResourceURN: resourceURN,
		OccurredAt:  time.Now().UTC(),
		TraceID:     op.TraceID,
		Payload:     payload,
	}
}
