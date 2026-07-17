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

// MeteredMonthly returns metered totals per meter for a month (platform scope),
// used by reconciliation to compare against provider bills (USG-FR-070).
func (s *PG) MeteredMonthly(ctx context.Context, month string) (map[string]float64, error) {
	first, err := time.Parse("2006-01", month)
	if err != nil {
		return nil, domain.ErrValidation
	}
	out := map[string]float64{}
	err = s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT meter_key, SUM(quantity_sum)::float8 FROM usage_monthly
			WHERE bucket=$1 GROUP BY meter_key`, first)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var mk string
			var q float64
			if err := rows.Scan(&mk, &q); err != nil {
				return err
			}
			out[mk] = q
		}
		return rows.Err()
	})
	return out, err
}

// UpsertReconciliation records a reconciliation result and, on variance, emits
// usage.reconciliation_variance and marks the month blocking (USG-FR-071).
func (s *PG) UpsertReconciliation(ctx context.Context, r domain.Reconciliation, detail map[string]any, varianceMeters []map[string]any) (domain.Reconciliation, error) {
	r.ID = domain.NewID()
	r.CreatedAt = time.Now().UTC()
	op := domain.Op{Tenant: uuid.Nil, Actor: svcActor(), Platform: true}
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO reconciliations (id, month, provider, status, report_uri, detail)
			VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (month, provider) DO UPDATE SET
			  status=EXCLUDED.status, report_uri=EXCLUDED.report_uri, detail=EXCLUDED.detail, updated_at=now()`,
			r.ID, r.Month, r.Provider, r.Status, r.ReportURI, mustJSON(detail)); err != nil {
			return err
		}
		if r.Status == domain.ReconVariance {
			for _, vm := range varianceMeters {
				env := events.NewEnvelope(events.EvReconciliationVariance, op, domain.ReconciliationURN(r.ID), map[string]any{
					"reconciliation_id": r.ID.String(), "month": r.Month, "provider": r.Provider,
					"meter_key": vm["meter_key"], "metered": vm["metered"], "billed": vm["billed"], "variance_pct": vm["variance_pct"],
				})
				if err := insertOutbox(ctx, tx, env); err != nil {
					return err
				}
			}
		}
		return nil
	})
	return r, err
}

// ReconciliationStatus returns the blocking status for a month (any provider in
// 'variance' blocks chargeback — USG-FR-071).
func (s *PG) ReconciliationStatus(ctx context.Context, month string) (string, error) {
	status := domain.ReconPending
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		var blocking bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM reconciliations WHERE month=$1 AND status='variance')`, month).Scan(&blocking); err != nil {
			return err
		}
		if blocking {
			status = domain.ReconVariance
			return nil
		}
		var any bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM reconciliations WHERE month=$1)`, month).Scan(&any); err != nil {
			return err
		}
		if any {
			status = domain.ReconMatched
		}
		return nil
	})
	return status, err
}

// ListReconciliations returns reconciliations (platform-operator).
func (s *PG) ListReconciliations(ctx context.Context, limit int) ([]domain.Reconciliation, error) {
	if limit <= 0 {
		limit = 50
	}
	var out []domain.Reconciliation
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT id, month, provider, status, report_uri, created_at
			FROM reconciliations ORDER BY month DESC, provider LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var r domain.Reconciliation
			if err := rows.Scan(&r.ID, &r.Month, &r.Provider, &r.Status, &r.ReportURI, &r.CreatedAt); err != nil {
				return err
			}
			out = append(out, r)
		}
		return rows.Err()
	})
	return out, err
}

// AcknowledgeReconciliation moves a variance month to acknowledged, unblocking
// chargeback (USG-FR-071).
func (s *PG) AcknowledgeReconciliation(ctx context.Context, id uuid.UUID) error {
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `UPDATE reconciliations SET status='acknowledged', updated_at=now()
			WHERE id=$1 AND status='variance'`, id)
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

// RecordAdjustment appends a signed adjustment on a closed month (USG-FR-072).
// The month must be finalized (BR-5, month_open → conflict).
func (s *PG) RecordAdjustment(ctx context.Context, op domain.Op, a domain.Adjustment) (domain.Adjustment, error) {
	a.ID = domain.NewID()
	a.TenantID = op.Tenant
	a.CreatedAt = time.Now().UTC()
	first, err := time.Parse("2006-01", a.Month)
	if err != nil {
		return domain.Adjustment{}, domain.ErrValidation
	}
	err = s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		var finalized bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM usage_monthly
			WHERE tenant_id=$1 AND bucket=$2 AND finalized_at IS NOT NULL)`, op.Tenant, first).Scan(&finalized); err != nil {
			return err
		}
		if !finalized {
			return domain.ErrConflict // month_open
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO adjustments (id, tenant_id, meter_key, month, quantity_delta, usd_delta, reason, actor)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			a.ID, a.TenantID, a.MeterKey, a.Month, a.QuantityDelta, a.USDDelta, a.Reason, a.Actor); err != nil {
			return err
		}
		env := events.NewEnvelope(events.EvAdjustmentRecorded, op, domain.AdjustmentURN(op.Tenant, a.ID), map[string]any{
			"adjustment_id": a.ID.String(), "month": a.Month, "meter_key": a.MeterKey,
			"quantity_delta": a.QuantityDelta, "usd_delta": a.USDDelta, "reason": a.Reason,
		})
		return insertOutbox(ctx, tx, env)
	})
	return a, err
}

// adjustmentsUSD sums usd_delta per meter for a tenant-month (chargeback).
func (s *PG) adjustmentsUSD(ctx context.Context, tenant uuid.UUID, month string) (map[string]float64, error) {
	out := map[string]float64{}
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT meter_key, COALESCE(SUM(usd_delta),0)::float8
			FROM adjustments WHERE tenant_id=$1 AND month=$2 GROUP BY meter_key`, tenant, month)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var mk string
			var v float64
			if err := rows.Scan(&mk, &v); err != nil {
				return err
			}
			out[mk] = v
		}
		return rows.Err()
	})
	return out, err
}
