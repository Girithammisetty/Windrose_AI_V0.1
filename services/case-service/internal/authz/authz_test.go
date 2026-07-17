package authz

import (
	"context"
	"testing"
)

// Unit-tier authz matrix: the Static double denies the listed actions and
// allows the rest (the integration tier runs the same matrix against the real
// OPA sidecar). MASTER-FR-071.
func TestStaticAuthzMatrix(t *testing.T) {
	az := Static{Denied: map[string]bool{ActionProposalApply: true, ActionCaseExport: true}}
	deny := []string{ActionProposalApply, ActionCaseExport}
	allow := []string{ActionCaseRead, ActionCaseCreate, ActionCaseBulk, ActionDispositionCreate, ActionDispositionUpdate}
	for _, a := range deny {
		if az.Allow(context.Background(), Input{Action: a}) {
			t.Fatalf("expected deny for %s", a)
		}
	}
	for _, a := range allow {
		if !az.Allow(context.Background(), Input{Action: a}) {
			t.Fatalf("expected allow for %s", a)
		}
	}
}
