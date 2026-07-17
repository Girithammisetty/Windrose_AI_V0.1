// Package store is the tool-plane persistence layer: a pgx-backed Postgres store
// with Postgres RLS for tenant isolation (MASTER-FR-001). Platform-scoped
// catalog writes run under an app.role='platform' session; tenant-scoped
// enablement/invocation writes run under app.tenant_id set from the verified JWT.
// pgvector backs real semantic discovery (TPL-FR-020). The concrete *PG satisfies
// the small ports the enforce pipeline and API depend on.
package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/events"
)

// ErrNotFound maps to 404; cross-tenant reads surface as this via RLS.
var ErrNotFound = errors.New("not found")

// ErrConflict maps to 409 (version/tool already exists).
var ErrConflict = errors.New("conflict")

// PG is the pgx-backed store.
type PG struct {
	pool *pgxpool.Pool
}

// NewPG builds a store over a pgx pool.
func NewPG(pool *pgxpool.Pool) *PG { return &PG{pool: pool} }

// Pool exposes the underlying pool (readiness probe, tests).
func (s *PG) Pool() *pgxpool.Pool { return s.pool }

// Ping checks connectivity (readyz).
func (s *PG) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// withTenant runs fn inside a tx with app.tenant_id set (RLS, MASTER-FR-001).
func (s *PG) withTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return fmt.Errorf("set tenant context: %w", err)
		}
		return fn(tx)
	})
}

// withPlatform runs fn inside a tx with app.role='platform' for catalog admin.
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

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte("null")
	}
	return b
}

// vectorLiteral formats a float slice as a pgvector literal '[a,b,c]'.
func vectorLiteral(v []float32) string {
	if len(v) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteByte('[')
	for i, f := range v {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "%g", f)
	}
	b.WriteByte(']')
	return b.String()
}

func insertOutboxTx(ctx context.Context, tx pgx.Tx, envs []events.Envelope) error {
	for _, env := range envs {
		var viaAgent []byte
		if env.ViaAgent != nil {
			viaAgent = mustJSON(env.ViaAgent)
		}
		topic := env.Topic
		if topic == "" {
			topic = events.TopicToolEvents
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO outbox (event_id, tenant_id, topic, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
			ON CONFLICT (event_id) DO NOTHING`,
			env.EventID, env.TenantID, topic, env.EventType, env.Actor.Type, env.Actor.ID,
			viaAgent, env.ResourceURN, env.OccurredAt, env.TraceID, mustJSON(env.Payload)); err != nil {
			return fmt.Errorf("outbox insert: %w", err)
		}
	}
	return nil
}

// ---- Outbox relay surface (MASTER-FR-034) -----------------------------------

// FetchUnpublished returns oldest unpublished outbox rows (platform session
// reads across tenants for the relay).
func (s *PG) FetchUnpublished(ctx context.Context, limit int) ([]events.OutboxRow, error) {
	var out []events.OutboxRow
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, topic, event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var row events.OutboxRow
			var viaAgent, payload []byte
			if err := rows.Scan(&row.ID, &row.Topic, &row.Envelope.EventID, &row.Envelope.TenantID,
				&row.Envelope.EventType, &row.Envelope.Actor.Type, &row.Envelope.Actor.ID, &viaAgent,
				&row.Envelope.ResourceURN, &row.Envelope.OccurredAt, &row.Envelope.TraceID, &payload); err != nil {
				return err
			}
			if len(viaAgent) > 0 {
				var va domain.ViaAgent
				if json.Unmarshal(viaAgent, &va) == nil {
					row.Envelope.ViaAgent = &va
				}
			}
			_ = json.Unmarshal(payload, &row.Envelope.Payload)
			out = append(out, row)
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

// OutboxByType returns emitted envelopes of a type for a tenant (test assertions).
func (s *PG) OutboxByType(ctx context.Context, tenant uuid.UUID, eventType string) ([]events.Envelope, error) {
	var out []events.Envelope
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT event_id, tenant_id, event_type, actor_type, actor_id, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE tenant_id = $1 AND event_type = $2 ORDER BY id`, tenant, eventType)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var env events.Envelope
			var payload []byte
			if err := rows.Scan(&env.EventID, &env.TenantID, &env.EventType, &env.Actor.Type, &env.Actor.ID,
				&env.ResourceURN, &env.OccurredAt, &env.TraceID, &payload); err != nil {
				return err
			}
			_ = json.Unmarshal(payload, &env.Payload)
			out = append(out, env)
		}
		return rows.Err()
	})
	return out, err
}
