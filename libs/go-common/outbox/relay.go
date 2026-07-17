// Package outbox is the shared transactional-outbox relay (MASTER-FR-034):
// rows are written to a service's `outbox` table in the same DB transaction as
// the state change; this relay polls unpublished rows and ships them to Kafka
// via the shared producer, marking them published only after a successful
// publish (at-least-once — consumers dedup on event_id, MASTER-FR-032).
package outbox

import (
	"context"
	"log/slog"
	"time"

	"github.com/windrose-ai/go-common/event"
)

// Row is one unpublished outbox row: an opaque store id plus the envelope.
type Row struct {
	ID       any // store-native id (int64, uuid.UUID, ...) passed back to MarkPublished
	Envelope event.Envelope
}

// Source is implemented by a service's store: fetch unpublished rows oldest
// first, and mark a set of ids published. Both run against the real DB.
type Source interface {
	FetchUnpublished(ctx context.Context, limit int) ([]Row, error)
	MarkPublished(ctx context.Context, ids []any) error
}

// Publisher ships one envelope to a topic (satisfied by *kafka.Producer).
type Publisher interface {
	Publish(ctx context.Context, topic string, env event.Envelope) error
}

// Relay drains the outbox to the Publisher on an interval.
type Relay struct {
	Source    Source
	Publisher Publisher
	Topic     string
	Interval  time.Duration
	Batch     int
	Log       *slog.Logger
}

// New builds a Relay with sensible defaults (250ms poll, 256 batch).
func New(src Source, pub Publisher, topic string) *Relay {
	return &Relay{
		Source:    src,
		Publisher: pub,
		Topic:     topic,
		Interval:  250 * time.Millisecond,
		Batch:     256,
		Log:       slog.Default(),
	}
}

// Run polls until ctx is cancelled.
func (r *Relay) Run(ctx context.Context) {
	for {
		n, err := r.ProcessOnce(ctx)
		if err != nil && ctx.Err() == nil {
			r.Log.Error("outbox relay pass failed", "err", err)
		}
		if ctx.Err() != nil {
			return
		}
		if n == 0 {
			select {
			case <-ctx.Done():
				return
			case <-time.After(r.Interval):
			}
		}
	}
}

// ProcessOnce publishes one batch in outbox-id order and returns rows
// published. It stops at the first publish failure to preserve per-tenant
// ordering; the rest retries next pass. Rows are marked published only after a
// successful publish (at-least-once).
func (r *Relay) ProcessOnce(ctx context.Context) (int, error) {
	rows, err := r.Source.FetchUnpublished(ctx, r.Batch)
	if err != nil {
		return 0, err
	}
	var done []any
	for _, row := range rows {
		if err := r.Publisher.Publish(ctx, r.Topic, row.Envelope); err != nil {
			r.Log.Warn("outbox publish failed; will retry", "event_id", row.Envelope.EventID, "err", err)
			break
		}
		done = append(done, row.ID)
	}
	if len(done) > 0 {
		if err := r.Source.MarkPublished(ctx, done); err != nil {
			return len(done), err
		}
	}
	return len(done), nil
}
