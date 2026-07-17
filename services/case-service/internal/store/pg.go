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

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
)

// PG is the pgx-backed store.
type PG struct {
	pool *pgxpool.Pool
}

func NewPG(pool *pgxpool.Pool) *PG { return &PG{pool: pool} }

func (s *PG) Pool() *pgxpool.Pool { return s.pool }

func (s *PG) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// withTenant runs fn in a tx with app.tenant_id set from the verified JWT so
// RLS constrains every statement (MASTER-FR-001).
func (s *PG) withTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return fmt.Errorf("set tenant context: %w", err)
		}
		return fn(tx)
	})
}

// withPlatform runs fn under the cross-tenant platform role (outbox relay, SLA
// sweep). Only these paths read across tenants.
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

func constraintName(err error) string {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		return pgErr.ConstraintName
	}
	return ""
}

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte("null")
	}
	return b
}

// insertOutboxTx writes envelopes to the outbox in the same tx as the state
// change (MASTER-FR-034).
func insertOutboxTx(ctx context.Context, tx pgx.Tx, envs []events.Envelope) error {
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

// insertActivitiesTx appends timeline entries (CASE-FR-025).
func insertActivitiesTx(ctx context.Context, tx pgx.Tx, tenant uuid.UUID, acts []domain.Activity) error {
	for _, a := range acts {
		var viaAgent, oldV, newV []byte
		if a.ViaAgent != nil {
			viaAgent = mustJSON(a.ViaAgent)
		}
		if a.OldValue != nil {
			oldV = mustJSON(a.OldValue)
		}
		if a.NewValue != nil {
			newV = mustJSON(a.NewValue)
		}
		var proposalURN *string
		if a.ProposalURN != "" {
			p := a.ProposalURN
			proposalURN = &p
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO case_events (id, tenant_id, case_id, event_type, actor_type, actor_id, via_agent, proposal_urn, old_value, new_value, occurred_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
			a.ID, tenant, a.CaseID, a.EventType, a.ActorType, a.ActorID, viaAgent, proposalURN, oldV, newV, a.OccurredAt); err != nil {
			return fmt.Errorf("activity insert: %w", err)
		}
	}
	return nil
}

// applyTimerPlanTx (re)installs or cancels SLA timers atomically (CASE-FR-012).
func applyTimerPlanTx(ctx context.Context, tx pgx.Tx, tenant, caseID uuid.UUID, version int, plan TimerPlan) error {
	if plan.Cancel {
		if _, err := tx.Exec(ctx, `UPDATE sla_timers SET status='cancelled', updated_at=now() WHERE case_id=$1 AND status='pending'`, caseID); err != nil {
			return err
		}
	}
	for _, t := range plan.Set {
		if _, err := tx.Exec(ctx, `
			INSERT INTO sla_timers (tenant_id, case_id, kind, fire_at, case_version, status)
			VALUES ($1,$2,$3,$4,$5,'pending')
			ON CONFLICT (case_id, kind) DO UPDATE SET fire_at=$4, case_version=$5, status='pending', updated_at=now()`,
			tenant, caseID, t.Kind, t.FireAt, version); err != nil {
			return err
		}
	}
	return nil
}

// ---- Outbox relay port (MASTER-FR-034) --------------------------------------

func (s *PG) FetchUnpublished(ctx context.Context, limit int) ([]events.OutboxRow, error) {
	var out []events.OutboxRow
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var row events.OutboxRow
			var viaAgent, payload []byte
			if err := rows.Scan(&row.ID, &row.Envelope.EventID, &row.Envelope.TenantID, &row.Envelope.EventType,
				&row.Envelope.Actor.Type, &row.Envelope.Actor.ID, &viaAgent, &row.Envelope.ResourceURN,
				&row.Envelope.OccurredAt, &row.Envelope.TraceID, &payload); err != nil {
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

func (s *PG) MarkPublished(ctx context.Context, ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE outbox SET published_at = now() WHERE id = ANY($1)`, ids)
		return err
	})
}

// InsertAudit writes a single audit/security envelope to the outbox.
func (s *PG) InsertAudit(ctx context.Context, env events.Envelope) error {
	return s.withTenant(ctx, env.TenantID, func(tx pgx.Tx) error {
		return insertOutboxTx(ctx, tx, []events.Envelope{env})
	})
}

// OutboxEventsByType returns emitted envelopes of a type (test assertions).
func (s *PG) OutboxEventsByType(ctx context.Context, tenant uuid.UUID, eventType string) ([]events.Envelope, error) {
	var out []events.Envelope
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE tenant_id=$1 AND event_type=$2 ORDER BY id`, tenant, eventType)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var env events.Envelope
			var payload, viaAgent []byte
			if err := rows.Scan(&env.EventID, &env.TenantID, &env.EventType, &env.Actor.Type, &env.Actor.ID,
				&viaAgent, &env.ResourceURN, &env.OccurredAt, &env.TraceID, &payload); err != nil {
				return err
			}
			if len(viaAgent) > 0 {
				var va domain.ViaAgent
				if json.Unmarshal(viaAgent, &va) == nil {
					env.ViaAgent = &va
				}
			}
			_ = json.Unmarshal(payload, &env.Payload)
			out = append(out, env)
		}
		return rows.Err()
	})
	return out, err
}

// ---- Idempotency (MASTER-FR-025) --------------------------------------------

// IdempotencyRecord is a stored POST response for replay.
type IdempotencyRecord struct {
	Status   int
	Response []byte
}

func (s *PG) GetIdempotency(ctx context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error) {
	var rec IdempotencyRecord
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT status, response FROM idempotency_keys
			WHERE tenant_id=$1 AND key=$2 AND created_at > now() - interval '24 hours'`, tenant, key).
			Scan(&rec.Status, &rec.Response)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &rec, nil
}

func (s *PG) PutIdempotency(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO idempotency_keys (tenant_id, key, method, path, status, response)
			VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (tenant_id, key) DO NOTHING`, tenant, key, method, path, status, response)
		return err
	})
}
