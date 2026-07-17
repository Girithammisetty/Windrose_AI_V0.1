// Package events implements the event envelope (MASTER-FR-031), the abstract
// EventPublisher with in-memory and Kafka adapters, the transactional-outbox
// relay worker, and inbound event handlers (tenant provisioning, implicit
// creator grants).
package events

import (
	"time"

	"github.com/google/uuid"
)

// Topic is rbac-service's event topic (MASTER-FR-030).
const Topic = "rbac.events.v1"

// Actor identifies who caused an event.
type Actor struct {
	Type string `json:"type"` // user | service | agent
	ID   string `json:"id"`
}

// ViaAgent carries dual attribution for OBO actions (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Envelope is the platform event envelope (MASTER-FR-031). Partition key is
// tenant_id.
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

// NewEnvelope builds an envelope with a fresh uuidv7 event id.
func NewEnvelope(eventType string, tenant uuid.UUID, actor Actor, resourceURN, traceID string, payload map[string]any) Envelope {
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

// Event types emitted by rbac-service (MASTER-FR-035 naming).
const (
	EvWorkspaceCreated        = "workspace.created"
	EvWorkspaceUpdated        = "workspace.updated"
	EvWorkspaceArchived       = "workspace.archived"
	EvWorkspaceRestored       = "workspace.restored"
	EvWorkspaceDefaultCreated = "workspace.default_created"
	EvGroupCreated            = "group.created"
	EvGroupUpdated            = "group.updated"
	EvGroupDeleted            = "group.deleted"
	EvMemberAdded             = "member.added"
	EvMemberRemoved           = "member.removed"
	EvRoleCreated             = "role.created"
	EvRoleUpdated             = "role.updated"
	EvRoleDeleted             = "role.deleted"
	EvGrantCreated            = "grant.created"
	EvGrantUpdated            = "grant.updated"
	EvGrantDeleted            = "grant.deleted"
	EvProjectionRebuilt       = "projection.rebuilt"
	EvPermissionDenied        = "security.permission_denied"
	EvLastAdminOverridden     = "security.last_admin_overridden"
)
