// Package store is the pgx-backed persistence layer. Every tenant-scoped
// operation runs in a transaction that first sets app.tenant_id so Postgres
// RLS enforces isolation below the application (MASTER-FR-001). Cross-tenant
// sweeps (outbox relay, retry/digest workers) set app.role=platform instead.
package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	gcevent "github.com/windrose-ai/go-common/event"
)

// Sentinel errors mapped onto the API error catalog by the api package.
var (
	ErrNotFound = errors.New("not found")
	ErrConflict = errors.New("conflict")
)

// PG is the Postgres store.
type PG struct {
	pool *pgxpool.Pool
}

// NewPG builds a store over a pgx pool.
func NewPG(pool *pgxpool.Pool) *PG { return &PG{pool: pool} }

// Pool exposes the underlying pool (tests).
func (s *PG) Pool() *pgxpool.Pool { return s.pool }

// Ping checks connectivity (readyz).
func (s *PG) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// withTenant runs fn in a tx with RLS pinned to tenant (MASTER-FR-001).
func (s *PG) withTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return fmt.Errorf("set tenant context: %w", err)
		}
		return fn(tx)
	})
}

// withPlatform runs fn under the cross-tenant platform role (relay + sweepers).
func (s *PG) withPlatform(ctx context.Context, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.role', 'platform', true)`); err != nil {
			return fmt.Errorf("set platform context: %w", err)
		}
		return fn(tx)
	})
}

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

func itoa(n int) string { return fmt.Sprintf("%d", n) }

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte("null")
	}
	return b
}

// ---- Outbox (MASTER-FR-034) --------------------------------------------------

// insertOutboxTx writes emitted events to the outbox in the mutation's tx.
func insertOutboxTx(ctx context.Context, tx pgx.Tx, envs []gcevent.Envelope) error {
	for _, env := range envs {
		var viaAgent []byte
		if env.ViaAgent != nil {
			viaAgent = mustJSON(env.ViaAgent)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO outbox (event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
			ON CONFLICT (event_id) DO NOTHING`,
			env.EventID, env.TenantID, env.EventType, env.Actor.Type, env.Actor.ID,
			viaAgent, env.ResourceURN, env.OccurredAt, env.TraceID, mustJSON(env.Payload)); err != nil {
			return fmt.Errorf("outbox insert: %w", err)
		}
	}
	return nil
}

// EmitAudit writes a single audit/ops event to the outbox (denials, cross-tenant).
func (s *PG) EmitAudit(ctx context.Context, env gcevent.Envelope) error {
	return s.withTenant(ctx, env.TenantID, func(tx pgx.Tx) error {
		return insertOutboxTx(ctx, tx, []gcevent.Envelope{env})
	})
}

// OutboxRow is one unpublished row for the go-common outbox.Relay.
type OutboxRow struct {
	ID       int64
	Envelope gcevent.Envelope
}

// FetchUnpublished returns unpublished outbox rows oldest-first
// (outbox.Source). Runs under the platform role to read across tenants.
func (s *PG) FetchUnpublished(ctx context.Context, limit int) ([]OutboxRow, error) {
	var out []OutboxRow
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var r OutboxRow
			var viaAgent, payload []byte
			if err := rows.Scan(&r.ID, &r.Envelope.EventID, &r.Envelope.TenantID, &r.Envelope.EventType,
				&r.Envelope.Actor.Type, &r.Envelope.Actor.ID, &viaAgent, &r.Envelope.ResourceURN,
				&r.Envelope.OccurredAt, &r.Envelope.TraceID, &payload); err != nil {
				return err
			}
			if len(viaAgent) > 0 {
				var va gcevent.ViaAgent
				if json.Unmarshal(viaAgent, &va) == nil {
					r.Envelope.ViaAgent = &va
				}
			}
			_ = json.Unmarshal(payload, &r.Envelope.Payload)
			out = append(out, r)
		}
		return rows.Err()
	})
	return out, err
}

// MarkPublished marks rows published after a successful Kafka publish.
func (s *PG) MarkPublished(ctx context.Context, ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE outbox SET published_at = now() WHERE id = ANY($1)`, ids)
		return err
	})
}

// ---- Idempotency (MASTER-FR-025) ---------------------------------------------

// IdempotencyRecord is a cached POST response.
type IdempotencyRecord struct {
	Status   int
	Response []byte
}

// GetIdempotency returns a cached response for (tenant, key) within 24h.
func (s *PG) GetIdempotency(ctx context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error) {
	var rec IdempotencyRecord
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT status, response FROM idempotency_keys
			WHERE tenant_id = $1 AND key = $2 AND created_at > now() - interval '24 hours'`,
			tenant, key).Scan(&rec.Status, &rec.Response)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &rec, nil
}

// PutIdempotency stores a response for replay.
func (s *PG) PutIdempotency(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO idempotency_keys (tenant_id, key, method, path, status, response)
			VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id, key) DO NOTHING`,
			tenant, key, method, path, status, response)
		return err
	})
}
