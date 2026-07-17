package domain

import (
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

func newCase(status Status, assignee *uuid.UUID) *Case {
	return &Case{ID: NewID(), Status: status, Severity: SeverityMedium, AssignedToID: assignee,
		DueDate: time.Now().Add(2 * time.Hour), CaseVersion: 1}
}

func ptrUUID() *uuid.UUID { u := uuid.New(); return &u }

// The lifecycle transition matrix (BRD 08 §4) — the guard for every legal and
// illegal edge, driving the assignee⇔status invariant and disposition rules.
func TestStateMachine_TransitionMatrix(t *testing.T) {
	now := time.Now()
	disp := &Disposition{ID: uuid.New(), Active: true}
	dispNote := &Disposition{ID: uuid.New(), Active: true, RequiresNote: true}

	t.Run("assign unassigned->draft", func(t *testing.T) {
		c := newCase(StatusUnassigned, nil)
		if err := c.Assign(uuid.New(), now); err != nil {
			t.Fatalf("assign: %v", err)
		}
		if c.Status != StatusDraft || c.AssignedToID == nil {
			t.Fatalf("want draft+assignee, got %v", c.Status)
		}
	})

	t.Run("reassign increments count, status unchanged", func(t *testing.T) {
		c := newCase(StatusInProgress, ptrUUID())
		if err := c.Assign(uuid.New(), now); err != nil {
			t.Fatal(err)
		}
		if c.Status != StatusInProgress || c.ReassignCount != 1 {
			t.Fatalf("want in_progress+count1, got %v/%d", c.Status, c.ReassignCount)
		}
	})

	t.Run("cannot assign resolved", func(t *testing.T) {
		c := newCase(StatusResolved, ptrUUID())
		if err := c.Assign(uuid.New(), now); err == nil {
			t.Fatal("expected invalid transition")
		}
	})

	t.Run("start draft->in_progress", func(t *testing.T) {
		c := newCase(StatusDraft, ptrUUID())
		if err := c.Start(); err != nil || c.Status != StatusInProgress {
			t.Fatalf("start: %v status=%v", err, c.Status)
		}
	})

	t.Run("start from unassigned invalid", func(t *testing.T) {
		c := newCase(StatusUnassigned, nil)
		if err := c.Start(); err == nil {
			t.Fatal("expected invalid transition")
		}
	})

	t.Run("resolve requires in_progress (AC-5)", func(t *testing.T) {
		c := newCase(StatusUnassigned, nil)
		if err := c.Resolve(disp, "", now); err == nil {
			t.Fatal("expected INVALID_TRANSITION resolving unassigned")
		}
	})

	t.Run("resolve requires active disposition", func(t *testing.T) {
		c := newCase(StatusInProgress, ptrUUID())
		if err := c.Resolve(&Disposition{Active: false}, "", now); err == nil {
			t.Fatal("expected DISPOSITION_REQUIRED")
		}
	})

	t.Run("resolve requires note when disposition demands it (AC-5)", func(t *testing.T) {
		c := newCase(StatusInProgress, ptrUUID())
		if err := c.Resolve(dispNote, "", now); err == nil {
			t.Fatal("expected DISPOSITION_NOTE_REQUIRED")
		}
		c2 := newCase(StatusInProgress, ptrUUID())
		if err := c2.Resolve(dispNote, "confirmed fraud", now); err != nil {
			t.Fatalf("resolve with note: %v", err)
		}
		if c2.Status != StatusResolved || c2.ResolvedAt == nil {
			t.Fatal("want resolved with resolved_at")
		}
	})

	t.Run("unassign draft/in_progress->unassigned", func(t *testing.T) {
		for _, st := range []Status{StatusDraft, StatusInProgress} {
			c := newCase(st, ptrUUID())
			if err := c.Unassign(); err != nil {
				t.Fatal(err)
			}
			if c.Status != StatusUnassigned || c.AssignedToID != nil {
				t.Fatalf("want unassigned+nil assignee, got %v", c.Status)
			}
		}
	})

	t.Run("reopen within window, blocked after", func(t *testing.T) {
		c := newCase(StatusResolved, ptrUUID())
		resolvedAt := now.Add(-10 * 24 * time.Hour)
		c.ResolvedAt = &resolvedAt
		c.DispositionID = &disp.ID
		if err := c.Reopen(now); err != nil || c.Status != StatusInProgress {
			t.Fatalf("reopen: %v", err)
		}
		if c.DispositionID != nil {
			t.Fatal("reopen must clear disposition")
		}
		c2 := newCase(StatusResolved, ptrUUID())
		old := now.Add(-40 * 24 * time.Hour)
		c2.ResolvedAt = &old
		if err := c2.Reopen(now); err == nil {
			t.Fatal("expected reopen window elapsed")
		}
	})

	t.Run("close resolved->closed terminal", func(t *testing.T) {
		c := newCase(StatusResolved, ptrUUID())
		if err := c.Close("snapshots/x.json.gz", now); err != nil || c.Status != StatusClosed {
			t.Fatalf("close: %v", err)
		}
		if err := c.Reopen(now); err == nil {
			t.Fatal("closed is terminal; reopen must fail")
		}
	})
}

// The assignee⇔status invariant (BR-1) holds across every mutation path.
func TestStateMachine_Invariant(t *testing.T) {
	now := time.Now()
	check := func(c *Case) {
		nilAssignee := c.AssignedToID == nil
		isUnassigned := c.Status == StatusUnassigned
		if nilAssignee != isUnassigned {
			t.Fatalf("invariant violated: assignee_nil=%v status=%v", nilAssignee, c.Status)
		}
	}
	c := newCase(StatusUnassigned, nil)
	check(c)
	_ = c.Assign(uuid.New(), now)
	check(c)
	_ = c.Start()
	check(c)
	_ = c.Unassign()
	check(c)
}

// The domain state machine is pure and safe under concurrent use of distinct
// cases (go test -race gate).
func TestStateMachine_RaceDistinctCases(t *testing.T) {
	now := time.Now()
	var wg sync.WaitGroup
	for i := 0; i < 64; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c := newCase(StatusUnassigned, nil)
			if err := c.Assign(uuid.New(), now); err != nil {
				t.Error(err)
			}
			if err := c.Start(); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
}
