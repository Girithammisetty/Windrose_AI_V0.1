package store

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
)

const deliveryCols = `id, tenant_id, notification_id, webhook_endpoint_id, event_id, recipient, channel, provider, status, provider_msg_id, attempts, last_error, next_retry_at, payload, created_at, updated_at`

func scanDelivery(row pgx.Row) (*domain.Delivery, map[string]any, error) {
	var d domain.Delivery
	var payload []byte
	err := row.Scan(&d.ID, &d.TenantID, &d.NotificationID, &d.WebhookEndpointID, &d.EventID, &d.Recipient,
		&d.Channel, &d.Provider, &d.Status, &d.ProviderMsgID, &d.Attempts, &d.LastError, &d.NextRetryAt,
		&payload, &d.CreatedAt, &d.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, ErrNotFound
	}
	if err != nil {
		return nil, nil, err
	}
	var p map[string]any
	_ = json.Unmarshal(payload, &p)
	return &d, p, nil
}

// InsertDelivery inserts a delivery row idempotently on (event_id, recipient,
// channel). Returns created=false when it already exists (BR-1).
func (s *PG) InsertDelivery(ctx context.Context, d *domain.Delivery, payload map[string]any) (created bool, err error) {
	err = s.withTenant(ctx, d.TenantID, func(tx pgx.Tx) error {
		ct, e := tx.Exec(ctx, `
			INSERT INTO deliveries (`+deliveryCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
			ON CONFLICT (tenant_id, event_id, recipient, channel) DO NOTHING`,
			d.ID, d.TenantID, d.NotificationID, d.WebhookEndpointID, d.EventID, d.Recipient, d.Channel,
			d.Provider, d.Status, d.ProviderMsgID, d.Attempts, d.LastError, d.NextRetryAt, mustJSON(payload),
			d.CreatedAt, d.UpdatedAt)
		if e != nil {
			return e
		}
		created = ct.RowsAffected() > 0
		return nil
	})
	return created, err
}

// GetDelivery fetches one delivery + its payload (RLS-scoped).
func (s *PG) GetDelivery(ctx context.Context, tenant, id uuid.UUID) (*domain.Delivery, map[string]any, error) {
	var d *domain.Delivery
	var p map[string]any
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var e error
		d, p, e = scanDelivery(tx.QueryRow(ctx, `SELECT `+deliveryCols+` FROM deliveries WHERE id=$1`, id))
		return e
	})
	return d, p, err
}

// UpdateDeliveryStatus applies a status transition and optional emitted events
// (NOTIF-FR-050). Runs under the tenant of the delivery.
func (s *PG) UpdateDeliveryStatus(ctx context.Context, tenant, id uuid.UUID, status, providerMsgID, lastErr string, attempts int, nextRetry *time.Time, envs []gcevent.Envelope) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE deliveries SET status=$2, provider_msg_id=$3, last_error=$4, attempts=$5, next_retry_at=$6, updated_at=now()
			WHERE id=$1`,
			id, status, providerMsgID, lastErr, attempts, nextRetry)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// UpdateDeliveryStatusByHash updates the email delivery matched by provider msg
// id (used by provider status callbacks: delivered/bounced/complained).
func (s *PG) UpdateDeliveryStatusByProviderMsgID(ctx context.Context, tenant uuid.UUID, providerMsgID, status string) (bool, error) {
	var ok bool
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE deliveries SET status=$2, updated_at=now() WHERE provider_msg_id=$1 AND channel='email'`, providerMsgID, status)
		if err != nil {
			return err
		}
		ok = ct.RowsAffected() > 0
		return nil
	})
	return ok, err
}

// ListDeliveriesForEndpoint returns an endpoint's delivery log, newest first.
func (s *PG) ListDeliveriesForEndpoint(ctx context.Context, tenant, endpointID uuid.UUID, limit int, cursor *uuid.UUID) ([]*domain.Delivery, error) {
	var out []*domain.Delivery
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + deliveryCols + ` FROM deliveries WHERE webhook_endpoint_id=$1`
		args := []any{endpointID}
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
			d, _, err := scanDelivery(rows)
			if err != nil {
				return err
			}
			out = append(out, d)
		}
		return rows.Err()
	})
	return out, err
}

// DueDelivery is a webhook delivery awaiting (re)send: its payload is the master
// event envelope to POST.
type DueDelivery struct {
	Delivery domain.Delivery
	Envelope gcevent.Envelope
}

// DueWebhookDeliveries returns webhook deliveries whose next_retry_at is due,
// across tenants (platform role) — the retry sweeper's work queue (NOTIF-FR-023).
func (s *PG) DueWebhookDeliveries(ctx context.Context, now time.Time, limit int) ([]DueDelivery, error) {
	var out []DueDelivery
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT `+deliveryCols+` FROM deliveries
			WHERE channel='webhook' AND status IN ('queued','sent') AND next_retry_at IS NOT NULL AND next_retry_at <= $1
			ORDER BY next_retry_at LIMIT $2`, now, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			d, p, err := scanDelivery(rows)
			if err != nil {
				return err
			}
			var env gcevent.Envelope
			raw, _ := json.Marshal(p["envelope"])
			_ = json.Unmarshal(raw, &env)
			out = append(out, DueDelivery{Delivery: *d, Envelope: env})
		}
		return rows.Err()
	})
	return out, err
}

