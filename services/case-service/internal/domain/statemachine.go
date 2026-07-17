package domain

import (
	"time"

	"github.com/google/uuid"
)

// The lifecycle state machine (BRD 08 §4). Each guarded transition below
// mutates the *Case in place (status/assignee/timestamps) and returns a coded
// *Error when the guard fails. It never touches case_version — the store layer
// bumps the version and appends the timeline event atomically. Keeping these
// pure makes the transition matrix unit- and race-testable without infra.

// ReopenWindow is the maximum age after resolution during which a case may be
// reopened (CASE-FR-014, BR-7).
const ReopenWindow = 30 * 24 * time.Hour

// Unassign reasons (CASE-FR-012, event payload).
const (
	ReasonManual     = "manual"
	ReasonSLABreach  = "sla_breach"
	ReasonDeactivate = "user_deactivated"
)

// Assign assigns (or reassigns) the case to assignee. From unassigned it moves
// to draft; from draft/in_progress it reassigns with status unchanged
// (incrementing reassign_count). Resolved/closed cannot be assigned
// (CASE-FR-011, BR-1).
func (c *Case) Assign(assignee uuid.UUID, now time.Time) error {
	switch c.Status {
	case StatusUnassigned:
		c.Status = StatusDraft
	case StatusDraft, StatusInProgress:
		if c.AssignedToID != nil && *c.AssignedToID != assignee {
			c.ReassignCount++
		}
	default:
		return EInvalidTransition("cannot assign a " + c.Status.String() + " case")
	}
	a := assignee
	c.AssignedToID = &a
	c.AssignedToAt = &now
	return nil
}

// Unassign clears the assignee and moves to unassigned (CASE-FR-011/012).
// Allowed from draft/in_progress only. Auto-unassign uses the same path with
// actor system/sla (BR-4).
func (c *Case) Unassign() error {
	switch c.Status {
	case StatusDraft, StatusInProgress:
		c.Status = StatusUnassigned
		c.AssignedToID = nil
		c.AssignedToAt = nil
		return nil
	case StatusUnassigned:
		return EInvalidTransition("case is already unassigned")
	default:
		return EInvalidTransition("cannot unassign a " + c.Status.String() + " case")
	}
}

// Start moves draft → in_progress (start_work, CASE-FR-010).
func (c *Case) Start() error {
	if c.Status != StatusDraft {
		return EInvalidTransition("start requires a draft case, got " + c.Status.String())
	}
	c.Status = StatusInProgress
	return nil
}

// Resolve moves in_progress → resolved with a disposition (CASE-FR-010/020).
// Guards: an active disposition is required; requires_note enforces a non-empty
// note (AC-5).
func (c *Case) Resolve(disp *Disposition, note string, now time.Time) error {
	if c.Status != StatusInProgress {
		return EInvalidTransition("resolve requires an in_progress case, got " + c.Status.String())
	}
	if disp == nil || !disp.Active {
		return EDispositionRequired()
	}
	if disp.RequiresNote && note == "" {
		return EDispositionNote()
	}
	c.Status = StatusResolved
	c.DispositionID = &disp.ID
	c.ResolutionNote = note
	c.ResolvedAt = &now
	return nil
}

// Reopen moves resolved → in_progress within the reopen window (CASE-FR-014,
// BR-7). The prior disposition is cleared (recorded in the timeline by the
// service as reopened_from).
func (c *Case) Reopen(now time.Time) error {
	if c.Status != StatusResolved {
		return EInvalidTransition("reopen requires a resolved case, got " + c.Status.String())
	}
	if c.ResolvedAt != nil && now.Sub(*c.ResolvedAt) > ReopenWindow {
		return EInvalidTransition("reopen window (30 days) has passed; case may only be closed")
	}
	c.Status = StatusInProgress
	c.DispositionID = nil
	c.ResolutionNote = ""
	c.ResolvedAt = nil
	return nil
}

// Close moves resolved → closed (terminal), recording the archived snapshot
// reference (CASE-FR-006, AC-8).
func (c *Case) Close(snapshotRef string, now time.Time) error {
	if c.Status != StatusResolved {
		return EInvalidTransition("close requires a resolved case, got " + c.Status.String())
	}
	c.Status = StatusClosed
	c.SnapshotRef = snapshotRef
	c.ClosedAt = &now
	return nil
}

// PriorDisposition returns the disposition being cleared on reopen so the
// service can record reopened_from (CASE-FR-014).
func (c *Case) PriorDisposition() *uuid.UUID { return c.DispositionID }
