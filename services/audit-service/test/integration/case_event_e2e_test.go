package integration

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/domain"
)

// TestCaseServiceSystemEventLandsInClickHouse is the cross-service guarantee for
// GAP 2: a background/system case event as case-service now emits it — actor
// {type:"service", id:"case-service"} — flows over the REAL case.events.v1 topic
// through the REAL audit consumer into the REAL ClickHouse audit trail, and is
// NOT quarantined to the ENVELOPE_INVALID DLQ. Before the actor.type fix these
// events used actor.type="system" and were DLQ'd, breaking the "every claim
// decision auditable" compliance guarantee (AUD-FR-001/002).
func TestCaseServiceSystemEventLandsInClickHouse(t *testing.T) {
	h := newHarness(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go h.consumer.Run(ctx)

	tenant := uuid.New()
	caseID := uuid.New()
	e := domain.Envelope{
		EventID:     uuid.New(),
		EventType:   "case.sla.breached",
		TenantID:    tenant,
		Actor:       domain.Actor{Type: "service", ID: "case-service"}, // the fix
		ResourceURN: "wr:" + tenant.String() + ":case:case/" + caseID.String(),
		OccurredAt:  time.Now().UTC(),
		TraceID:     "", // system sweep has no inbound trace; audit accepts empty
		Payload:     map[string]any{"case_number": 42, "on_breach": "auto_unassign", "reassign_count": 1},
	}
	h.produce(t, "case.events.v1", e)

	// It must land in ClickHouse (accepted, chained) within the ingest window.
	deadline := time.Now().Add(40 * time.Second)
	var rec *domain.Record
	for time.Now().Before(deadline) {
		if r, err := h.ch.GetEvent(context.Background(), tenant, e.EventID); err == nil && r != nil {
			rec = r
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	if rec == nil {
		t.Fatal("case.sla.breached (actor=service) never landed in ClickHouse — audit rejected it")
	}
	if rec.ResourceService != "case" {
		t.Fatalf("resource_urn not decoded as a case event: %+v", rec)
	}
	if rec.PayloadDigest != domain.PayloadDigest(e.Payload) {
		t.Fatalf("payload digest mismatch: %s", rec.PayloadDigest)
	}
	if rec.ChainSeq == 0 {
		t.Fatalf("event not chained (chain_seq=0): %+v", rec)
	}

	// And it must NOT have been quarantined as ENVELOPE_INVALID.
	dlqTopic := fmt.Sprintf("case.events.v1.%s.dlq", h.group)
	if h.waitForDLQ(t, dlqTopic, domain.ReasonEnvelopeInvalid, 2*time.Second) {
		t.Fatal("case event was quarantined to the ENVELOPE_INVALID DLQ (regression)")
	}
}
