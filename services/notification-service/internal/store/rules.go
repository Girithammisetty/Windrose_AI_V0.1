package store

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

const ruleCols = `id, tenant_id, scope, subject_type, subject_id, event_types, resource_filter, channels, digest_enabled, digest_window, active, created_by, created_at, updated_at, deleted_at`

func scanRule(row pgx.Row) (*domain.SubscriptionRule, error) {
	var r domain.SubscriptionRule
	var filter []byte
	err := row.Scan(&r.ID, &r.TenantID, &r.Scope, &r.SubjectType, &r.SubjectID, &r.EventTypes,
		&filter, &r.Channels, &r.DigestEnabled, &r.DigestWindow, &r.Active, &r.CreatedBy,
		&r.CreatedAt, &r.UpdatedAt, &r.DeletedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal(filter, &r.ResourceFtr)
	return &r, nil
}

// CreateRule inserts a subscription rule (NOTIF-FR-010).
func (s *PG) CreateRule(ctx context.Context, r *domain.SubscriptionRule) error {
	return s.withTenant(ctx, r.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO subscription_rules (`+ruleCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
			r.ID, r.TenantID, r.Scope, r.SubjectType, r.SubjectID, r.EventTypes, mustJSON(r.ResourceFtr),
			r.Channels, r.DigestEnabled, r.DigestWindow, r.Active, r.CreatedBy, r.CreatedAt, r.UpdatedAt, r.DeletedAt)
		return err
	})
}

// GetRule fetches one active rule (RLS-scoped; cross-tenant → ErrNotFound).
func (s *PG) GetRule(ctx context.Context, tenant, id uuid.UUID) (*domain.SubscriptionRule, error) {
	var r *domain.SubscriptionRule
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var e error
		r, e = scanRule(tx.QueryRow(ctx, `SELECT `+ruleCols+` FROM subscription_rules WHERE id=$1 AND deleted_at IS NULL`, id))
		return e
	})
	return r, err
}

// ListRules returns active rules for a tenant, cursor-paginated.
func (s *PG) ListRules(ctx context.Context, tenant uuid.UUID, limit int, cursor *uuid.UUID) ([]*domain.SubscriptionRule, error) {
	var out []*domain.SubscriptionRule
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + ruleCols + ` FROM subscription_rules WHERE deleted_at IS NULL`
		args := []any{}
		if cursor != nil {
			args = append(args, *cursor)
			q += ` AND id > $1`
		}
		args = append(args, limit)
		q += ` ORDER BY id LIMIT $` + itoa(len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			r, err := scanRule(rows)
			if err != nil {
				return err
			}
			out = append(out, r)
		}
		return rows.Err()
	})
	return out, err
}

// ActiveRulesForEvent returns active rules in a tenant whose event_types match
// eventType (exact or wildcard prefix like case.*). Matching is finished in Go.
func (s *PG) ActiveRulesForEvent(ctx context.Context, tenant uuid.UUID) ([]*domain.SubscriptionRule, error) {
	var out []*domain.SubscriptionRule
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT `+ruleCols+` FROM subscription_rules WHERE active AND deleted_at IS NULL`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			r, err := scanRule(rows)
			if err != nil {
				return err
			}
			out = append(out, r)
		}
		return rows.Err()
	})
	return out, err
}

// UpdateRule replaces mutable fields of a rule.
func (s *PG) UpdateRule(ctx context.Context, r *domain.SubscriptionRule) error {
	return s.withTenant(ctx, r.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE subscription_rules SET event_types=$2, resource_filter=$3, channels=$4,
				digest_enabled=$5, digest_window=$6, active=$7, updated_at=now()
			WHERE id=$1 AND deleted_at IS NULL`,
			r.ID, r.EventTypes, mustJSON(r.ResourceFtr), r.Channels, r.DigestEnabled, r.DigestWindow, r.Active)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// DeleteRule soft-deletes a rule.
func (s *PG) DeleteRule(ctx context.Context, tenant, id uuid.UUID) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE subscription_rules SET deleted_at=now() WHERE id=$1 AND deleted_at IS NULL`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}
