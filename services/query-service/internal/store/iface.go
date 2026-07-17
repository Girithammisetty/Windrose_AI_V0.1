// Package store is the persistence port with two implementations: the
// pgx-backed Postgres store (RLS-enforced, MASTER-FR-001) and an in-memory
// fake with the same tenant-isolation semantics for the unit tier
// (CONVENTIONS: testing tiers — a unit-tier variant with an in-memory
// policy fake must exist).
package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/events"
)

// ErrNotFound maps to 404 NOT_FOUND; cross-tenant access surfaces as this
// error because RLS (or the fake's policy) hides the rows (MASTER-FR-003).
var ErrNotFound = errors.New("not found")

// ErrNameConflict maps to 409 CONFLICT on the per-workspace unique name.
var ErrNameConflict = errors.New("name already in use in workspace")

// ErrStaleVersion maps to 409 on If-Match mismatch (BR-11).
var ErrStaleVersion = errors.New("stale version (If-Match mismatch)")

// Pagination bounds (MASTER-FR-022).
const (
	DefaultLimit = 50
	MaxLimit     = 200
)

// ClampLimit applies the master pagination bounds.
func ClampLimit(limit int) int {
	if limit <= 0 {
		return DefaultLimit
	}
	if limit > MaxLimit {
		return MaxLimit
	}
	return limit
}

// Page is a generic cursor page (MASTER-FR-022).
type Page[T any] struct {
	Data       []T
	NextCursor string
	HasMore    bool
}

// SavedQueryFilter filters list endpoints (indexed fields only,
// MASTER-FR-023).
type SavedQueryFilter struct {
	WorkspaceID *uuid.UUID
	Limit       int
	Cursor      string
}

// ExecutionFilter filters query history (QRY-FR-080).
type ExecutionFilter struct {
	Status       string
	User         string
	SavedQueryID *uuid.UUID
	Since        *time.Time
	SortByCost   bool // ?sort=-cost
	Limit        int
	Cursor       string
}

// IdempotencyRecord is a stored POST response (MASTER-FR-025).
type IdempotencyRecord struct {
	Status   int
	Response []byte
}

// QueryStat is one row of the TA/OP aggregate view (QRY-FR-081).
type QueryStat struct {
	SQLFingerprint string `json:"sql_fingerprint"`
	Executions     int64  `json:"executions"`
	TotalScanBytes int64  `json:"total_scan_bytes"`
	Failures       int64  `json:"failures"`
	TopUser        string `json:"top_user,omitempty"`
}

// Store is the persistence port.
type Store interface {
	// Saved queries (QRY-FR-001).
	CreateSavedQuery(ctx context.Context, op domain.Op, sq *domain.SavedQuery, v *domain.SavedQueryVersion, envs []events.Envelope) error
	GetSavedQuery(ctx context.Context, tenant, id uuid.UUID) (*domain.SavedQuery, *domain.SavedQueryVersion, error)
	GetVersion(ctx context.Context, tenant, id uuid.UUID, versionNo int) (*domain.SavedQueryVersion, error)
	ListSavedQueries(ctx context.Context, tenant uuid.UUID, f SavedQueryFilter) (Page[*domain.SavedQuery], error)
	ListVersions(ctx context.Context, tenant, id uuid.UUID, limit int, cursor string) (Page[*domain.SavedQueryVersion], error)
	// UpdateSavedQuery bumps the version under If-Match (BR-11: version
	// numbers never fork).
	UpdateSavedQuery(ctx context.Context, op domain.Op, sq *domain.SavedQuery, v *domain.SavedQueryVersion, expectVersion int, envs []events.Envelope) error
	SoftDeleteSavedQuery(ctx context.Context, op domain.Op, id uuid.UUID, envs []events.Envelope) error

	// Executions (QRY-FR-080).
	InsertExecution(ctx context.Context, op domain.Op, e *domain.Execution, envs []events.Envelope) error
	// UpdateExecution applies a read-modify-write under the tenant's RLS
	// context; apply returns the outbox envelopes to attach atomically
	// (MASTER-FR-034).
	UpdateExecution(ctx context.Context, tenant, id uuid.UUID, apply func(e *domain.Execution) ([]events.Envelope, error)) error
	GetExecution(ctx context.Context, tenant, id uuid.UUID) (*domain.Execution, error)
	ListExecutions(ctx context.Context, tenant uuid.UUID, f ExecutionFilter) (Page[*domain.Execution], error)
	// FindCacheHit locates a reusable succeeded execution (QRY-FR-046).
	FindCacheHit(ctx context.Context, tenant uuid.UUID, cacheKey string, since time.Time) (*domain.Execution, error)
	// ActiveExecutions returns queued+running rows (tenant.suspended, §6).
	ActiveExecutions(ctx context.Context, tenant uuid.UUID) ([]*domain.Execution, error)
	// QueryStats aggregates history for TA/OP (QRY-FR-081).
	QueryStats(ctx context.Context, tenant uuid.UUID, since time.Time, limit int) ([]QueryStat, error)

	// Tenant limits (QRY-FR-042).
	GetTenantLimits(ctx context.Context, tenant uuid.UUID) (*domain.TenantLimits, error)
	PutTenantLimits(ctx context.Context, op domain.Op, l *domain.TenantLimits) error

	// Idempotency (MASTER-FR-025).
	GetIdempotency(ctx context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error)
	PutIdempotency(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error

	// Audit-only envelopes outside a mutation tx (denials, MASTER-FR-040).
	InsertAudit(ctx context.Context, env events.Envelope) error

	// Outbox relay surface (MASTER-FR-034).
	FetchUnpublished(ctx context.Context, limit int) ([]events.OutboxRow, error)
	MarkPublished(ctx context.Context, ids []int64) error
	OutboxEventsByType(ctx context.Context, tenant uuid.UUID, eventType string) ([]events.Envelope, error)

	Ping(ctx context.Context) error
}
