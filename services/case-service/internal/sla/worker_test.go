package sla

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/store"
)

// fakeStore is a unit-tier double (never reachable from cmd/server) that records
// which fire path the sweep dispatched to.
type fakeStore struct {
	timers    []store.SLADueTimer
	warnFired []uuid.UUID
	dueFired  []uuid.UUID
	policy    domain.SLAPolicy
}

func (f *fakeStore) DueTimers(_ context.Context, _ time.Time, _ int) ([]store.SLADueTimer, error) {
	return f.timers, nil
}
func (f *fakeStore) FireWarnTimer(_ context.Context, _, caseID uuid.UUID) error {
	f.warnFired = append(f.warnFired, caseID)
	return nil
}
func (f *fakeStore) FireDueTimer(_ context.Context, _, caseID uuid.UUID, _ domain.SLAPolicy) error {
	f.dueFired = append(f.dueFired, caseID)
	return nil
}
func (f *fakeStore) PolicyForCase(_ context.Context, _, _ uuid.UUID) (domain.SLAPolicy, error) {
	return f.policy, nil
}

// The sweep routes warn timers to FireWarnTimer and due timers to FireDueTimer,
// resolving each due timer's policy first (CASE-FR-012).
func TestSweepDispatch(t *testing.T) {
	warnCase, dueCase := uuid.New(), uuid.New()
	f := &fakeStore{
		timers: []store.SLADueTimer{
			{TenantID: uuid.New(), CaseID: warnCase, Kind: "warn"},
			{TenantID: uuid.New(), CaseID: dueCase, Kind: "due"},
		},
		policy: domain.SLAPolicy{OnBreach: domain.BreachAutoUnassign, MaxReassignCount: 3},
	}
	w := New(f)
	if err := w.Sweep(context.Background()); err != nil {
		t.Fatalf("sweep: %v", err)
	}
	if len(f.warnFired) != 1 || f.warnFired[0] != warnCase {
		t.Fatalf("warn dispatch wrong: %v", f.warnFired)
	}
	if len(f.dueFired) != 1 || f.dueFired[0] != dueCase {
		t.Fatalf("due dispatch wrong: %v", f.dueFired)
	}
}

func TestNewDefaults(t *testing.T) {
	w := New(&fakeStore{})
	if w.Interval <= 0 || w.Batch <= 0 {
		t.Fatal("worker defaults not set")
	}
}
