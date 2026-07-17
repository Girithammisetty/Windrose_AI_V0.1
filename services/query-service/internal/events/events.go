// Package events implements the platform event envelope (MASTER-FR-031),
// the EventPublisher port with in-memory and Kafka adapters, the
// transactional-outbox relay (MASTER-FR-034) and inbound event handlers
// (BRD 05 §6: dataset.deleted, dataset.version_created, tenant.suspended).
package events

import (
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/domain"
)

// Topic is query-service's event topic (MASTER-FR-030).
const Topic = "query.events.v1"

// Envelope is the platform event envelope (MASTER-FR-031); partition key is
// tenant_id.
type Envelope struct {
	EventID     uuid.UUID        `json:"event_id"` // uuidv7
	EventType   string           `json:"event_type"`
	TenantID    uuid.UUID        `json:"tenant_id"`
	Actor       domain.Actor     `json:"actor"`
	ViaAgent    *domain.ViaAgent `json:"via_agent"`
	ResourceURN string           `json:"resource_urn"`
	OccurredAt  time.Time        `json:"occurred_at"`
	TraceID     string           `json:"trace_id"`
	Payload     map[string]any   `json:"payload"`
}

// NewEnvelope builds an envelope with a fresh uuidv7 event id. Payloads
// carry resource references, never PII values (MASTER-FR-042).
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

// Emitted event types (BRD 05 §6, MASTER-FR-035 naming).
const (
	EvQuerySaved   = "query.saved"
	EvQueryUpdated = "query.updated"
	EvQueryDeleted = "query.deleted"

	EvExecutionStarted         = "execution.started"
	EvExecutionSucceeded       = "execution.succeeded"
	EvExecutionFailed          = "execution.failed"
	EvExecutionCancelled       = "execution.cancelled"
	EvExecutionCeilingExceeded = "execution.ceiling_exceeded"

	EvCrossTenantDenied = "security.cross_tenant_denied" // MASTER-FR-003
	EvPermissionDenied  = "security.permission_denied"   // MASTER-FR-040
)

// URN builders (MASTER-FR-013).
func QueryURN(tenant, id uuid.UUID) string {
	return "wr:" + tenant.String() + ":query:saved_query/" + id.String()
}

func ExecutionURN(tenant, id uuid.UUID) string {
	return "wr:" + tenant.String() + ":query:execution/" + id.String()
}
