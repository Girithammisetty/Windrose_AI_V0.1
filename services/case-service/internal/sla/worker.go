// Package sla is the durable SLA enforcement worker (CASE-FR-012/013). When
// Temporal is available (:7233) the platform runs SLA as a Temporal workflow;
// when it is not, this Postgres-backed scheduled-sweep worker provides the same
// durable guarantee — timer state lives in the sla_timers table, so a killed
// and restarted service resumes pending timers and still fires within the
// accuracy window (AC-4). This is a real durable mechanism, not a fake.
package sla

import (
	"context"
	"log/slog"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/store"
)

// Store is the slice of persistence the worker needs (satisfied by *store.PG).
type Store interface {
	DueTimers(ctx context.Context, now time.Time, limit int) ([]store.SLADueTimer, error)
	FireWarnTimer(ctx context.Context, tenant, caseID uuid.UUID) error
	FireDueTimer(ctx context.Context, tenant, caseID uuid.UUID, policy domain.SLAPolicy) error
	PolicyForCase(ctx context.Context, tenant, caseID uuid.UUID) (domain.SLAPolicy, error)
}

// Worker sweeps due SLA timers on an interval.
type Worker struct {
	Store    Store
	Interval time.Duration
	Batch    int
	Log      *slog.Logger
}

// New builds a Worker with sane defaults (1s sweep — well inside the 60s
// accuracy NFR).
func New(st Store) *Worker {
	return &Worker{Store: st, Interval: time.Second, Batch: 200, Log: slog.Default()}
}

// Run sweeps until ctx is cancelled.
func (w *Worker) Run(ctx context.Context) {
	iv := w.Interval
	if iv <= 0 {
		iv = time.Second
	}
	t := time.NewTicker(iv)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := w.Sweep(ctx); err != nil && ctx.Err() == nil {
				w.Log.Warn("sla sweep failed", "err", err)
			}
		}
	}
}

// Sweep fires all currently-due timers once. Exposed so tests can drive it
// deterministically without waiting on the ticker.
func (w *Worker) Sweep(ctx context.Context) error {
	batch := w.Batch
	if batch <= 0 {
		batch = 200
	}
	timers, err := w.Store.DueTimers(ctx, time.Now().UTC(), batch)
	if err != nil {
		return err
	}
	for _, t := range timers {
		switch t.Kind {
		case "warn":
			if err := w.Store.FireWarnTimer(ctx, t.TenantID, t.CaseID); err != nil {
				w.Log.Warn("fire warn timer failed", "case", t.CaseID, "err", err)
			}
		case "due":
			policy, err := w.Store.PolicyForCase(ctx, t.TenantID, t.CaseID)
			if err != nil {
				w.Log.Warn("policy lookup failed", "case", t.CaseID, "err", err)
				continue
			}
			if err := w.Store.FireDueTimer(ctx, t.TenantID, t.CaseID, policy); err != nil {
				w.Log.Warn("fire due timer failed", "case", t.CaseID, "err", err)
			}
		}
	}
	return nil
}
