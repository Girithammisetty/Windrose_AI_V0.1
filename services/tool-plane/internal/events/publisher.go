package events

import (
	"context"
	"log/slog"
	"sync"
	"time"
)

// Publisher is the outbound event port. Real production impl is KafkaPublisher
// (gocommon.go); InMemory is the unit-test double only (never wired into cmd/).
type Publisher interface {
	Publish(ctx context.Context, envs []Envelope) error
}

// InMemory collects published envelopes for unit tests / local dev.
type InMemory struct {
	mu   sync.Mutex
	envs []Envelope
}

func NewInMemory() *InMemory { return &InMemory{} }

func (p *InMemory) Publish(_ context.Context, envs []Envelope) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.envs = append(p.envs, envs...)
	return nil
}

// ByType returns published envelopes of one type.
func (p *InMemory) ByType(eventType string) []Envelope {
	p.mu.Lock()
	defer p.mu.Unlock()
	var out []Envelope
	for _, e := range p.envs {
		if e.EventType == eventType {
			out = append(out, e)
		}
	}
	return out
}

// All returns every published envelope.
func (p *InMemory) All() []Envelope {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]Envelope(nil), p.envs...)
}

// OutboxSource is what the relay drains (implemented by the store).
type OutboxSource interface {
	FetchUnpublished(ctx context.Context, limit int) ([]OutboxRow, error)
	MarkPublished(ctx context.Context, ids []int64) error
}

// Relay drains the transactional outbox to the publisher (MASTER-FR-034: never
// emit before commit — only committed rows are visible here). It routes each
// row to its declared topic (ai.tool_invoked.v1 vs tool.events.v1).
type Relay struct {
	Source    OutboxSource
	Publisher Publisher
	Interval  time.Duration
	Batch     int
}

// Run polls until ctx is done.
func (r *Relay) Run(ctx context.Context) {
	iv := r.Interval
	if iv <= 0 {
		iv = 200 * time.Millisecond
	}
	t := time.NewTicker(iv)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := r.Drain(ctx); err != nil {
				slog.Warn("outbox relay drain failed", "err", err)
			}
		}
	}
}

// Drain publishes one batch and marks it published.
func (r *Relay) Drain(ctx context.Context) error {
	batch := r.Batch
	if batch <= 0 {
		batch = 100
	}
	rows, err := r.Source.FetchUnpublished(ctx, batch)
	if err != nil || len(rows) == 0 {
		return err
	}
	envs := make([]Envelope, len(rows))
	ids := make([]int64, len(rows))
	for i, row := range rows {
		env := row.Envelope
		env.Topic = row.Topic
		envs[i] = env
		ids[i] = row.ID
	}
	if err := r.Publisher.Publish(ctx, envs); err != nil {
		return err // rows stay unpublished; retried next tick
	}
	return r.Source.MarkPublished(ctx, ids)
}
