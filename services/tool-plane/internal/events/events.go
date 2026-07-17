// Package events implements the tool-plane event contracts (BRD §6): the
// dedicated ai.tool_invoked.v1 audit stream (one per enforcement attempt) and
// the tool.events.v1 lifecycle stream. Both ride the platform master envelope
// (MASTER-FR-031) and are emitted via the transactional outbox (MASTER-FR-034)
// drained to REAL Kafka (Redpanda) by the shared go-common producer.
package events

import (
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/tool-plane/internal/domain"
)

// Topics (MASTER-FR-030).
const (
	TopicToolInvoked = "ai.tool_invoked.v1" // dedicated audit topic (BRD §6)
	TopicToolEvents  = "tool.events.v1"     // lifecycle events
)

// tool.events.v1 event types (BRD §6, MASTER-FR-035 naming).
const (
	EvToolRegistered       = "tool.registered"
	EvToolVersionPublished = "tool.version_published"
	EvToolDeprecated       = "tool.deprecated"
	EvToolRetired          = "tool.retired"
	EvToolQuarantined      = "tool.quarantined"
	EvToolKilled           = "tool.killed"
	EvToolUnkilled         = "tool.unkilled"
	EvToolSLABreached      = "tool.sla_breached"
	EvTenantToolEnabled    = "tenant_tool.enabled"
	EvTenantToolDisabled   = "tenant_tool.disabled"
	EvBYOSubmitted         = "byo.submitted"
	EvBYOApproved          = "byo.approved"
	EvBYORejected          = "byo.rejected"

	EvCrossTenantDenied = "security.cross_tenant_denied" // MASTER-FR-003
	EvToolInvoked       = "ai.tool_invoked"              // ai.tool_invoked.v1 payload marker
)

// Decision values for ai.tool_invoked.v1 (BRD §6).
const (
	DecisionAllowed      = "allowed"
	DecisionDeniedPolicy = "denied_policy"
	DecisionDeniedRate   = "denied_rate"
	DecisionDeniedSchema = "denied_schema"
	DecisionKilled       = "killed"
	DecisionStubbed      = "stubbed" // eval-mode (BR-16)
	DecisionProposal     = "proposal_required"
)

// Envelope is the platform event envelope (MASTER-FR-031); partition key is
// tenant_id.
type Envelope struct {
	EventID     uuid.UUID         `json:"event_id"`
	EventType   string            `json:"event_type"`
	TenantID    uuid.UUID         `json:"tenant_id"`
	Actor       domain.Actor      `json:"actor"`
	ViaAgent    *domain.ViaAgent  `json:"via_agent"`
	ResourceURN string            `json:"resource_urn"`
	OccurredAt  time.Time         `json:"occurred_at"`
	TraceID     string            `json:"trace_id"`
	Topic       string            `json:"-"` // routing hint for the relay
	Payload     map[string]any    `json:"payload"`
}

// NewEnvelope builds an envelope with a fresh uuidv7 id. Payloads carry
// resource references and digests, never PII values (MASTER-FR-042).
func NewEnvelope(topic, eventType string, tenant uuid.UUID, actor domain.Actor, via *domain.ViaAgent, resourceURN, traceID string, payload map[string]any) Envelope {
	if payload == nil {
		payload = map[string]any{}
	}
	return Envelope{
		EventID:     domain.NewID(),
		EventType:   eventType,
		TenantID:    tenant,
		Actor:       actor,
		ViaAgent:    via,
		ResourceURN: resourceURN,
		OccurredAt:  time.Now().UTC(),
		TraceID:     traceID,
		Topic:       topic,
		Payload:     payload,
	}
}

// OutboxRow is one unpublished outbox entry (relay surface).
type OutboxRow struct {
	ID       int64
	Topic    string
	Envelope Envelope
}
