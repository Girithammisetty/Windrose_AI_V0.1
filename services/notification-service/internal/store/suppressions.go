package store

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// IsSuppressed reports whether an email hash is on the active suppression list
// (NOTIF-FR-021, AC-10).
func (s *PG) IsSuppressed(ctx context.Context, tenant uuid.UUID, emailHash string) (bool, error) {
	var ok bool
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM suppressions WHERE email_hash=$1 AND cleared_at IS NULL)`, emailHash).Scan(&ok)
	})
	return ok, err
}

// AddSuppression inserts a suppression (bounce|complaint|manual). Idempotent per
// active (tenant, email_hash).
func (s *PG) AddSuppression(ctx context.Context, tenant uuid.UUID, emailHash, reason string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM suppressions WHERE email_hash=$1 AND cleared_at IS NULL)`, emailHash).Scan(&exists); err != nil {
			return err
		}
		if exists {
			return nil
		}
		_, err := tx.Exec(ctx, `INSERT INTO suppressions (id, tenant_id, email_hash, reason) VALUES ($1,$2,$3,$4)`,
			domain.NewID(), tenant, emailHash, reason)
		return err
	})
}

// ListSuppressions returns active suppressions for a tenant.
func (s *PG) ListSuppressions(ctx context.Context, tenant uuid.UUID, limit int) ([]*domain.Suppression, error) {
	var out []*domain.Suppression
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT id, tenant_id, email_hash, reason, created_at, cleared_at FROM suppressions WHERE cleared_at IS NULL ORDER BY created_at DESC LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var sup domain.Suppression
			if err := rows.Scan(&sup.ID, &sup.TenantID, &sup.EmailHash, &sup.Reason, &sup.CreatedAt, &sup.ClearedAt); err != nil {
				return err
			}
			out = append(out, &sup)
		}
		return rows.Err()
	})
	return out, err
}

// ClearSuppression clears a suppression by email hash (admin, NOTIF-FR-051).
func (s *PG) ClearSuppression(ctx context.Context, tenant uuid.UUID, emailHash string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE suppressions SET cleared_at=now() WHERE email_hash=$1 AND cleared_at IS NULL`, emailHash)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}
