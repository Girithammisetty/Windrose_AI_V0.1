package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
)

// RecordAnomaly upserts a detected anomaly and emits usage.anomaly_detected
// (USG-FR-050). Idempotent per (tenant, meter, day).
func (s *PG) RecordAnomaly(ctx context.Context, a domain.Anomaly) (domain.Anomaly, bool, error) {
	a.ID = domain.NewID()
	a.Status = domain.AnomalyOpen
	a.CreatedAt = time.Now().UTC()
	created := false
	op := domain.Op{Tenant: a.TenantID, Actor: svcActor()}
	err := s.withTenant(ctx, a.TenantID, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
			INSERT INTO anomalies (id, tenant_id, meter_key, day, observed, mean, stddev, z, status, suppressed_reason)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'open',$9)
			ON CONFLICT (tenant_id, meter_key, day) DO NOTHING`,
			a.ID, a.TenantID, a.MeterKey, a.Day, a.Observed, a.Mean, a.Stddev, a.Z, a.SuppressedReason)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return nil // already recorded for this day
		}
		created = true
		if a.SuppressedReason != nil {
			return nil // suppressed anomalies are stored but do not alert (BR-14)
		}
		env := events.NewEnvelope(events.EvAnomalyDetected, op, domain.AnomalyURN(a.TenantID, a.ID), map[string]any{
			"anomaly_id": a.ID.String(), "meter_key": a.MeterKey,
			"scope":    map[string]any{"tenant_id": a.TenantID.String()},
			"day":      a.Day.Format("2006-01-02"),
			"observed": a.Observed, "mean": a.Mean, "stddev": a.Stddev, "z": a.Z,
		})
		return insertOutbox(ctx, tx, env)
	})
	return a, created, err
}

// ListAnomalies returns anomalies for a tenant (USG-FR-051).
func (s *PG) ListAnomalies(ctx context.Context, tenant uuid.UUID, status string, limit int) ([]domain.Anomaly, error) {
	if limit <= 0 {
		limit = 50
	}
	var out []domain.Anomaly
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, meter_key, day, observed, mean, stddev, z, status, dismissed_by, suppressed_reason, created_at
			FROM anomalies WHERE ($1='' OR status=$1) ORDER BY day DESC, created_at DESC LIMIT $2`, status, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var a domain.Anomaly
			if err := rows.Scan(&a.ID, &a.TenantID, &a.MeterKey, &a.Day, &a.Observed, &a.Mean,
				&a.Stddev, &a.Z, &a.Status, &a.DismissedBy, &a.SuppressedReason, &a.CreatedAt); err != nil {
				return err
			}
			out = append(out, a)
		}
		return rows.Err()
	})
	return out, err
}

// DismissAnomaly marks an anomaly dismissed (USG-FR-051, audited).
func (s *PG) DismissAnomaly(ctx context.Context, op domain.Op, id uuid.UUID, by string) error {
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `UPDATE anomalies SET status='dismissed', dismissed_by=$2, updated_at=now()
			WHERE id=$1 AND status='open'`, id, by)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return domain.ErrNotFound
		}
		return nil
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.ErrNotFound
	}
	return err
}
