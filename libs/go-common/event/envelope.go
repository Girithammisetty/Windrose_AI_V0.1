// Package event defines the platform master event envelope (MASTER-FR-031)
// shared by every Windrose service. It is the single wire contract carried by
// the kafka producer/consumer and written to each service's transactional
// outbox. Partition key is always tenant_id.
package event

import (
	"time"

	"github.com/google/uuid"
)

// Actor identifies who caused an event (MASTER-FR-031/041).
type Actor struct {
	Type string `json:"type"` // user | service | agent | platform
	ID   string `json:"id"`
}

// ViaAgent carries dual attribution for OBO actions (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Envelope is the platform event envelope (MASTER-FR-031). Every state change
// emits one of these to the owning context's `<ctx>.events.v1` topic.
type Envelope struct {
	EventID     uuid.UUID      `json:"event_id"` // uuidv7
	EventType   string         `json:"event_type"`
	TenantID    uuid.UUID      `json:"tenant_id"`
	Actor       Actor          `json:"actor"`
	ViaAgent    *ViaAgent      `json:"via_agent"`
	ResourceURN string         `json:"resource_urn"`
	OccurredAt  time.Time      `json:"occurred_at"`
	TraceID     string         `json:"trace_id"`
	Payload     map[string]any `json:"payload"`
}

// New builds an envelope with a fresh uuidv7 event id.
func New(eventType string, tenant uuid.UUID, actor Actor, resourceURN, traceID string, payload map[string]any) Envelope {
	id, err := uuid.NewV7()
	if err != nil {
		id = uuid.New()
	}
	if payload == nil {
		payload = map[string]any{}
	}
	return Envelope{
		EventID:     id,
		EventType:   eventType,
		TenantID:    tenant,
		Actor:       actor,
		ResourceURN: resourceURN,
		OccurredAt:  time.Now().UTC(),
		TraceID:     traceID,
		Payload:     payload,
	}
}

// PartitionKey is the Kafka partition key for this envelope (tenant_id), so a
// tenant's events keep per-tenant order (MASTER-FR-031).
func (e Envelope) PartitionKey() []byte { return []byte(e.TenantID.String()) }
