package events

import (
	"context"
	"os"

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
)

// KafkaPublisher is the real outbound adapter backed by the shared
// libs/go-common Kafka producer (Redpanda + Schema Registry). The outbox relay
// drains committed rows through it so budget/anomaly/reconciliation events land
// on usage.events.v1 (MASTER-FR-030/031/034). Keyed by tenant_id for per-tenant
// ordering with RequireAll acks; consumers additionally dedup on event_id.
type KafkaPublisher struct {
	prod *gckafka.Producer
}

// NewKafkaPublisher builds the shared-producer-backed publisher. When a Schema
// Registry URL is configured it registers the envelope Avro subject for the
// emit topic (best-effort so a missing registry never blocks startup).
func NewKafkaPublisher(ctx context.Context, brokers []string, schemaRegistryURL string) *KafkaPublisher {
	cfg := gckafka.Config{
		Brokers: brokers,
		SASL:    gckafka.SASLFromEnv(os.Getenv), TLS: gckafka.TLSFromEnv(os.Getenv),
	}
	if schemaRegistryURL != "" {
		cfg.SchemaRegistry = gckafka.NewSchemaRegistry(schemaRegistryURL)
	}
	prod := gckafka.NewProducer(cfg)
	if cfg.SchemaRegistry != nil {
		_, _ = prod.RegisterEnvelopeSubject(ctx, EmitTopic)
	}
	return &KafkaPublisher{prod: prod}
}

// Publish ships a batch of envelopes to usage.events.v1 (the relay's Publisher
// port). It stops at the first error so the relay retries remaining rows next
// pass, preserving per-tenant ordering.
func (p *KafkaPublisher) Publish(ctx context.Context, envs []Envelope) error {
	for _, e := range envs {
		if err := p.prod.Publish(ctx, EmitTopic, toMaster(e)); err != nil {
			return err
		}
	}
	return nil
}

// Close flushes and closes the shared producer.
func (p *KafkaPublisher) Close() error { return p.prod.Close() }

// toMaster maps usage-service's Envelope onto the platform master envelope
// (libs/go-common/event) that is the shared wire contract.
func toMaster(env Envelope) gcevent.Envelope {
	out := gcevent.Envelope{
		EventID:     env.EventID,
		EventType:   env.EventType,
		TenantID:    env.TenantID,
		Actor:       gcevent.Actor{Type: env.Actor.Type, ID: env.Actor.ID},
		ResourceURN: env.ResourceURN,
		OccurredAt:  env.OccurredAt,
		TraceID:     env.TraceID,
		Payload:     env.Payload,
	}
	if env.ViaAgent != nil {
		out.ViaAgent = &gcevent.ViaAgent{AgentID: env.ViaAgent.AgentID, Version: env.ViaAgent.Version}
	}
	if out.Payload == nil {
		out.Payload = map[string]any{}
	}
	return out
}
