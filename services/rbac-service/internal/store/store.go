// Package store is the pgx-backed persistence layer. Every tenant-scoped
// operation runs inside a transaction that first sets the app.tenant_id GUC,
// so Postgres row-level security enforces isolation below the application
// (MASTER-FR-001). Worker paths (outbox relay, projection recompute claim)
// set app.worker instead, which the RLS policies on the two internal queue
// tables accept.
package store

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Store wraps the connection pool.
type Store struct {
	pool *pgxpool.Pool
}

func New(pool *pgxpool.Pool) *Store { return &Store{pool: pool} }

func (s *Store) Pool() *pgxpool.Pool { return s.pool }

// WithTenant runs fn in a transaction with RLS pinned to the tenant.
func (s *Store) WithTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		// set_config with is_local=true scopes the GUC to this transaction.
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return fmt.Errorf("set tenant context: %w", err)
		}
		return fn(tx)
	})
}

// WithWorker runs fn in a transaction flagged as worker context (cross-tenant
// access to the internal queue tables only).
func (s *Store) WithWorker(ctx context.Context, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.worker', 'on', true)`); err != nil {
			return fmt.Errorf("set worker context: %w", err)
		}
		return fn(tx)
	})
}

// Ping checks connectivity (readyz).
func (s *Store) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// ---- Error model -----------------------------------------------------------

// ErrNotFound maps to 404 NOT_FOUND. Cross-tenant access surfaces as this
// error because RLS hides the rows entirely (MASTER-FR-003).
var ErrNotFound = errors.New("not found")

// ConflictError maps to 409 with a stable machine code.
type ConflictError struct {
	Code    string
	Message string
}

func (e *ConflictError) Error() string { return e.Code + ": " + e.Message }

// ValidationError maps to 422 (semantic) or 400 (shape) with a stable code.
type ValidationError struct {
	Code    string
	Message string
	Details any
}

func (e *ValidationError) Error() string { return e.Code + ": " + e.Message }

// Stable error codes (MASTER-FR-024 + BRD-specific).
const (
	CodeConflict            = "CONFLICT"
	CodeWorkspaceArchived   = "WORKSPACE_ARCHIVED"
	CodeLastAdmin           = "LAST_ADMIN"
	CodeRoleInUse           = "ROLE_IN_USE"
	CodeGroupNotInWorkspace = "GROUP_NOT_IN_WORKSPACE"
	CodeValidationFailed    = "VALIDATION_FAILED"
	CodeSystemImmutable     = "SYSTEM_IMMUTABLE"
)

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

func isFKViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23503"
}

// NewID returns a UUIDv7 (MASTER-FR-021: time-ordered ids).
func NewID() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.New()
	}
	return id
}

// Cursor pagination helpers (MASTER-FR-022): cursor is the last row's uuidv7
// id (time-ordered), passed opaque to clients.

const (
	DefaultLimit = 50
	MaxLimit     = 200
)

func ClampLimit(limit int) int {
	if limit <= 0 {
		return DefaultLimit
	}
	if limit > MaxLimit {
		return MaxLimit
	}
	return limit
}

// Page is the generic pagination result.
type Page[T any] struct {
	Data       []T
	NextCursor string
	HasMore    bool
}

// nowUTC exists for test injection points; wall clock elsewhere.
func nowUTC() time.Time { return time.Now().UTC() }
