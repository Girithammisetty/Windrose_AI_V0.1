package store

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// AppendDigest adds an item to the (user, channel, class) digest buffer,
// creating it (with windowEnd) if absent (NOTIF-FR-030). Returns the item count
// after append. The buffer swap on flush is atomic (BR-10).
func (s *PG) AppendDigest(ctx context.Context, tenant uuid.UUID, userID, channel, class string, item domain.DigestItem, windowEnd time.Time) (int, error) {
	var count int
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO digest_buffers (id, tenant_id, user_id, channel, event_class, items, window_end)
			VALUES ($1,$2,$3,$4,$5, jsonb_build_array($6::jsonb), $7)
			ON CONFLICT (tenant_id, user_id, channel, event_class) DO UPDATE
			  SET items = digest_buffers.items || $6::jsonb`,
			domain.NewID(), tenant, userID, channel, class, mustJSON(item), windowEnd)
		if err != nil {
			return err
		}
		return tx.QueryRow(ctx, `SELECT jsonb_array_length(items) FROM digest_buffers WHERE tenant_id=$1 AND user_id=$2 AND channel=$3 AND event_class=$4`,
			tenant, userID, channel, class).Scan(&count)
	})
	return count, err
}

// MarkDigestDue sets a buffer's window_end to `at` so the flush sweeper picks
// it up immediately — the 200-item early-flush trigger (NOTIF-FR-030).
func (s *PG) MarkDigestDue(ctx context.Context, tenant uuid.UUID, user, channel, class string, at time.Time) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE digest_buffers SET window_end=$5 WHERE tenant_id=$1 AND user_id=$2 AND channel=$3 AND event_class=$4`,
			tenant, user, channel, class, at)
		return err
	})
}

// DueDigestBuffers returns buffers whose window has ended, across tenants
// (platform role) — the digest-flush sweeper's queue (NOTIF-FR-030).
func (s *PG) DueDigestBuffers(ctx context.Context, now time.Time, limit int) ([]*domain.DigestBuffer, error) {
	var out []*domain.DigestBuffer
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, user_id, channel, event_class, items, window_end, created_at
			FROM digest_buffers WHERE window_end <= $1 ORDER BY window_end LIMIT $2`, now, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			b := &domain.DigestBuffer{}
			var items []byte
			if err := rows.Scan(&b.ID, &b.TenantID, &b.UserID, &b.Channel, &b.EventClass, &items, &b.WindowEnd, &b.CreatedAt); err != nil {
				return err
			}
			_ = json.Unmarshal(items, &b.Items)
			out = append(out, b)
		}
		return rows.Err()
	})
	return out, err
}

// TakeDigestBuffer atomically deletes and returns a buffer by id (flush claim),
// so a concurrent sweeper cannot double-flush and items arriving after the
// delete open a fresh window (BR-10). Returns nil if already taken.
func (s *PG) TakeDigestBuffer(ctx context.Context, tenant uuid.UUID, id uuid.UUID) (*domain.DigestBuffer, error) {
	var b *domain.DigestBuffer
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		row := tx.QueryRow(ctx, `
			DELETE FROM digest_buffers WHERE id=$1 AND tenant_id=$2
			RETURNING id, tenant_id, user_id, channel, event_class, items, window_end, created_at`, id, tenant)
		bb := &domain.DigestBuffer{}
		var items []byte
		if err := row.Scan(&bb.ID, &bb.TenantID, &bb.UserID, &bb.Channel, &bb.EventClass, &items, &bb.WindowEnd, &bb.CreatedAt); err != nil {
			if err == pgx.ErrNoRows {
				return nil
			}
			return err
		}
		_ = json.Unmarshal(items, &bb.Items)
		b = bb
		return nil
	})
	return b, err
}
