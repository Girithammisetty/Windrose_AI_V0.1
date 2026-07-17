package store

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

const webhookCols = `id, tenant_id, url, event_types, secrets, active, verified_at, circuit_state, circuit_opened_at, consecutive_failures, created_by, created_at, updated_at`

func scanWebhook(row pgx.Row) (*domain.WebhookEndpoint, error) {
	var e domain.WebhookEndpoint
	var secrets []byte
	err := row.Scan(&e.ID, &e.TenantID, &e.URL, &e.EventTypes, &secrets, &e.Active, &e.VerifiedAt,
		&e.CircuitState, &e.CircuitOpenedAt, &e.ConsecutiveFailures, &e.CreatedBy, &e.CreatedAt, &e.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal(secrets, &e.Secrets)
	return &e, nil
}

// CreateWebhook inserts a webhook endpoint.
func (s *PG) CreateWebhook(ctx context.Context, e *domain.WebhookEndpoint) error {
	return s.withTenant(ctx, e.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO webhook_endpoints (`+webhookCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
			e.ID, e.TenantID, e.URL, e.EventTypes, mustJSON(e.Secrets), e.Active, e.VerifiedAt,
			e.CircuitState, e.CircuitOpenedAt, e.ConsecutiveFailures, e.CreatedBy, e.CreatedAt, e.UpdatedAt)
		return err
	})
}

// GetWebhook fetches one endpoint (RLS-scoped).
func (s *PG) GetWebhook(ctx context.Context, tenant, id uuid.UUID) (*domain.WebhookEndpoint, error) {
	var e *domain.WebhookEndpoint
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var er error
		e, er = scanWebhook(tx.QueryRow(ctx, `SELECT `+webhookCols+` FROM webhook_endpoints WHERE id=$1`, id))
		return er
	})
	return e, err
}

// ListWebhooks returns a tenant's endpoints.
func (s *PG) ListWebhooks(ctx context.Context, tenant uuid.UUID, limit int, cursor *uuid.UUID) ([]*domain.WebhookEndpoint, error) {
	var out []*domain.WebhookEndpoint
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + webhookCols + ` FROM webhook_endpoints WHERE true`
		args := []any{}
		if cursor != nil {
			args = append(args, *cursor)
			q += ` AND id > $` + itoa(len(args))
		}
		args = append(args, limit)
		q += ` ORDER BY id LIMIT $` + itoa(len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			e, err := scanWebhook(rows)
			if err != nil {
				return err
			}
			out = append(out, e)
		}
		return rows.Err()
	})
	return out, err
}

// ActiveWebhooksForEvent returns active, verified endpoints in a tenant that
// subscribe to eventType.
func (s *PG) ActiveWebhooksForEvent(ctx context.Context, tenant uuid.UUID, eventType string) ([]*domain.WebhookEndpoint, error) {
	var out []*domain.WebhookEndpoint
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT `+webhookCols+` FROM webhook_endpoints
			WHERE active AND verified_at IS NOT NULL AND circuit_state <> 'disabled' AND $1 = ANY(event_types)`, eventType)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			e, err := scanWebhook(rows)
			if err != nil {
				return err
			}
			out = append(out, e)
		}
		return rows.Err()
	})
	return out, err
}

// UpdateWebhook persists mutable fields (event_types, active, secrets, circuit,
// verified_at).
func (s *PG) UpdateWebhook(ctx context.Context, e *domain.WebhookEndpoint) error {
	return s.withTenant(ctx, e.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE webhook_endpoints SET url=$2, event_types=$3, secrets=$4, active=$5, verified_at=$6,
				circuit_state=$7, circuit_opened_at=$8, consecutive_failures=$9, updated_at=now()
			WHERE id=$1`,
			e.ID, e.URL, e.EventTypes, mustJSON(e.Secrets), e.Active, e.VerifiedAt,
			e.CircuitState, e.CircuitOpenedAt, e.ConsecutiveFailures)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// DeleteWebhook removes an endpoint.
func (s *PG) DeleteWebhook(ctx context.Context, tenant, id uuid.UUID) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `DELETE FROM webhook_endpoints WHERE id=$1`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// UpdateCircuitByID applies a circuit-state transition under the platform role
// (called by the webhook retry/circuit worker across tenants).
func (s *PG) UpdateCircuitByID(ctx context.Context, tenant, id uuid.UUID, state string, openedAt *time.Time, failures int) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE webhook_endpoints SET circuit_state=$2, circuit_opened_at=$3, consecutive_failures=$4, updated_at=now() WHERE id=$1 AND tenant_id=$5`,
			id, state, openedAt, failures, tenant)
		return err
	})
}
