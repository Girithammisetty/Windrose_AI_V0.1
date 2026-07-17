package events

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/store/memory"
)

// recordingPublisher captures published batches.
type recordingPublisher struct {
	batches [][]*domain.OutboxEvent
	err     error
}

func (p *recordingPublisher) Publish(_ context.Context, evs []*domain.OutboxEvent) error {
	if p.err != nil {
		return p.err
	}
	p.batches = append(p.batches, evs)
	return nil
}

func TestPollerDrainOnce(t *testing.T) {
	ctx := context.Background()
	store := memory.New()
	tid, _ := uuid.NewV7()
	for i := 0; i < 3; i++ {
		if err := store.AppendOutbox(ctx, domain.NewEvent("thing.happened", tid,
			domain.Actor{Type: "service", ID: "x"}, "wr:"+tid.String()+":identity:thing/1", time.Now().UTC(), nil)); err != nil {
			t.Fatal(err)
		}
	}
	pub := &recordingPublisher{}
	p := &Poller{Store: store, Publisher: pub, Interval: time.Second, BatchSize: 10, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	if err := p.DrainOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if len(pub.batches) != 1 || len(pub.batches[0]) != 3 {
		t.Fatalf("expected one batch of 3, got %v", pub.batches)
	}
	// All events are now marked published; a second drain publishes nothing.
	if err := p.DrainOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if len(pub.batches) != 1 {
		t.Fatalf("second drain published again: %d batches", len(pub.batches))
	}
	remaining, _ := store.ListOutbox(ctx, 100)
	if len(remaining) != 0 {
		t.Fatalf("expected 0 unpublished, got %d", len(remaining))
	}
}

func TestPollerRunStopsOnContextCancel(t *testing.T) {
	store := memory.New()
	tid, _ := uuid.NewV7()
	_ = store.AppendOutbox(context.Background(), domain.NewEvent("x.y", tid,
		domain.Actor{Type: "service", ID: "x"}, "urn", time.Now().UTC(), nil))
	pub := &recordingPublisher{}
	p := &Poller{Store: store, Publisher: pub, Interval: time.Millisecond, BatchSize: 10, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { p.Run(ctx); close(done) }()
	// Give the ticker a few cycles to drain, then stop.
	deadline := time.After(2 * time.Second)
	for {
		remaining, _ := store.ListOutbox(context.Background(), 10)
		if len(remaining) == 0 {
			break
		}
		select {
		case <-deadline:
			t.Fatal("poller did not drain within deadline")
		case <-time.After(2 * time.Millisecond):
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return after context cancel")
	}
}

func TestLogPublisher(t *testing.T) {
	tid, _ := uuid.NewV7()
	p := &LogPublisher{Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	if err := p.Publish(context.Background(), []*domain.OutboxEvent{
		{EventID: uuid.New(), EventType: "x.y", TenantID: tid, ResourceURN: "urn"},
	}); err != nil {
		t.Fatal(err)
	}
}
