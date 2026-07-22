package event

import (
	"testing"
	"time"

	"github.com/google/uuid"
)

func validEnvelope() Envelope {
	return New("case.created", uuid.New(), Actor{Type: "user", ID: "u-1"}, "wr:t:case:c-1", "trace-1", nil)
}

func TestValidateAcceptsWellFormedEnvelope(t *testing.T) {
	if err := Validate(validEnvelope()); err != nil {
		t.Fatalf("expected a well-formed envelope to validate, got: %v", err)
	}
}

func TestValidateAcceptsEveryMasterActorType(t *testing.T) {
	for _, actorType := range []string{"user", "service", "agent", "platform"} {
		e := validEnvelope()
		e.Actor.Type = actorType
		if err := Validate(e); err != nil {
			t.Fatalf("actor.type %q must be accepted (MASTER-FR-031/041), got: %v", actorType, err)
		}
	}
}

func TestValidateRejectsUnknownActorType(t *testing.T) {
	e := validEnvelope()
	e.Actor.Type = "system" // the exact regression BRD 58 caught in case-service
	if err := Validate(e); err == nil {
		t.Fatal("expected actor.type \"system\" to be rejected")
	}
}

func TestValidateRejectsMissingRequiredFields(t *testing.T) {
	cases := map[string]func(e *Envelope){
		"event_id":    func(e *Envelope) { e.EventID = uuid.Nil },
		"event_type":  func(e *Envelope) { e.EventType = "" },
		"tenant_id":   func(e *Envelope) { e.TenantID = uuid.Nil },
		"actor.type":  func(e *Envelope) { e.Actor.Type = "" },
		"actor.id":    func(e *Envelope) { e.Actor.ID = "" },
		"occurred_at": func(e *Envelope) { e.OccurredAt = time.Time{} },
	}
	for name, mutate := range cases {
		e := validEnvelope()
		mutate(&e)
		if err := Validate(e); err == nil {
			t.Fatalf("expected missing %s to fail validation", name)
		}
	}
}

func TestValidateRejectsNilPayload(t *testing.T) {
	e := validEnvelope()
	e.Payload = nil
	if err := Validate(e); err == nil {
		t.Fatal("expected a nil payload to fail validation")
	}
}
