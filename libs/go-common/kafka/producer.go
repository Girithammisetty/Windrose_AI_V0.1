// Package kafka is the platform's real Kafka client over segmentio/kafka-go,
// speaking the Kafka wire protocol against Redpanda (deploy: localhost:9092).
// It provides an idempotent producer that publishes the master event envelope
// (MASTER-FR-030/031) and a consumer group with Redis SETNX dedup
// (MASTER-FR-032), retry-with-backoff, and a real DLQ topic (MASTER-FR-033).
package kafka

import (
	"context"
	"encoding/json"
	"strconv"
	"sync"

	"github.com/segmentio/kafka-go"

	"github.com/windrose-ai/go-common/event"
)

// Producer publishes envelopes to a topic. It configures the underlying writer
// with RequireAll acks and hash-by-key partitioning so a tenant's events keep
// per-tenant order (MASTER-FR-031). kafka-go's writer coalesces retries and,
// with RequireAll + a single in-flight batch per partition, gives effectively
// idempotent delivery; consumers additionally dedup on event_id.
type Producer struct {
	writer   *kafka.Writer
	sr       *SchemaRegistry // optional: registers/looks-up subject ids
	mu       sync.Mutex
	schemaID map[string]int // topic -> registered schema id (header annotation)
}

// Config configures a Producer.
type Config struct {
	Brokers        []string
	SchemaRegistry *SchemaRegistry // nil to skip subject registration
	// SASL configures broker authentication (AWS MSK, Confluent Cloud, Azure
	// Event Hubs) — nil (the default) preserves the existing unauthenticated
	// connection to a self-hosted Kafka/Redpanda broker exactly.
	SASL *SASLConfig
	// TLS enables a TLS connection to the broker — required alongside SASL by
	// most managed offerings.
	TLS bool
}

// NewProducer builds a Producer. Topic is set per-message so one Producer can
// publish to a service topic and its DLQ.
func NewProducer(cfg Config) *Producer {
	w := &kafka.Writer{
		Addr:                   kafka.TCP(cfg.Brokers...),
		Balancer:               &kafka.Hash{}, // partition by key = tenant_id
		RequiredAcks:           kafka.RequireAll,
		BatchTimeout:           20e6, // 20ms
		AllowAutoTopicCreation: true,
	}
	// Transport is a RoundTripper INTERFACE field: only assign it when
	// buildTransport returns a genuinely non-nil *kafka.Transport, never a
	// typed-nil, or kafka-go's `w.Transport != nil` check (which falls back to
	// DefaultTransport) would see a non-nil interface wrapping a nil pointer.
	if t := buildTransport(cfg.SASL, cfg.TLS); t != nil {
		w.Transport = t
	}
	return &Producer{writer: w, sr: cfg.SchemaRegistry, schemaID: map[string]int{}}
}

// EnvelopeAvroSchema is the Avro schema document registered for the master
// event envelope value (MASTER-FR-031). Fields mirror event.Envelope; the
// schemaless payload is carried as a JSON string field.
const EnvelopeAvroSchema = `{
  "type": "record",
  "name": "Envelope",
  "namespace": "ai.windrose.events",
  "fields": [
    {"name": "event_id", "type": {"type": "string", "logicalType": "uuid"}},
    {"name": "event_type", "type": "string"},
    {"name": "tenant_id", "type": {"type": "string", "logicalType": "uuid"}},
    {"name": "actor", "type": {"type": "record", "name": "Actor", "fields": [
      {"name": "type", "type": "string"}, {"name": "id", "type": "string"}]}},
    {"name": "via_agent", "type": ["null", {"type": "record", "name": "ViaAgent", "fields": [
      {"name": "agent_id", "type": "string"}, {"name": "version", "type": "string"}]}], "default": null},
    {"name": "resource_urn", "type": "string"},
    {"name": "occurred_at", "type": {"type": "long", "logicalType": "timestamp-micros"}},
    {"name": "trace_id", "type": "string"},
    {"name": "payload", "type": "string"}
  ]
}`

// RegisterEnvelopeSubject registers the envelope Avro schema for topic's value
// subject and caches the returned id. Safe to call at startup; a no-op when no
// SchemaRegistry is configured.
func (p *Producer) RegisterEnvelopeSubject(ctx context.Context, topic string) (int, error) {
	if p.sr == nil {
		return 0, nil
	}
	id, err := p.sr.Register(ctx, SubjectFor(topic), EnvelopeAvroSchema)
	if err != nil {
		return 0, err
	}
	p.mu.Lock()
	p.schemaID[topic] = id
	p.mu.Unlock()
	return id, nil
}

// Publish sends one envelope to topic, keyed by tenant_id (MASTER-FR-031).
func (p *Producer) Publish(ctx context.Context, topic string, env event.Envelope) error {
	raw, err := json.Marshal(env)
	if err != nil {
		return err
	}
	headers := []kafka.Header{
		{Key: "event_id", Value: []byte(env.EventID.String())},
		{Key: "event_type", Value: []byte(env.EventType)},
		{Key: "trace_id", Value: []byte(env.TraceID)},
		{Key: "content_type", Value: []byte("application/json")},
	}
	p.mu.Lock()
	sid, ok := p.schemaID[topic]
	p.mu.Unlock()
	if ok {
		headers = append(headers, kafka.Header{Key: "schema_id", Value: []byte(strconv.Itoa(sid))})
	}
	return p.writer.WriteMessages(ctx, kafka.Message{
		Topic:   topic,
		Key:     env.PartitionKey(),
		Value:   raw,
		Headers: headers,
	})
}

// PublishBatch sends many envelopes to topic in one call (outbox relay).
func (p *Producer) PublishBatch(ctx context.Context, topic string, envs []event.Envelope) error {
	for _, e := range envs {
		if err := p.Publish(ctx, topic, e); err != nil {
			return err
		}
	}
	return nil
}

// Close flushes and closes the writer.
func (p *Producer) Close() error { return p.writer.Close() }
