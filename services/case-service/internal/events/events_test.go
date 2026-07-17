package events

import (
	"testing"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
)

func TestParseCaseURN(t *testing.T) {
	tenant := uuid.New()
	id := uuid.New()
	urn := CaseURN(tenant, id)
	gotT, gotID, ok := ParseCaseURN(urn)
	if !ok || gotT != tenant || gotID != id {
		t.Fatalf("round-trip failed: %v %v %v", gotT, gotID, ok)
	}
	if _, _, ok := ParseCaseURN("not-a-urn"); ok {
		t.Fatal("garbage urn must not parse")
	}
	if _, _, ok := ParseCaseURN("wr:" + tenant.String() + ":case:case/not-a-uuid"); ok {
		t.Fatal("bad id must not parse")
	}
}

// ToMaster maps the service envelope onto the platform wire envelope, carrying
// dual attribution (MASTER-FR-041).
func TestToMasterCarriesViaAgent(t *testing.T) {
	op := domain.Op{Tenant: uuid.New(), Actor: domain.Actor{Type: "user", ID: "approver"},
		ViaAgent: &domain.ViaAgent{AgentID: "copilot", Version: "1.2"}}
	env := NewEnvelope(EvDispositionApplied, op, "wr:t:case:case/x", map[string]any{"k": "v"})
	m := ToMaster(env)
	if m.Actor.Type != "user" || m.Actor.ID != "approver" {
		t.Fatalf("actor lost: %+v", m.Actor)
	}
	if m.ViaAgent == nil || m.ViaAgent.AgentID != "copilot" {
		t.Fatalf("via_agent lost: %+v", m.ViaAgent)
	}
	if m.EventID != env.EventID || m.EventType != EvDispositionApplied {
		t.Fatal("id/type mismatch")
	}
}

func TestNewEnvelopeDefaultsPayload(t *testing.T) {
	env := NewEnvelope(EvCreated, domain.Op{Tenant: uuid.New()}, "urn", nil)
	if env.Payload == nil {
		t.Fatal("nil payload must default to empty map")
	}
	if env.EventID == uuid.Nil {
		t.Fatal("event id must be set")
	}
}
