package store

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/events"
	"github.com/windrose-ai/rbac-service/internal/projection"
)

// ---- projection.SnapshotLoader ---------------------------------------------

// ClaimDirtyRows adapts ClaimDirty to the projection worker contract.
func (s *Store) ClaimDirtyRows(ctx context.Context, workerID string, batch int, visibility time.Duration) ([]projection.DirtyBatch, error) {
	claims, err := s.ClaimDirty(ctx, workerID, batch, visibility)
	if err != nil {
		return nil, err
	}
	out := make([]projection.DirtyBatch, 0, len(claims))
	for _, c := range claims {
		out = append(out, projection.DirtyBatch{
			TenantID: c.TenantID, UserID: c.UserID, IDs: c.IDs, OldestEnqueued: c.OldestEnqueued,
		})
	}
	return out, nil
}

// DeleteDirtyRows adapts DeleteDirty to the projection worker contract.
func (s *Store) DeleteDirtyRows(ctx context.Context, ids []int64) error {
	return s.DeleteDirty(ctx, ids)
}

// ---- events.OutboxSource ----------------------------------------------------

// FetchUnpublishedEnvelopes adapts FetchUnpublished to the outbox relay.
func (s *Store) FetchUnpublishedEnvelopes(ctx context.Context, limit int) ([]events.OutboxEntry, error) {
	rows, err := s.FetchUnpublished(ctx, limit)
	if err != nil {
		return nil, err
	}
	out := make([]events.OutboxEntry, 0, len(rows))
	for _, r := range rows {
		out = append(out, events.OutboxEntry{ID: r.ID, Envelope: r.Envelope})
	}
	return out, nil
}

// MarkEnvelopesPublished adapts MarkPublished to the outbox relay.
func (s *Store) MarkEnvelopesPublished(ctx context.Context, ids []int64) error {
	return s.MarkPublished(ctx, ids)
}

// ---- misc helpers used by the API layer -------------------------------------

// InsertAudit writes a standalone audit event to the outbox (denials,
// override records) outside a mutation transaction.
func (s *Store) InsertAudit(ctx context.Context, env events.Envelope) error {
	return s.WithTenant(ctx, env.TenantID, func(tx pgx.Tx) error {
		return InsertOutbox(ctx, tx, env)
	})
}

// IsTenantAdmin reports membership in the tenant's system Admin permission
// group (cheap targeted check for list-endpoint visibility filtering).
func (s *Store) IsTenantAdmin(ctx context.Context, tenant uuid.UUID, userID string) (bool, error) {
	var admin bool
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT EXISTS (
				SELECT 1 FROM members m
				JOIN groups g ON g.id = m.group_id
				JOIN group_roles gr ON gr.group_id = g.id
				JOIN roles r ON r.id = gr.role_id
				WHERE m.user_id = $1 AND g.group_type = 'permission'
				  AND r.system AND r.name = 'Admin'
				  AND (m.expires_at IS NULL OR m.expires_at > now())
			)`, userID).Scan(&admin)
	})
	return admin, err
}
