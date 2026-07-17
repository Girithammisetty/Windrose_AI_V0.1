//go:build integration

// Integration test for the outbox relay against real Redpanda: rows in an
// in-memory Source (standing in for the store's outbox table) are relayed to
// Kafka and marked published; a consumer reads them back off the topic.
package outbox

import (
	"context"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	segkafka "github.com/segmentio/kafka-go"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/go-common/kafka"
)

func brokers() []string {
	if b := os.Getenv("KAFKA_BROKERS"); b != "" {
		return []string{b}
	}
	return []string{"localhost:9092"}
}

// memSource is a test double for a store's outbox table (unit-test-style fake,
// allowed here because it stands in for the DB while the relay + Kafka are real).
type memSource struct {
	mu        sync.Mutex
	rows      []Row
	published map[string]bool
}

func (m *memSource) FetchUnpublished(_ context.Context, limit int) ([]Row, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []Row
	for _, r := range m.rows {
		if !m.published[r.ID.(string)] {
			out = append(out, r)
			if len(out) >= limit {
				break
			}
		}
	}
	return out, nil
}

func (m *memSource) MarkPublished(_ context.Context, ids []any) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, id := range ids {
		m.published[id.(string)] = true
	}
	return nil
}

func (m *memSource) pending() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	n := 0
	for _, r := range m.rows {
		if !m.published[r.ID.(string)] {
			n++
		}
	}
	return n
}

func TestOutboxRelayToKafka(t *testing.T) {
	conn, err := segkafka.DialContext(context.Background(), "tcp", brokers()[0])
	if err != nil {
		t.Skipf("kafka unavailable: %v", err)
	}
	_ = conn.Close()

	ctx := context.Background()
	topic := "test.outbox." + uuid.NewString()[:8]
	tenant := uuid.New()

	src := &memSource{published: map[string]bool{}}
	for i := 0; i < 5; i++ {
		env := event.New("thing.created", tenant, event.Actor{Type: "service", ID: "test"}, "wr:t:svc:res/1", "tr", nil)
		src.rows = append(src.rows, Row{ID: uuid.NewString(), Envelope: env})
	}

	prod := kafka.NewProducer(kafka.Config{Brokers: brokers()})
	defer prod.Close()

	relay := New(src, prod, topic)
	rctx, rcancel := context.WithCancel(ctx)
	defer rcancel()
	go relay.Run(rctx)

	// Consume all 5 off the topic.
	var got atomic.Int32
	cg := kafka.NewConsumerGroup(kafka.ConsumerConfig{
		Brokers: brokers(), GroupID: "outbox-verify-" + uuid.NewString()[:8],
		Topics:  []string{topic},
		Handler: func(_ context.Context, e event.Envelope) error { got.Add(1); return nil },
	})
	cctx, ccancel := context.WithCancel(ctx)
	defer ccancel()
	go cg.Run(cctx)
	defer cg.Close()

	deadline := time.Now().Add(25 * time.Second)
	for time.Now().Before(deadline) {
		if got.Load() >= 5 && src.pending() == 0 {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("relay incomplete: consumed=%d pending=%d", got.Load(), src.pending())
}
