package store

import (
	"context"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

const reportCols = `id, tenant_id, workspace_id, dashboard_id, name, recipients, cadence, send_hour, send_weekday,
	timezone, format, enabled, temporal_schedule_id, last_sent_at, last_status, last_error,
	created_by, created_at, updated_at, deleted_at`

func scanReport(row pgx.Row) (*domain.ReportSubscription, error) {
	var r domain.ReportSubscription
	err := row.Scan(&r.ID, &r.TenantID, &r.WorkspaceID, &r.DashboardID, &r.Name, &r.Recipients, &r.Cadence,
		&r.SendHour, &r.SendWeekday, &r.Timezone, &r.Format, &r.Enabled, &r.TemporalScheduleID,
		&r.LastSentAt, &r.LastStatus, &r.LastError, &r.CreatedBy, &r.CreatedAt, &r.UpdatedAt, &r.DeletedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &r, nil
}

// CreateReportSubscription inserts a new scheduled report (NOTIF-FR-060).
func (s *PG) CreateReportSubscription(ctx context.Context, r *domain.ReportSubscription) error {
	return s.withTenant(ctx, r.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO report_subscriptions (`+reportCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)`,
			r.ID, r.TenantID, r.WorkspaceID, r.DashboardID, r.Name, r.Recipients, r.Cadence, r.SendHour, r.SendWeekday,
			r.Timezone, r.Format, r.Enabled, r.TemporalScheduleID, r.LastSentAt, r.LastStatus, r.LastError,
			r.CreatedBy, r.CreatedAt, r.UpdatedAt, r.DeletedAt)
		return err
	})
}

// GetReportSubscription fetches one active subscription (RLS-scoped).
func (s *PG) GetReportSubscription(ctx context.Context, tenant, id uuid.UUID) (*domain.ReportSubscription, error) {
	var r *domain.ReportSubscription
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var e error
		r, e = scanReport(tx.QueryRow(ctx, `SELECT `+reportCols+` FROM report_subscriptions WHERE id=$1 AND deleted_at IS NULL`, id))
		return e
	})
	return r, err
}

// ListReportSubscriptions returns active subscriptions for a tenant, cursor-paginated,
// optionally narrowed to one dashboard.
func (s *PG) ListReportSubscriptions(ctx context.Context, tenant uuid.UUID, dashboardID *uuid.UUID, limit int, cursor *uuid.UUID) ([]*domain.ReportSubscription, error) {
	var out []*domain.ReportSubscription
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + reportCols + ` FROM report_subscriptions WHERE deleted_at IS NULL`
		args := []any{}
		if dashboardID != nil {
			args = append(args, *dashboardID)
			q += ` AND dashboard_id = $` + itoa(len(args))
		}
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
			r, err := scanReport(rows)
			if err != nil {
				return err
			}
			out = append(out, r)
		}
		return rows.Err()
	})
	return out, err
}

// UpdateReportSubscription replaces mutable fields of a subscription.
func (s *PG) UpdateReportSubscription(ctx context.Context, r *domain.ReportSubscription) error {
	return s.withTenant(ctx, r.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE report_subscriptions SET name=$2, recipients=$3, cadence=$4, send_hour=$5, send_weekday=$6,
				timezone=$7, format=$8, enabled=$9, temporal_schedule_id=$10, updated_at=now()
			WHERE id=$1 AND deleted_at IS NULL`,
			r.ID, r.Name, r.Recipients, r.Cadence, r.SendHour, r.SendWeekday, r.Timezone, r.Format,
			r.Enabled, r.TemporalScheduleID)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// SetReportScheduleID persists the Temporal Schedule handle backing a
// subscription (set once right after the schedule is created).
func (s *PG) SetReportScheduleID(ctx context.Context, tenant, id uuid.UUID, scheduleID string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE report_subscriptions SET temporal_schedule_id=$2, updated_at=now() WHERE id=$1`, id, scheduleID)
		return err
	})
}

// RecordReportRun stamps the outcome of one send attempt (success or failure).
// Called by the Temporal activity, which always knows the subscription's
// tenant (it is part of the workflow input), so this stays within the normal
// per-tenant RLS path — no platform-role bypass needed.
func (s *PG) RecordReportRun(ctx context.Context, tenant, id uuid.UUID, status, sendErr string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			UPDATE report_subscriptions SET last_sent_at=now(), last_status=$2, last_error=$3, updated_at=now()
			WHERE id=$1`, id, status, sendErr)
		return err
	})
}

// DeleteReportSubscription soft-deletes a subscription.
func (s *PG) DeleteReportSubscription(ctx context.Context, tenant, id uuid.UUID) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE report_subscriptions SET deleted_at=now() WHERE id=$1 AND deleted_at IS NULL`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}
