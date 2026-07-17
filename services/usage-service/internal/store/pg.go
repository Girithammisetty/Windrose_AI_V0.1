// Package store is the pgx-backed persistence layer. Every tenant-scoped
// operation runs inside a transaction that first sets the app.tenant_id GUC so
// Postgres RLS enforces isolation below the application (MASTER-FR-001).
// Platform-operator operations set app.role='platform' (audited bypass).
package store

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
)

// PG is the pgx-backed store.
type PG struct {
	pool *pgxpool.Pool
}

// NewPG wraps a pool.
func NewPG(pool *pgxpool.Pool) *PG { return &PG{pool: pool} }

// Pool exposes the underlying pool (readiness probes / admin jobs).
func (s *PG) Pool() *pgxpool.Pool { return s.pool }

// Ping checks connectivity (readyz).
func (s *PG) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// withTenant runs fn in a tx with app.tenant_id pinned (RLS, MASTER-FR-001).
func (s *PG) withTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return fmt.Errorf("set tenant context: %w", err)
		}
		return fn(tx)
	})
}

// withPlatform runs fn in a tx with app.role='platform' (cross-tenant admin
// paths: outbox relay, reconciliation, rate cards — audited).
func (s *PG) withPlatform(ctx context.Context, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.role', 'platform', true)`); err != nil {
			return fmt.Errorf("set platform context: %w", err)
		}
		return fn(tx)
	})
}

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte("null")
	}
	return b
}

// insertOutbox writes one event to the transactional outbox within tx
// (MASTER-FR-034: never emit before commit).
func insertOutbox(ctx context.Context, tx pgx.Tx, env events.Envelope) error {
	var viaAgent []byte
	if env.ViaAgent != nil {
		viaAgent = mustJSON(env.ViaAgent)
	}
	_, err := tx.Exec(ctx, `
		INSERT INTO outbox (event_id, tenant_id, event_type, actor_type, actor_id,
		                    via_agent, resource_urn, occurred_at, trace_id, payload)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		ON CONFLICT (event_id) DO NOTHING`,
		env.EventID, env.TenantID, env.EventType, env.Actor.Type, env.Actor.ID,
		viaAgent, env.ResourceURN, env.OccurredAt, env.TraceID, mustJSON(env.Payload))
	return err
}

// FetchUnpublished drains committed outbox rows (relay; platform scope so it
// sees every tenant's rows — MASTER-FR-034).
func (s *PG) FetchUnpublished(ctx context.Context, limit int) ([]events.OutboxRow, error) {
	var out []events.OutboxRow
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, event_id, tenant_id, event_type, actor_type, actor_id,
			       via_agent, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var (
				id       int64
				env      events.Envelope
				viaAgent []byte
				payload  []byte
			)
			if err := rows.Scan(&id, &env.EventID, &env.TenantID, &env.EventType,
				&env.Actor.Type, &env.Actor.ID, &viaAgent, &env.ResourceURN,
				&env.OccurredAt, &env.TraceID, &payload); err != nil {
				return err
			}
			if len(viaAgent) > 0 {
				var va domain.ViaAgent
				if json.Unmarshal(viaAgent, &va) == nil {
					env.ViaAgent = &va
				}
			}
			if len(payload) > 0 {
				_ = json.Unmarshal(payload, &env.Payload)
			}
			out = append(out, events.OutboxRow{ID: id, Envelope: env})
		}
		return rows.Err()
	})
	return out, err
}

// MarkPublished marks relayed rows published.
func (s *PG) MarkPublished(ctx context.Context, ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE outbox SET published_at = now() WHERE id = ANY($1)`, ids)
		return err
	})
}

// EmitEvent writes a single event to the outbox under the caller's tenant
// scope (audit / security events from the API layer, MASTER-FR-003/040).
func (s *PG) EmitEvent(ctx context.Context, env events.Envelope) error {
	return s.withTenant(ctx, env.TenantID, func(tx pgx.Tx) error {
		return insertOutbox(ctx, tx, env)
	})
}

// SeedMeters upserts the seeded meter catalog (USG-FR-001), idempotent.
func (s *PG) SeedMeters(ctx context.Context) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		for _, m := range domain.Catalog() {
			if _, err := tx.Exec(ctx, `
				INSERT INTO meters (meter_key, unit, aggregation, description, dimensions, deprecated)
				VALUES ($1,$2,$3,$4,$5,$6)
				ON CONFLICT (meter_key) DO UPDATE SET
				  unit=EXCLUDED.unit, aggregation=EXCLUDED.aggregation,
				  description=EXCLUDED.description, dimensions=EXCLUDED.dimensions,
				  updated_at=now()`,
				m.MeterKey, m.Unit, m.Aggregation, m.Description, m.Dimensions, m.Deprecated); err != nil {
				return err
			}
		}
		return nil
	})
}

// ListMeters returns the catalog (USG-FR-003).
func (s *PG) ListMeters(ctx context.Context) ([]domain.Meter, error) {
	var out []domain.Meter
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT meter_key, unit, aggregation, description, dimensions, deprecated
			FROM meters ORDER BY meter_key`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var m domain.Meter
			if err := rows.Scan(&m.MeterKey, &m.Unit, &m.Aggregation, &m.Description, &m.Dimensions, &m.Deprecated); err != nil {
				return err
			}
			out = append(out, m)
		}
		return rows.Err()
	})
	return out, err
}
