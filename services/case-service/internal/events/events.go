// Package events implements case-service's event layer: the platform envelope
// (MASTER-FR-031), the emitted case.events.v1 catalog (BRD 08 §6), URN
// builders (MASTER-FR-013), the outbox relay port (MASTER-FR-034) and inbound
// consumers (inference.completed auto-case, user.deactivated unassign).
package events

import (
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
)

// Topic is case-service's event topic (MASTER-FR-030).
const Topic = "case.events.v1"

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
	Payload     map[string]any    `json:"payload"`
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

// Emitted event types (BRD 08 §6, MASTER-FR-035 naming).
const (
	EvCreated         = "case.created"
	EvAssigned        = "case.assigned"
	EvUnassigned      = "case.unassigned"
	EvStarted         = "case.started"
	EvResolved        = "case.resolved"
	EvReopened        = "case.reopened"
	EvClosed          = "case.closed"
	EvEscalated       = "case.escalated"
	EvSLAWarning      = "case.sla.warning"
	EvSLABreached     = "case.sla.breached"
	EvCommentAdded    = "case.comment.added"
	EvSeverityChanged = "case.severity.changed"
	EvBulkCompleted   = "case.bulk.completed"
	EvLimitWarning    = "case.limit.warning"

	// Learning-loop hooks (BRD 08 §1, CASE-FR-051): a human triage correction
	// becomes a labeled training signal. disposition_applied carries the row
	// reference + disposition; correction_recorded fires when a copilot
	// proposal is applied (dual-attributed to the human approver + agent).
	EvDispositionApplied  = "case.disposition_applied"
	EvCorrectionRecorded  = "case.correction_recorded"

	EvCrossTenantDenied = "security.cross_tenant_denied" // MASTER-FR-003
	EvPermissionDenied  = "security.permission_denied"   // MASTER-FR-040
)

// CaseURN builds a case resource URN (MASTER-FR-013).
func CaseURN(tenant, id uuid.UUID) string {
	return "wr:" + tenant.String() + ":case:case/" + id.String()
}

// DispositionURN builds a disposition catalog URN.
func DispositionURN(tenant, id uuid.UUID) string {
	return "wr:" + tenant.String() + ":case:disposition/" + id.String()
}

// ParseCaseURN extracts (tenant, case id) from a case URN
// wr:<tenant>:case:case/<id>. Used by the search indexer to route an event to
// the right case projection.
func ParseCaseURN(urn string) (tenant, id uuid.UUID, ok bool) {
	// wr:<tenant>:case:case/<id>
	const prefix = "wr:"
	if len(urn) < len(prefix) || urn[:len(prefix)] != prefix {
		return uuid.Nil, uuid.Nil, false
	}
	rest := urn[len(prefix):]
	// rest = <tenant>:case:case/<id>
	var tenantStr, tail string
	for i := 0; i < len(rest); i++ {
		if rest[i] == ':' {
			tenantStr = rest[:i]
			tail = rest[i+1:]
			break
		}
	}
	slash := -1
	for i := 0; i < len(tail); i++ {
		if tail[i] == '/' {
			slash = i
		}
	}
	if slash < 0 {
		return uuid.Nil, uuid.Nil, false
	}
	t, err1 := uuid.Parse(tenantStr)
	cid, err2 := uuid.Parse(tail[slash+1:])
	if err1 != nil || err2 != nil {
		return uuid.Nil, uuid.Nil, false
	}
	return t, cid, true
}

// OutboxRow is one unpublished outbox entry.
type OutboxRow struct {
	ID       int64
	Envelope Envelope
}
