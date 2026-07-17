package events

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
)

// masterActorTypes is the closed set the platform master envelope allows for
// actor.type (MASTER-FR-031). audit-service rejects anything else to its DLQ as
// ENVELOPE_INVALID, so case-service must only ever emit one of these.
var masterActorTypes = map[string]bool{"user": true, "service": true, "agent": true, "platform": true}

// assertConformsToMaster validates a wire envelope against the master contract
// (MASTER-FR-031 / §2.4-031): {event_id uuidv7, event_type, tenant_id,
// actor{type,id}, via_agent|null, resource_urn, occurred_at, trace_id, payload}.
// It mirrors audit-service's ValidateEnvelope so a pass here means audit accepts
// the event. The envelope is checked as it goes on the wire (JSON), which is how
// audit-service decodes it.
func assertConformsToMaster(t *testing.T, env Envelope) {
	t.Helper()
	wire := ToMaster(env)
	raw, err := json.Marshal(wire)
	if err != nil {
		t.Fatalf("envelope must marshal: %v", err)
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("envelope must be a JSON object: %v", err)
	}

	// Every required key must be present on the wire.
	for _, k := range []string{
		"event_id", "event_type", "tenant_id", "actor", "via_agent",
		"resource_urn", "occurred_at", "trace_id", "payload",
	} {
		if _, ok := m[k]; !ok {
			t.Fatalf("missing required envelope field %q", k)
		}
	}

	// event_id: a non-nil uuid, and specifically a time-ordered uuidv7 (MASTER-FR-021).
	if wire.EventID == uuid.Nil {
		t.Fatal("event_id must be set (non-nil)")
	}
	if v := wire.EventID.Version(); v != 7 {
		t.Fatalf("event_id must be uuidv7, got version %d", v)
	}

	// event_type: non-empty <resource>.<verb> (MASTER-FR-035).
	if strings.TrimSpace(wire.EventType) == "" {
		t.Fatal("event_type must be non-empty")
	}

	// tenant_id: non-nil (partition key + RLS scope).
	if wire.TenantID == uuid.Nil {
		t.Fatal("tenant_id must be set (non-nil)")
	}

	// actor: type in the allowed set AND id non-empty (audit's hard gate).
	if !masterActorTypes[wire.Actor.Type] {
		t.Fatalf("actor.type %q not in master set {user,service,agent,platform} — audit would DLQ this as ENVELOPE_INVALID", wire.Actor.Type)
	}
	if strings.TrimSpace(wire.Actor.ID) == "" {
		t.Fatal("actor.id must be non-empty")
	}

	// resource_urn: case events are always resource-scoped (MASTER-FR-013).
	if strings.TrimSpace(wire.ResourceURN) == "" {
		t.Fatal("resource_urn must be non-empty for a case event")
	}

	// occurred_at: a real timestamp.
	if wire.OccurredAt.IsZero() {
		t.Fatal("occurred_at must be set")
	}

	// payload: present and a JSON object (never null), so canonical digesting works.
	if wire.Payload == nil {
		t.Fatal("payload must be non-nil")
	}
	if !strings.HasPrefix(strings.TrimSpace(string(m["payload"])), "{") {
		t.Fatalf("payload must serialize as a JSON object, got %s", string(m["payload"]))
	}
}

// TestEmittedEnvelopeConformsToMaster asserts every emitted case event carries a
// fully-conformant master envelope, for every actor kind the service produces:
// user (API), agent (autonomous), and — the regression that made audit DLQ case
// events — the service/system attribution used by the SLA sweep, the identity
// unassign and the inbound consumers.
func TestEmittedEnvelopeConformsToMaster(t *testing.T) {
	tenant := uuid.New()
	caseID := uuid.New()
	urn := CaseURN(tenant, caseID)

	// The service/background attribution (SLA, identity, consumer) after the fix.
	serviceOp := systemOp(tenant)
	if serviceOp.Actor.Type != "service" {
		t.Fatalf("background/system events must emit actor.type=service, got %q", serviceOp.Actor.Type)
	}

	ops := map[string]domain.Op{
		"user":    {Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "u-123"}, TraceID: "trace-abc"},
		"agent":   {Tenant: tenant, Actor: domain.Actor{Type: "agent", ID: "triage-copilot"}, ViaAgent: &domain.ViaAgent{AgentID: "triage-copilot", Version: "1.4.0"}},
		"service": serviceOp,
	}

	// Cover the full emitted catalog (BRD 08 §6) across each actor kind.
	eventTypes := []string{
		EvCreated, EvAssigned, EvUnassigned, EvStarted, EvResolved, EvReopened, EvClosed,
		EvEscalated, EvSLAWarning, EvSLABreached, EvCommentAdded, EvSeverityChanged,
		EvBulkCompleted, EvLimitWarning, EvDispositionApplied, EvCorrectionRecorded,
	}

	for actorKind, op := range ops {
		for _, et := range eventTypes {
			env := NewEnvelope(et, op, urn, map[string]any{"case_number": int64(7)})
			t.Run(actorKind+"/"+et, func(t *testing.T) {
				assertConformsToMaster(t, env)
			})
		}
	}
}

// TestResolvedPayloadCarriesGroundingFields pins the case.resolved payload
// contract memory-service's resolved_cases corpus mapper depends on
// (map_case_resolved): resolution_note is the text embedded for copilot RAG
// grounding and authored_by is the erasure user-linkage (MEM-FR-040). The keys
// must survive the wire round-trip alongside the original triple.
func TestResolvedPayloadCarriesGroundingFields(t *testing.T) {
	tenant := uuid.New()
	op := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "u-123"}, TraceID: "trace-abc"}
	env := NewEnvelope(EvResolved, op, CaseURN(tenant, uuid.New()), map[string]any{
		"case_number": int64(8), "disposition_code": "duplicate_invoice", "disposition_category": "true_positive",
		"resolution_note": "confirmed duplicate of INV-991, vendor double-billed", "authored_by": op.Actor.ID,
	})
	assertConformsToMaster(t, env)

	raw, err := json.Marshal(ToMaster(env))
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var wire struct {
		Payload map[string]any `json:"payload"`
	}
	if err := json.Unmarshal(raw, &wire); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	for _, k := range []string{"case_number", "disposition_code", "disposition_category", "resolution_note", "authored_by"} {
		if v, ok := wire.Payload[k]; !ok || v == "" {
			t.Fatalf("case.resolved payload must carry non-empty %q on the wire, got %v", k, wire.Payload[k])
		}
	}
}

// TestSystemActorTypeIsAudtAccepted is a focused regression guard for the exact
// bug: the SLA/identity/consumer paths previously emitted actor.type="system",
// which is NOT in the master set, so audit-service quarantined those case events
// to its DLQ as ENVELOPE_INVALID. The fix emits actor={service,case-service}.
func TestSystemActorTypeIsAuditAccepted(t *testing.T) {
	op := systemOp(uuid.New())
	if op.Actor.Type == "system" {
		t.Fatal("regression: background events must not emit actor.type=system (audit DLQs it)")
	}
	if !masterActorTypes[op.Actor.Type] {
		t.Fatalf("background actor.type %q is not audit-acceptable", op.Actor.Type)
	}
	if strings.TrimSpace(op.Actor.ID) == "" {
		t.Fatal("background actor.id must be non-empty")
	}
	// The mapped wire time must be UTC and non-zero.
	env := NewEnvelope(EvSLABreached, op, "wr:t:case:case/x", nil)
	if env.OccurredAt.Location() != time.UTC {
		t.Fatalf("occurred_at must be UTC, got %v", env.OccurredAt.Location())
	}
}