// QueuedForEndpoint returns queued webhook deliveries for an endpoint in
// event-id order (in-order flush on circuit close, NOTIF-FR-023).
func (s *PG) QueuedForEndpoint(ctx context.Context, tenant, endpointID uuid.UUID) ([]DueDelivery, error) {
	var out []DueDelivery
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT `+deliveryCols+` FROM deliveries
			WHERE webhook_endpoint_id=$1 AND tenant_id=$2 AND status IN ('queued','sent')
			ORDER BY event_id`, endpointID, tenant)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			d, p, err := scanDelivery(rows)
			if err != nil {
				return err
			}
			var env gcevent.Envelope
			raw, _ := json.Marshal(p["envelope"])
			_ = json.Unmarshal(raw, &env)
			out = append(out, DueDelivery{Delivery: *d, Envelope: env})
		}
		return rows.Err()
	})
	return out, err
}

// DeliveryByEvent returns a delivery for (event_id, channel) (RLS-scoped).
func (s *PG) DeliveryByEvent(ctx context.Context, tenant, eventID uuid.UUID, channel string) (*domain.Delivery, error) {
	var d *domain.Delivery
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var e error
		d, _, e = scanDelivery(tx.QueryRow(ctx, `SELECT `+deliveryCols+` FROM deliveries WHERE event_id=$1 AND channel=$2 ORDER BY created_at DESC LIMIT 1`, eventID, channel))
		return e
	})
	return d, err
}

// RequeueWebhookDelivery resets a delivery to queued+due-now so the retry
// sweeper redelivers it (manual redeliver, NOTIF-FR-024).
func (s *PG) RequeueWebhookDelivery(ctx context.Context, tenant, id uuid.UUID) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE deliveries SET status='queued', next_retry_at=now(), updated_at=now() WHERE id=$1 AND channel='webhook'`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// FindDeliveryTenantByProviderMsgID resolves the tenant owning an email
// delivery by provider message id, across tenants (platform role) — used by
// provider status callbacks which carry no Windrose JWT (NOTIF-FR-021, BR-13).
func (s *PG) FindDeliveryTenantByProviderMsgID(ctx context.Context, providerMsgID string) (uuid.UUID, bool, error) {
	var tenant uuid.UUID
	var found bool
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		e := tx.QueryRow(ctx, `SELECT tenant_id FROM deliveries WHERE provider_msg_id=$1 AND channel='email' LIMIT 1`, providerMsgID).Scan(&tenant)
		if errors.Is(e, pgx.ErrNoRows) {
			return nil
		}
		if e != nil {
			return e
		}
		found = true
		return nil
	})
	return tenant, found, err
}

// DueEmail is a deferred email delivery ready to send (quiet-hours end).
type DueEmail struct {
	Delivery domain.Delivery
	To       string
	Subject  string
	Text     string
	HTML     string
}

// DueEmailDeliveries returns deferred emails whose send time has arrived, across
// tenants (platform role) — quiet-hours sweeper (NOTIF-FR-012, AC-13).
func (s *PG) DueEmailDeliveries(ctx context.Context, now time.Time, limit int) ([]DueEmail, error) {
	var out []DueEmail
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT `+deliveryCols+` FROM deliveries
			WHERE channel='email' AND provider='deferred' AND status='queued' AND next_retry_at IS NOT NULL AND next_retry_at <= $1
			ORDER BY next_retry_at LIMIT $2`, now, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			d, p, err := scanDelivery(rows)
			if err != nil {
				return err
			}
			de := DueEmail{Delivery: *d}
			if em, ok := p["email"].(map[string]any); ok {
				de.To, _ = em["to"].(string)
				de.Subject, _ = em["subject"].(string)
				de.Text, _ = em["text"].(string)
				de.HTML, _ = em["html"].(string)
			}
			out = append(out, de)
		}
		return rows.Err()
	})
	return out, err
}

// TenantDeliveryStats returns counts per (channel, status) for a window (ops).
func (s *PG) TenantDeliveryStats(ctx context.Context, tenant uuid.UUID, since time.Time) (map[string]map[string]int, error) {
	stats := map[string]map[string]int{}
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT channel, status, count(*) FROM deliveries WHERE created_at >= $1 GROUP BY channel, status`, since)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var ch, st string
			var n int
			if err := rows.Scan(&ch, &st, &n); err != nil {
				return err
			}
			if stats[ch] == nil {
				stats[ch] = map[string]int{}
			}
			stats[ch][st] = n
		}
		return rows.Err()
	})
	return stats, err
}
