package store

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/events"
)

// InsertOutbox appends an event to the transactional outbox inside the
// caller's mutation transaction (MASTER-FR-034: never emit before commit).
func InsertOutbox(ctx context.Context, tx pgx.Tx, env events.Envelope) error {
	payload, err := json.Marshal(env.Payload)
	if err != nil {
		return fmt.Errorf("outbox payload: %w", err)
	}
	var viaAgent []byte
	if env.ViaAgent != nil {
		viaAgent, err = json.Marshal(env.ViaAgent)
		if err != nil {
			return fmt.Errorf("outbox via_agent: %w", err)
		}
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO outbox (event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		env.EventID, env.TenantID, env.EventType, env.Actor.Type, env.Actor.ID,
		viaAgent, env.ResourceURN, env.OccurredAt, env.TraceID, payload)
	if err != nil {
		return fmt.Errorf("outbox insert: %w", err)
	}
	return nil
}

// OutboxRow is one unpublished outbox entry.
type OutboxRow struct {
	ID       int64
	Envelope events.Envelope
}

// FetchUnpublished claims up to limit unpublished outbox rows in id order.
func (s *Store) FetchUnpublished(ctx context.Context, limit int) ([]OutboxRow, error) {
	var rows []OutboxRow
	err := s.WithWorker(ctx, func(tx pgx.Tx) error {
		r, err := tx.Query(ctx, `
			SELECT id, event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer r.Close()
		for r.Next() {
			var row OutboxRow
			var viaAgent, payload []byte
			if err := r.Scan(&row.ID, &row.Envelope.EventID, &row.Envelope.TenantID, &row.Envelope.EventType,
				&row.Envelope.Actor.Type, &row.Envelope.Actor.ID, &viaAgent, &row.Envelope.ResourceURN,
				&row.Envelope.OccurredAt, &row.Envelope.TraceID, &payload); err != nil {
				return err
			}
			if len(viaAgent) > 0 {
				var va events.ViaAgent
				if err := json.Unmarshal(viaAgent, &va); err == nil {
					row.Envelope.ViaAgent = &va
				}
			}
			if len(payload) > 0 {
				_ = json.Unmarshal(payload, &row.Envelope.Payload)
			}
			rows = append(rows, row)
		}
		return r.Err()
	})
	return rows, err
}

// MarkPublished stamps outbox rows as published.
func (s *Store) MarkPublished(ctx context.Context, ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	return s.WithWorker(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE outbox SET published_at = $1 WHERE id = ANY($2)`, time.Now().UTC(), ids)
		return err
	})
}

// OutboxEventsByType returns committed outbox envelopes of a type for a
// tenant — used by tests and the audit trail (override events, etc.).
func (s *Store) OutboxEventsByType(ctx context.Context, tenant string, eventType string) ([]events.Envelope, error) {
	var out []events.Envelope
	err := s.WithWorker(ctx, func(tx pgx.Tx) error {
		r, err := tx.Query(ctx, `
			SELECT event_id, tenant_id, event_type, actor_type, actor_id, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE tenant_id = $1 AND event_type = $2 ORDER BY id`, tenant, eventType)
		if err != nil {
			return err
		}
		defer r.Close()
		for r.Next() {
			var env events.Envelope
			var payload []byte
			if err := r.Scan(&env.EventID, &env.TenantID, &env.EventType, &env.Actor.Type, &env.Actor.ID,
				&env.ResourceURN, &env.OccurredAt, &env.TraceID, &payload); err != nil {
				return err
			}
			if len(payload) > 0 {
				_ = json.Unmarshal(payload, &env.Payload)
			}
			out = append(out, env)
		}
		return r.Err()
	})
	return out, err
}
