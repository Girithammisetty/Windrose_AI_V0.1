package events

import (
	"testing"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/datacern-ai/go-common/event"

	"github.com/datacern-ai/identity-service/internal/domain"
)

// TestToEnvelopeConformsToMasterContract checks that identity-service's
// outbox events, once converted to the shared envelope via toEnvelope, pass
// the master event contract validator (MASTER-FR-031/041, WS5 BRD 58) — the
// same rules audit-service enforces on the consumption side before a DLQ.
func TestToEnvelopeConformsToMasterContract(t *testing.T) {
	tid, err := uuid.NewV7()
	if err != nil {
		t.Fatal(err)
	}
	actor := domain.Actor{Type: "user", ID: "user-123"}
	urn := domain.URN(tid, "user", "user-123")
	ev := domain.NewEvent(domain.EvUserActivated, tid, actor, urn, time.Now().UTC(), map[string]any{
		"email": "someone@example.com",
	})

	if err := gcevent.Validate(toEnvelope(&ev)); err != nil {
		t.Fatalf("envelope built by toEnvelope failed master conformance validation: %v", err)
	}
}
