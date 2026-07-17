package events

import (
	"context"
	"log/slog"
	"time"
)

// OutboxSource is implemented by the store: fetch unpublished rows, mark
// published. Kept as an interface for unit testing the relay loop.
type OutboxSource interface {
	FetchUnpublishedEnvelopes(ctx context.Context, limit int) ([]OutboxEntry, error)
	MarkEnvelopesPublished(ctx context.Context, ids []int64) error
}

// OutboxEntry is one unpublished outbox row.
type OutboxEntry struct {
	ID       int64
	Envelope Envelope
}

// OutboxRelay drains the transactional outbox to the EventPublisher
// (MASTER-FR-034: DB-write + event-emit atomicity via outbox + poller).
// At-least-once: rows are marked published only after a successful publish;
// consumers dedup on event_id (MASTER-FR-032).
type OutboxRelay struct {
	Source    OutboxSource
	Publisher EventPublisher
	Topic     string
	Interval  time.Duration
	Batch     int
	Log       *slog.Logger
}

func NewOutboxRelay(src OutboxSource, pub EventPublisher) *OutboxRelay {
	return &OutboxRelay{
		Source:    src,
		Publisher: pub,
		Topic:     Topic,
		Interval:  250 * time.Millisecond,
		Batch:     256,
		Log:       slog.Default(),
	}
}

// Run polls until ctx is cancelled.
func (r *OutboxRelay) Run(ctx context.Context) {
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

// ProcessOnce publishes one batch in outbox-id order; returns rows published.
func (r *OutboxRelay) ProcessOnce(ctx context.Context) (int, error) {
	rows, err := r.Source.FetchUnpublishedEnvelopes(ctx, r.Batch)
	if err != nil {
		return 0, err
	}
	var done []int64
	for _, row := range rows {
		if err := r.Publisher.Publish(ctx, r.Topic, row.Envelope); err != nil {
			// Stop at the first failure to preserve per-tenant ordering;
			// everything from here on retries next pass.
			r.Log.Warn("outbox publish failed; will retry", "event_id", row.Envelope.EventID, "err", err)
			break
		}
		done = append(done, row.ID)
	}
	if len(done) > 0 {
		if err := r.Source.MarkEnvelopesPublished(ctx, done); err != nil {
			return len(done), err
		}
	}
	return len(done), nil
}
