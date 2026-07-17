package store

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
)

const notifCols = `id, tenant_id, user_id, event_id, event_type, severity_class, title, body, resource_urn, deep_link, matched_rules, read_at, created_at`

func scanNotification(row pgx.Row) (*domain.Notification, error) {
	var n domain.Notification
	var matched []byte
	err := row.Scan(&n.ID, &n.TenantID, &n.UserID, &n.EventID, &n.EventType, &n.SeverityClass,
		&n.Title, &n.Body, &n.ResourceURN, &n.DeepLink, &matched, &n.ReadAt, &n.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal(matched, &n.MatchedRules)
	return &n, nil
}

// InsertNotificationTx persists an in-app notification and its delivery row plus
// any emitted outbox events, atomically. Returns created=false when the
// (event_id, recipient, channel) delivery already exists (BR-1 dedup no-op).
func (s *PG) InsertNotificationTx(ctx context.Context, n *domain.Notification, d *domain.Delivery, payload map[string]any, envs []gcevent.Envelope) (created bool, err error) {
	err = s.withTenant(ctx, n.TenantID, func(tx pgx.Tx) error {
		ct, e := tx.Exec(ctx, `
			INSERT INTO deliveries (id, tenant_id, notification_id, webhook_endpoint_id, event_id, recipient, channel, provider, status, provider_msg_id, attempts, last_error, next_retry_at, payload)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
			ON CONFLICT (tenant_id, event_id, recipient, channel) DO NOTHING`,
			d.ID, d.TenantID, d.NotificationID, d.WebhookEndpointID, d.EventID, d.Recipient, d.Channel,
			d.Provider, d.Status, d.ProviderMsgID, d.Attempts, d.LastError, d.NextRetryAt, mustJSON(payload))
		if e != nil {
			return e
		}
		if ct.RowsAffected() == 0 {
			return nil // duplicate: created stays false
		}
		created = true
		if _, e := tx.Exec(ctx, `
			INSERT INTO notifications (`+notifCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
			n.ID, n.TenantID, n.UserID, n.EventID, n.EventType, n.SeverityClass, n.Title, n.Body,
			n.ResourceURN, n.DeepLink, mustJSON(n.MatchedRules), n.ReadAt, n.CreatedAt); e != nil {
			return e
		}
		return insertOutboxTx(ctx, tx, envs)
	})
	return created, err
}

// ListNotifications returns a user's inbox, newest first, optionally unread-only.
func (s *PG) ListNotifications(ctx context.Context, tenant uuid.UUID, userID string, unreadOnly bool, limit int, cursor *uuid.UUID) ([]*domain.Notification, error) {
	var out []*domain.Notification
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + notifCols + ` FROM notifications WHERE user_id=$1`
		args := []any{userID}
		if unreadOnly {
			q += ` AND read_at IS NULL`
		}
		if cursor != nil {
			args = append(args, *cursor)
			q += ` AND id < $` + itoa(len(args))
		}
		args = append(args, limit)
		q += ` ORDER BY id DESC LIMIT $` + itoa(len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			n, err := scanNotification(rows)
			if err != nil {
				return err
			}
			out = append(out, n)
		}
		return rows.Err()
	})
	return out, err
}

// GetNotification fetches one notification for the user (RLS-scoped).
func (s *PG) GetNotification(ctx context.Context, tenant uuid.UUID, userID string, id uuid.UUID) (*domain.Notification, error) {
	var n *domain.Notification
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var e error
		n, e = scanNotification(tx.QueryRow(ctx, `SELECT `+notifCols+` FROM notifications WHERE id=$1 AND user_id=$2`, id, userID))
		return e
	})
	return n, err
}

// SetRead marks a notification read/unread; ErrNotFound when absent for user.
func (s *PG) SetRead(ctx context.Context, tenant uuid.UUID, userID string, id uuid.UUID, read bool) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var ct pgconn.CommandTag
		var err error
		if read {
			ct, err = tx.Exec(ctx, `UPDATE notifications SET read_at=now() WHERE id=$1 AND user_id=$2 AND read_at IS NULL`, id, userID)
		} else {
			ct, err = tx.Exec(ctx, `UPDATE notifications SET read_at=NULL WHERE id=$1 AND user_id=$2`, id, userID)
		}
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			// Confirm existence to distinguish 404 from already-in-state.
			var exists bool
			if e := tx.QueryRow(ctx, `SELECT true FROM notifications WHERE id=$1 AND user_id=$2`, id, userID).Scan(&exists); e != nil {
				return ErrNotFound
			}
		}
		return nil
	})
}

// MarkAllRead marks every unread notification read for the user; returns count.
func (s *PG) MarkAllRead(ctx context.Context, tenant uuid.UUID, userID string) (int64, error) {
	var n int64
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE notifications SET read_at=now() WHERE user_id=$1 AND read_at IS NULL`, userID)
		if err != nil {
			return err
		}
		n = ct.RowsAffected()
		return nil
	})
	return n, err
}

// UnreadCount returns the user's unread count (NOTIF-FR-020).
func (s *PG) UnreadCount(ctx context.Context, tenant uuid.UUID, userID string) (int, error) {
	var n int
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT count(*) FROM notifications WHERE user_id=$1 AND read_at IS NULL`, userID).Scan(&n)
	})
	return n, err
}

// CountInAppToday counts a user's in-app notifications created today (rate cap
// NOTIF-FR-031: 500 stored/day/user).
func (s *PG) CountInAppToday(ctx context.Context, tenant uuid.UUID, userID string) (int, error) {
	var n int
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT count(*) FROM notifications WHERE user_id=$1 AND created_at >= date_trunc('day', now())`, userID).Scan(&n)
	})
	return n, err
}
