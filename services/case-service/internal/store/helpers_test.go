package store

import (
	"testing"

	"github.com/windrose-ai/case-service/internal/domain"
)

func TestContainsAndDeref(t *testing.T) {
	if !contains([]string{"a", "b"}, "b") || contains([]string{"a"}, "z") {
		t.Fatal("contains wrong")
	}
	s := "x"
	if derefStr(&s) != "x" || derefStr(nil) != "" {
		t.Fatal("derefStr wrong")
	}
}

// purposeMatch: a field with purpose=both matches any requested purpose; an
// empty request matches everything (CASE-FR-022 form modes).
func TestPurposeMatch(t *testing.T) {
	if !purposeMatch(domain.PurposeBoth, []int16{domain.PurposeCreate}) {
		t.Fatal("both must match create")
	}
	if !purposeMatch(domain.PurposeUpdate, []int16{domain.PurposeUpdate}) {
		t.Fatal("update must match update")
	}
	if purposeMatch(domain.PurposeCreate, []int16{domain.PurposeUpdate}) {
		t.Fatal("create must not match update-only request")
	}
	if !purposeMatch(domain.PurposeCreate, nil) {
		t.Fatal("empty request matches all")
	}
}

func TestClampLimit(t *testing.T) {
	if ClampLimit(0) != 50 || ClampLimit(999) != 200 || ClampLimit(30) != 30 {
		t.Fatal("ClampLimit bounds wrong")
	}
}

func TestTerminalForSLA(t *testing.T) {
	for _, st := range []domain.Status{domain.StatusResolved, domain.StatusClosed, domain.StatusUnassigned} {
		if !terminalForSLA(st) {
			t.Fatalf("%v should be terminal for SLA", st)
		}
	}
	for _, st := range []domain.Status{domain.StatusDraft, domain.StatusInProgress} {
		if terminalForSLA(st) {
			t.Fatalf("%v should NOT be terminal for SLA", st)
		}
	}
}
