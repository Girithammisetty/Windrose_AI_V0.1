// Package events defines notification-service's emitted topic and event
// catalog (BRD 19 §6) plus the consumed topic set. Emitted events land on
// notification.events.v1 via the transactional outbox (MASTER-FR-034); they are
// ops-grade (consumed by audit-service + dashboards, no functional dependents).
package events

import (
	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
)

// Topic is notification-service's own event topic (MASTER-FR-030).
const Topic = "notification.events.v1"

// Emitted event types (BRD 19 §6, MASTER-FR-035 naming).
const (
	EvNotificationCreated = "notification.created"
	EvDeliveryUpdated     = "notification.delivery.updated"
	EvCircuitOpened       = "notification.webhook.circuit_opened"
	EvCircuitClosed       = "notification.webhook.circuit_closed"
	EvEndpointDisabled    = "notification.endpoint.disabled"
	EvAudienceTruncated   = "notification.audience.truncated"
	EvRateLimited         = "notification.rate_limited"
	// Audit (MASTER-FR-040/003).
	EvPermissionDenied = "security.permission_denied"
	EvCrossTenant      = "security.cross_tenant_denied"
)

// New builds an emitted envelope with a fresh uuidv7 id. Payloads carry
// resource references only, never PII (MASTER-FR-042).
func New(eventType string, tenant uuid.UUID, actor gcevent.Actor, resourceURN, traceID string, payload map[string]any) gcevent.Envelope {
	return gcevent.New(eventType, tenant, actor, resourceURN, traceID, payload)
}

// FromOp builds an emitted envelope from an authenticated Op (audit path).
func FromOp(eventType string, op domain.Op, resourceURN string, payload map[string]any) gcevent.Envelope {
	env := gcevent.New(eventType, op.Tenant, gcevent.Actor{Type: op.Actor.Type, ID: op.Actor.ID}, resourceURN, op.TraceID, payload)
	if op.ViaAgent != nil {
		env.ViaAgent = &gcevent.ViaAgent{AgentID: op.ViaAgent.AgentID, Version: op.ViaAgent.Version}
	}
	return env
}

// ConsumedTopics is the full set of platform topics the pipeline consumes
// (NOTIF-FR-001). All events are filtered against the mapping registry.
//
// "audit.export.v1" (Phase 3, docs/design/siem-export.md) is audit-service's
// external SIEM-export topic — added here so a tenant's webhook_endpoint can
// subscribe to it (event_types: ["audit.export.v1"]) and have it forwarded
// through the EXISTING webhook delivery/retry/circuit-breaker pipeline
// (internal/pipeline/webhook.go), the same mechanism used for every other
// webhook-eligible event. See registry.go's "audit.export.v1" mapping: it has
// no audience/channels of its own (no email/in-app noise), it exists purely
// so Registry.Lookup succeeds and Process reaches deliverWebhooks.
func ConsumedTopics() []string {
	return []string{
		"identity.events.v1", "rbac.events.v1", "ingestion.events.v1", "dataset.events.v1",
		"query.events.v1", "semantic.events.v1", "experiment.events.v1", "pipeline.events.v1",
		"inference.events.v1", "chart.events.v1", "case.events.v1", "usage.events.v1", "ai.events.v1",
		"audit.export.v1",
		// ai.proposal.v1 (task #78): agent-runtime's proposal.created/approved/
		// rejected events were never consumed here, so registry.go's own
		// proposal.* mappings (approvers/proposer in-app+email push) were dead
		// despite existing — separate from realtime-hub's SSE fan-out fix.
		"ai.proposal.v1",
	}
}
