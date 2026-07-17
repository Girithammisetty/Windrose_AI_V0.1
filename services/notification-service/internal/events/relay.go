package events

import (
	"context"

	gcoutbox "github.com/windrose-ai/go-common/outbox"
	"github.com/windrose-ai/notification-service/internal/store"
)

// OutboxSource adapts *store.PG to the go-common outbox.Source interface so the
// shared relay drains notification-service's outbox to Kafka (MASTER-FR-034).
type OutboxSource struct {
	St *store.PG
}

// FetchUnpublished returns unpublished rows as generic outbox rows.
func (o OutboxSource) FetchUnpublished(ctx context.Context, limit int) ([]gcoutbox.Row, error) {
	rows, err := o.St.FetchUnpublished(ctx, limit)
	if err != nil {
		return nil, err
	}
	out := make([]gcoutbox.Row, len(rows))
	for i, r := range rows {
		out[i] = gcoutbox.Row{ID: r.ID, Envelope: r.Envelope}
	}
	return out, nil
}

// MarkPublished marks the given ids published.
func (o OutboxSource) MarkPublished(ctx context.Context, ids []any) error {
	int64s := make([]int64, 0, len(ids))
	for _, id := range ids {
		if v, ok := id.(int64); ok {
			int64s = append(int64s, v)
		}
	}
	return o.St.MarkPublished(ctx, int64s)
}
