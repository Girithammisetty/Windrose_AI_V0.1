// Package store is the pgx-backed persistence layer. Every tenant-scoped
// operation runs in a transaction that first sets app.tenant_id so Postgres
// RLS enforces isolation below the application (MASTER-FR-001, AC-13). Postgres
// is the source of truth; the OpenSearch projection is eventual (CASE-FR-041).
package store

import (
	"errors"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
)

// Store sentinels mapped onto the error catalog by the api layer.
var (
	ErrNotFound      = errors.New("not found")
	ErrDedupConflict = errors.New("dedup conflict")
	ErrStaleVersion  = errors.New("stale case version")
	ErrCodeConflict  = errors.New("code already in use")
	ErrFieldInUse    = errors.New("field in use")
	ErrCaseLimit     = errors.New("case limit exceeded")
)

// Page is a cursor page (MASTER-FR-022).
type Page[T any] struct {
	Data       []T
	NextCursor string
	HasMore    bool
}

// Timer is one SLA timer to (re)set (CASE-FR-012/013).
type Timer struct {
	Kind   string // "warn" | "due"
	FireAt time.Time
}

// TimerPlan describes how a mutation changes the case's SLA timers. Cancel
// removes all pending timers; Set (re)installs timers keyed to the post-
// mutation case_version so stale fires are discarded (BR-4).
type TimerPlan struct {
	Cancel bool
	Set    []Timer
}

// Mutation is the result of applying a transition to a case in a store tx: the
// events to emit (outbox), the timeline activities to append, and the SLA
// timer plan. The store bumps case_version and commits all of it atomically.
type Mutation struct {
	Events     []events.Envelope
	Activities []domain.Activity
	Timers     TimerPlan
}

// CaseInput is one row to turn into a case (CASE-FR-002). The service has
// already validated + truncated the projection and validated custom fields.
type CaseInput struct {
	RowPK             string
	DisplayProjection map[string]string
	ProjTruncated     bool
	DedupKey          *string
}

// DedupResult reports a row that merged into an existing open case (CASE-FR-005).
type DedupResult struct {
	Case  *domain.Case
	RowPK string
}

// CommentUpsert carries a new/edited comment.
type CommentUpsert struct {
	ID       uuid.UUID
	CaseID   uuid.UUID
	AuthorID string
	Body     string
}

// SLADueTimer is one fired-eligible timer returned by the sweep (CASE-FR-012).
type SLADueTimer struct {
	TenantID    uuid.UUID
	CaseID      uuid.UUID
	Kind        string
	CaseVersion int
}

// ClampLimit applies the pagination bounds (MASTER-FR-022).
func ClampLimit(n int) int {
	if n <= 0 {
		return 50
	}
	if n > 200 {
		return 200
	}
	return n
}
