package events

import (
	"context"
	"encoding/json"
	"sync"

	"github.com/segmentio/kafka-go"
)

// EventPublisher abstracts the event transport so the outbox relay is
// testable without brokers. Implementations must be idempotent-friendly:
// the envelope's event_id is the dedup key (MASTER-FR-032).
type EventPublisher interface {
	Publish(ctx context.Context, topic string, env Envelope) error
	Close() error
}

// InMemoryPublisher records published events; the test fake.
type InMemoryPublisher struct {
	mu     sync.Mutex
	events []PublishedEvent
	// FailNext, when set, fails the next Publish (retry-path testing).
	FailNext error
}

type PublishedEvent struct {
	Topic    string
	Envelope Envelope
}

func NewInMemoryPublisher() *InMemoryPublisher { return &InMemoryPublisher{} }

func (p *InMemoryPublisher) Publish(_ context.Context, topic string, env Envelope) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.FailNext != nil {
		err := p.FailNext
		p.FailNext = nil
		return err
	}
	p.events = append(p.events, PublishedEvent{Topic: topic, Envelope: env})
	return nil
}

func (p *InMemoryPublisher) Close() error { return nil }

// Events returns a snapshot of everything published.
func (p *InMemoryPublisher) Events() []PublishedEvent {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := make([]PublishedEvent, len(p.events))
	copy(out, p.events)
	return out
}

// ByType filters recorded events by event_type.
func (p *InMemoryPublisher) ByType(eventType string) []PublishedEvent {
	var out []PublishedEvent
	for _, e := range p.Events() {
		if e.Envelope.EventType == eventType {
			out = append(out, e)
		}
	}
	return out
}

// KafkaPublisher is the real adapter over segmentio/kafka-go. It uses an
// idempotent-configured writer with tenant_id as the partition key
// (MASTER-FR-031). JSON encoding stands in for Avro until the shared
// schema-registry client lands in libs/go-common (envelope fields match
// events/rbac_envelope.avsc exactly).
type KafkaPublisher struct {
	writer *kafka.Writer
}

func NewKafkaPublisher(brokers []string) *KafkaPublisher {
	return &KafkaPublisher{
		writer: &kafka.Writer{
			Addr:         kafka.TCP(brokers...),
			Balancer:     &kafka.Hash{}, // key-hash partitioning by tenant_id
			RequiredAcks: kafka.RequireAll,
			BatchTimeout: 20e6, // 20ms
		},
	}
}

func (p *KafkaPublisher) Publish(ctx context.Context, topic string, env Envelope) error {
	raw, err := json.Marshal(env)
	if err != nil {
		return err
	}
	return p.writer.WriteMessages(ctx, kafka.Message{
		Topic: topic,
		Key:   []byte(env.TenantID.String()),
		Value: raw,
		Headers: []kafka.Header{
			{Key: "event_id", Value: []byte(env.EventID.String())},
			{Key: "event_type", Value: []byte(env.EventType)},
			{Key: "trace_id", Value: []byte(env.TraceID)},
		},
	})
}

func (p *KafkaPublisher) Close() error { return p.writer.Close() }
