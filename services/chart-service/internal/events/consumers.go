package events

import (
	"context"
	"log/slog"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/event"
)

// InvalStore is the subset of the store the invalidation consumer needs. The
// scan is tenant-scoped: the master envelope carries the tenant_id of the
// changed resource, and charts referencing it live in that same tenant — so
// invalidation runs under that tenant's RLS, never a cross-tenant platform scan.
type InvalStore interface {
	ChartsForURN(ctx context.Context, tenant uuid.UUID, urn string) ([]uuid.UUID, error)
	MarkChartsBroken(ctx context.Context, tenant uuid.UUID, ids []uuid.UUID, envs []event.Envelope) error
}

// InvalCache is the subset of the cache the consumer needs.
type InvalCache interface {
	InvalidateChart(ctx context.Context, tenant, chartID string) error
}

// Invalidator handles consumed source-change events by evicting cache entries
// for every chart referencing the changed URN (CHART-FR-031) and marking charts
// broken when their measure/query was deleted (BR-3).
type Invalidator struct {
	Store InvalStore
	Cache InvalCache
	Log   *slog.Logger
}

// Handle is the go-common kafka.Handler for consumed topics.
func (inv *Invalidator) Handle(ctx context.Context, env event.Envelope) error {
	urn := env.ResourceURN
	if urn == "" {
		return nil
	}
	deleted := strings.HasSuffix(env.EventType, ".deleted")
	tenant := env.TenantID

	ids, err := inv.Store.ChartsForURN(ctx, tenant, urn)
	if err != nil {
		return err
	}
	for _, id := range ids {
		if err := inv.Cache.InvalidateChart(ctx, tenant.String(), id.String()); err != nil {
			if inv.Log != nil {
				inv.Log.Warn("cache invalidate failed", "tenant", tenant, "chart", id, "err", err)
			}
		}
	}
	if deleted && len(ids) > 0 {
		// mark broken + emit chart.updated per referenced chart (BR-3).
		var envs []event.Envelope
		for _, id := range ids {
			envs = append(envs, New(ChartUpdated, tenant, "service", "svc:chart-service",
				URN(tenant, "chart", id.String()), env.TraceID,
				map[string]any{"chart_id": id.String(), "config_status": "broken", "cause_urn": urn}))
		}
		if err := inv.Store.MarkChartsBroken(ctx, tenant, ids, envs); err != nil {
			return err
		}
	}
	return nil
}
