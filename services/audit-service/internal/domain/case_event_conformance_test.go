package domain

import (
	"encoding/json"
	"strings"
	"testing"
)

// caseWireEnvelope is the exact JSON case-service puts on case.events.v1 (the
// go-common master envelope, MASTER-FR-031) for a background SLA-breach event.
// This mirrors the real producer output so the check below is a true
// cross-service wire-contract test, not a restatement of audit's own struct.
//
// The important field is actor: for background/system-initiated case events the
// service now emits actor={type:"service", id:"case-service"} — a value inside
// the master set. Before the fix it emitted actor.type="system", which audit
// (correctly strict) quarantines to its DLQ as ENVELOPE_INVALID.
const caseWireEnvelope = `{
  "event_id": "0190c3a2-4f6b-7c31-9a2b-1f0c2d3e4f50",
  "event_type": "case.sla.breached",
  "tenant_id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
  "actor": {"type": "service", "id": "case-service"},
  "via_agent": null,
  "resource_urn": "wr:b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e:case:case/aa11bb22-cc33-4d44-8e55-6f7788990011",
  "occurred_at": "2026-07-10T12:00:00Z",
  "trace_id": "",
  "payload": {"case_number": 42, "on_breach": "auto_unassign", "reassign_count": 1}
}`

// TestCaseServiceEnvelopeAcceptedByAudit proves audit-service ACCEPTS case
// events emitted after the actor.type fix: the real wire bytes decode into
// audit's Envelope and pass ValidateEnvelope, so the event is written to the
// ClickHouse audit trail rather than quarantined to the ENVELOPE_INVALID DLQ
// (AUD-FR-002; the "every claim decision auditable" guarantee).
func TestCaseServiceEnvelopeAcceptedByAudit(t *testing.T) {
	var env Envelope
	if err := json.Unmarshal([]byte(caseWireEnvelope), &env); err != nil {
		t.Fatalf("case-service wire envelope must decode into audit's Envelope: %v", err)
	}
	if err := ValidateEnvelope(env); err != nil {
		t.Fatalf("audit rejected a conformant case event (would DLQ): %v", err)
	}
	// Sanity: the fields audit keys on are all present and typed.
	if env.Actor.Type != "service" || env.Actor.ID != "case-service" {
		t.Fatalf("unexpected actor: %+v", env.Actor)
	}
	if env.EventType != "case.sla.breached" {
		t.Fatalf("unexpected event_type: %q", env.EventType)
	}
}

// TestSystemActorStillRejected pins the pre-fix behavior: an actor.type="system"
// case event MUST be rejected as ENVELOPE_INVALID. This documents that the fix
// was on case-service's side — audit's strictness is correct and unchanged.
func TestSystemActorStillRejected(t *testing.T) {
	bad := strings.Replace(caseWireEnvelope,
		`"actor": {"type": "service", "id": "case-service"}`,
		`"actor": {"type": "system", "id": "sla"}`, 1)
	var env Envelope
	if err := json.Unmarshal([]byte(bad), &env); err != nil {
		t.Fatalf("decode: %v", err)
	}
	err := ValidateEnvelope(env)
	if err == nil {
		t.Fatal("actor.type=system must be rejected (ENVELOPE_INVALID) — audit must stay strict")
	}
	if !strings.Contains(err.Error(), ReasonEnvelopeInvalid) {
		t.Fatalf("expected ENVELOPE_INVALID, got: %v", err)
	}
}
