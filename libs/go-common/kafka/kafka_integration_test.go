//go:build integration

// Integration tests for the real Kafka client against Redpanda + Schema
// Registry (deploy/docker-compose.dev.yml). They prove the full wire path:
// schema-subject registration, publish → consume, Redis SETNX dedup, and
// DLQ-on-poison after retries. Run: go test -tags integration ./kafka/...
package kafka

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
	"github.com/windrose-ai/go-common/redisx"
)

func brokers() []string {
	if b := os.Getenv("KAFKA_BROKERS"); b != "" {
		return []string{b}
	}
	return []string{"localhost:9092"}
}

func srURL() string {
	if u := os.Getenv("SCHEMA_REGISTRY_URL"); u != "" {
		return u
	}
	return "http://localhost:8081"
}

func redisAddr() string {
	if a := os.Getenv("REDIS_ADDR"); a != "" {
		return a
	}
	return "localhost:6379"
}

func skipIfNoBroker(t *testing.T) {
	conn, err := segkafka.DialContext(context.Background(), "tcp", brokers()[0])
	if err != nil {
		t.Skipf("kafka unavailable at %s: %v", brokers()[0], err)
	}
	_ = conn.Close()
}

func mkEnvelope(tenant uuid.UUID, typ string) event.Envelope {
	return event.New(typ, tenant, event.Actor{Type: "service", ID: "test"}, "wr:t:svc:res/1", "trace-x", map[string]any{"k": "v"})
}

func TestSchemaRegistryRegisterAndPublishConsume(t *testing.T) {
	skipIfNoBroker(t)
	ctx := context.Background()
	topic := "test.events." + uuid.NewString()[:8]

	sr := NewSchemaRegistry(srURL())
	prod := NewProducer(Config{Brokers: brokers(), SchemaRegistry: sr})
	defer prod.Close()

	id, err := prod.RegisterEnvelopeSubject(ctx, topic)
	if err != nil {
		t.Fatalf("register subject: %v", err)
	}
	if id <= 0 {
		t.Fatalf("schema id not assigned: %d", id)
	}

	tenant := uuid.New()
	env := mkEnvelope(tenant, "thing.created")
	if err := prod.Publish(ctx, topic, env); err != nil {
		t.Fatalf("publish: %v", err)
	}

	// Consume it back.
	rdb := redisx.New(redisAddr())
	defer rdb.Close()
	var got atomic.Int32
	var seen event.Envelope
	var mu sync.Mutex
	cg := NewConsumerGroup(ConsumerConfig{
		Brokers: brokers(), GroupID: "test-grp-" + uuid.NewString()[:8],
		Topics: []string{topic}, Dedup: rdb,
		Handler: func(_ context.Context, e event.Envelope) error {
			mu.Lock()
			seen = e
			mu.Unlock()
			got.Add(1)
			return nil
		},
	})
	cctx, cancel := context.WithCancel(ctx)
	defer cancel()
	go cg.Run(cctx)
	defer cg.Close()

	waitFor(t, 20*time.Second, func() bool { return got.Load() >= 1 })
	mu.Lock()
	defer mu.Unlock()
	if seen.EventID != env.EventID || seen.TenantID != tenant {
		t.Fatalf("consumed wrong envelope: %+v", seen)
	}
}

func TestConsumerDedup(t *testing.T) {
	skipIfNoBroker(t)
	ctx := context.Background()
	topic := "test.dedup." + uuid.NewString()[:8]
	prod := NewProducer(Config{Brokers: brokers()})
	defer prod.Close()

	rdb := redisx.New(redisAddr())
	defer rdb.Close()

	tenant := uuid.New()
	env := mkEnvelope(tenant, "thing.created")
	// Publish the SAME envelope (same event_id) twice.
	if err := prod.Publish(ctx, topic, env); err != nil {
		t.Fatal(err)
	}
	if err := prod.Publish(ctx, topic, env); err != nil {
		t.Fatal(err)
	}

	var count atomic.Int32
	cg := NewConsumerGroup(ConsumerConfig{
		Brokers: brokers(), GroupID: "test-dedup-" + uuid.NewString()[:8],
		Topics: []string{topic}, Dedup: rdb,
		Handler: func(_ context.Context, _ event.Envelope) error { count.Add(1); return nil },
	})
	cctx, cancel := context.WithCancel(ctx)
	defer cancel()
	go cg.Run(cctx)
	defer cg.Close()

	// Give it time to deliver both copies; dedup must collapse to one handle.
	time.Sleep(8 * time.Second)
	if n := count.Load(); n != 1 {
		t.Fatalf("expected exactly 1 handled (dedup), got %d", n)
	}
}

func TestConsumerDLQOnPoison(t *testing.T) {
	skipIfNoBroker(t)
	ctx := context.Background()
	topic := "test.poison." + uuid.NewString()[:8]
	group := "test-dlq-" + uuid.NewString()[:8]
	prod := NewProducer(Config{Brokers: brokers()})
	defer prod.Close()
	rdb := redisx.New(redisAddr())
	defer rdb.Close()

	env := mkEnvelope(uuid.New(), "thing.created")
	if err := prod.Publish(ctx, topic, env); err != nil {
		t.Fatal(err)
	}

	var attempts atomic.Int32
	cg := NewConsumerGroup(ConsumerConfig{
		Brokers: brokers(), GroupID: group, Topics: []string{topic},
		Dedup: rdb, DLQ: prod, MaxRetries: 2,
		Handler: func(_ context.Context, _ event.Envelope) error {
			attempts.Add(1)
			return errPoison
		},
	})
	cctx, cancel := context.WithCancel(ctx)
	defer cancel()
	go cg.Run(cctx)
	defer cg.Close()

	// Consume the DLQ topic; the poison message must arrive there.
	dlqTopic := DLQTopic(topic, group)
	reader := segkafka.NewReader(segkafka.ReaderConfig{
		Brokers: brokers(), GroupID: "dlq-verify-" + uuid.NewString()[:8],
		GroupTopics: []string{dlqTopic}, MinBytes: 1, MaxBytes: 1 << 20,
	})
	defer reader.Close()
	rctx, rcancel := context.WithTimeout(ctx, 25*time.Second)
	defer rcancel()
	msg, err := reader.FetchMessage(rctx)
	if err != nil {
		t.Fatalf("no DLQ message: %v (attempts=%d)", err, attempts.Load())
	}
	if len(msg.Value) == 0 {
		t.Fatal("empty DLQ message")
	}
	if attempts.Load() < 2 {
		t.Fatalf("expected >=2 handler attempts before DLQ, got %d", attempts.Load())
	}
}

var errPoison = &poisonErr{}

type poisonErr struct{}

func (*poisonErr) Error() string { return "poison" }

func waitFor(t *testing.T, d time.Duration, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("condition not met within %s", d)
}
