// Package jobs holds usage-service's periodic workers: rollup refresh, budget
// sweep, daily anomaly scan and retention enforcement (USG-FR-020/021/022/050).
// They are real background loops driven from cmd/server; the scan logic here is
// unit-testable against the store.
package jobs

import (
	"context"
	"log/slog"
	"math"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/usage-service/internal/anomaly"
	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/store"
)

// Runner bundles the store the jobs act on.
type Runner struct {
	Store *store.PG
	Log   *slog.Logger
}

func (r *Runner) log() *slog.Logger {
	if r.Log != nil {
		return r.Log
	}
	return slog.Default()
}

// RefreshRollups recomputes rollups for buckets touched in the last 49h
// (covers 48h late events, USG-FR-014/020).
func (r *Runner) RefreshRollups(ctx context.Context) error {
	return r.Store.RefreshRollups(ctx, time.Now().Add(-49*time.Hour))
}

// SweepBudgets re-evaluates every active budget (rollup-driven path + BR-12
// fallback, USG-FR-031).
func (r *Runner) SweepBudgets(ctx context.Context) error {
	tenants, err := r.Store.TenantsWithBudgets(ctx)
	if err != nil {
		return err
	}
	for _, t := range tenants {
		if err := r.Store.EvaluateAll(ctx, t); err != nil {
			r.log().Warn("budget sweep failed", "tenant", t, "err", err)
		}
	}
	return nil
}

// EnforceRetention applies per-tier retention (USG-FR-022).
func (r *Runner) EnforceRetention(ctx context.Context) error {
	return r.Store.EnforceRetention(ctx, time.Now())
}

// AnomalyScan runs z-score detection for `day` (a date) across every tenant and
// meter with a ≥ 28-day trailing baseline (min 7 days) — USG-FR-050. First 7
// days of a new series are suppressed (BR-14).
func (r *Runner) AnomalyScan(ctx context.Context, day time.Time) (int, error) {
	day = time.Date(day.Year(), day.Month(), day.Day(), 0, 0, 0, 0, time.UTC)
	tenants, err := r.Store.TenantsWithUsage(ctx)
	if err != nil {
		return 0, err
	}
	detected := 0
	for _, t := range tenants {
		meters, err := r.Store.MetersWithUsage(ctx, t)
		if err != nil {
			r.log().Warn("anomaly meters lookup failed", "tenant", t, "err", err)
			continue
		}
		for _, mk := range meters {
			n, err := r.scanOne(ctx, t, mk, day)
			if err != nil {
				r.log().Warn("anomaly scan failed", "tenant", t, "meter", mk, "err", err)
				continue
			}
			detected += n
		}
	}
	return detected, nil
}

func (r *Runner) scanOne(ctx context.Context, tenant uuid.UUID, meter string, day time.Time) (int, error) {
	from := day.AddDate(0, 0, -28)
	to := day
	totals, err := r.Store.DailyTotals(ctx, tenant, meter, from, to)
	if err != nil {
		return 0, err
	}
	observed := totals[day.Format("2006-01-02")]
	var history []float64
	for i := 1; i <= 28; i++ {
		d := day.AddDate(0, 0, -i).Format("2006-01-02")
		if v, ok := totals[d]; ok {
			history = append(history, v)
		}
	}
	res := anomaly.ZScore(observed, history, 7)
	if !res.Enough {
		return 0, nil // fewer than 7 days of history: skip (BR-14 new-series)
	}
	if math.Abs(res.Z) < 3 {
		return 0, nil
	}
	a := domain.Anomaly{
		TenantID: tenant, MeterKey: meter, Day: day,
		Observed: res.Observed, Mean: res.Mean, Stddev: res.Stddev, Z: res.Z,
	}
	_, created, err := r.Store.RecordAnomaly(ctx, a)
	if err != nil {
		return 0, err
	}
	if created {
		return 1, nil
	}
	return 0, nil
}
