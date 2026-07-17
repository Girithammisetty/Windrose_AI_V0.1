package api

import (
	"testing"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
)

// TestResolveMutationResolvedPayload pins the case.resolved payload contract
// memory-service's resolved_cases corpus mapper grounds copilot RAG on:
// resolution_note is the embedded narrative and authored_by (the resolving
// actor) drives right-to-erasure user linkage (MEM-FR-040).
func TestResolveMutationResolvedPayload(t *testing.T) {
	op := domain.Op{
		Tenant:  uuid.New(),
		Actor:   domain.Actor{Type: "user", ID: "u-123"},
		TraceID: "trace-abc",
	}
	c := &domain.Case{
		ID:         uuid.New(),
		TenantID:   op.Tenant,
		CaseNumber: 8,
		Status:     domain.StatusInProgress,
		Severity:   "medium",
	}
	disp := &domain.Disposition{
		ID:       uuid.New(),
		Code:     "duplicate_invoice",
		Category: "true_positive",
		Active:   true,
	}
	note := "confirmed duplicate of INV-991, vendor double-billed"

	m, err := (&Server{}).resolveMutation(op, c, disp, note, "")
	if err != nil {
		t.Fatalf("resolveMutation: %v", err)
	}

	var resolved *events.Envelope
	for i := range m.Events {
		if m.Events[i].EventType == events.EvResolved {
			resolved = &m.Events[i]
		}
	}
	if resolved == nil {
		t.Fatal("resolveMutation must emit a case.resolved envelope")
	}
	want := map[string]any{
		"case_number":          c.CaseNumber,
		"disposition_code":     disp.Code,
		"disposition_category": disp.Category,
		"resolution_note":      note,
		"authored_by":          op.Actor.ID,
	}
	for k, v := range want {
		if got := resolved.Payload[k]; got != v {
			t.Errorf("case.resolved payload[%q] = %v, want %v", k, got, v)
		}
	}
}
