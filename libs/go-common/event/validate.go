package event

import (
	"fmt"
	"strings"

	"github.com/google/uuid"
)

// masterActorTypes is the closed set MASTER-FR-031/041 allows for Actor.Type.
var masterActorTypes = map[string]bool{
	"user": true, "service": true, "agent": true, "platform": true,
}

// Validate checks an Envelope against the master event contract
// (MASTER-FR-031/041) — the same required-field and actor.type rules
// audit-service's consumption-side ValidateEnvelope enforces before a DLQ,
// factored out here as the single source of truth every emitting service can
// unit-test against (WS5, BRD 58: event-envelope conformance as a CI gate).
func Validate(e Envelope) error {
	var missing []string
	if e.EventID == uuid.Nil {
		missing = append(missing, "event_id")
	}
	if strings.TrimSpace(e.EventType) == "" {
		missing = append(missing, "event_type")
	}
	if e.TenantID == uuid.Nil {
		missing = append(missing, "tenant_id")
	}
	if strings.TrimSpace(e.Actor.Type) == "" || strings.TrimSpace(e.Actor.ID) == "" {
		missing = append(missing, "actor")
	}
	if e.OccurredAt.IsZero() {
		missing = append(missing, "occurred_at")
	}
	if len(missing) > 0 {
		return fmt.Errorf("envelope invalid: missing/invalid %s", strings.Join(missing, ","))
	}
	if !masterActorTypes[e.Actor.Type] {
		return fmt.Errorf("envelope invalid: actor.type %q not allowed", e.Actor.Type)
	}
	if e.Payload == nil {
		return fmt.Errorf("envelope invalid: payload must not be nil")
	}
	return nil
}
