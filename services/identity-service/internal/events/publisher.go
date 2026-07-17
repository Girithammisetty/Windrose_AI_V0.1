// Package events contains the outbox relay (MASTER-FR-034). Rows are written
// transactionally by the store; the Poller ships unpublished rows to a
// Publisher and marks them published.
//
// The Kafka publisher (topic identity.events.v1, Avro per events/*.avsc,
// partition key tenant_id — MASTER-FR-030/031) is a stub: this build ships
// LogPublisher. TODO(identity): franz-go + schema-registry Avro encoding.
package events

import (
	"context"
	"log/slog"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// Publisher ships one event batch. Must be idempotent (MASTER-FR-032:
// consumers dedup on event_id).
type Publisher interface {
	Publish(ctx context.Context, evs []*domain.OutboxEvent) error
}

// LogPublisher logs the envelope (dev stand-in for Kafka).
type LogPublisher struct{ Log *slog.Logger }

func (p *LogPublisher) Publish(_ context.Context, evs []*domain.OutboxEvent) error {
	for _, ev := range evs {
		p.Log.Info("outbox.publish",
			"event_id", ev.EventID, "event_type", ev.EventType,
			"tenant_id", ev.TenantID, "resource_urn", ev.ResourceURN)
	}
	return nil
}

// Poller drains the outbox on an interval.
type Poller struct {
	Store     domain.Store
	Publisher Publisher
	Interval  time.Duration
	BatchSize int
	Log       *slog.Logger
}

// Run blocks until ctx is cancelled.
func (p *Poller) Run(ctx context.Context) {
	tick := time.NewTicker(p.Interval)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
			if err := p.DrainOnce(ctx); err != nil {
				p.Log.Error("outbox poll failed", "error", err)
			}
		}
	}
}

// DrainOnce publishes one batch (exported for tests).
func (p *Poller) DrainOnce(ctx context.Context) error {
	evs, err := p.Store.ListOutbox(ctx, p.BatchSize)
	if err != nil || len(evs) == 0 {
		return err
	}
	if err := p.Publisher.Publish(ctx, evs); err != nil {
		return err
	}
	ids := make([]uuid.UUID, len(evs))
	for i, ev := range evs {
		ids[i] = ev.EventID
	}
	return p.Store.MarkOutboxPublished(ctx, ids, time.Now().UTC())
}
