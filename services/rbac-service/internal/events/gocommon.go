package events

import (
	"context"
	"os"

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
)

// GoCommonPublisher is the real EventPublisher backed by the shared
// libs/go-common Kafka producer (Redpanda). It replaces the in-memory publisher
// in the runtime path: rbac's outbox relay and consumer DLQ both publish
// through it, so the whole service speaks the platform's shared plumbing
// (MASTER-FR-030/031). rbac's Envelope maps 1:1 onto the master envelope.
type GoCommonPublisher struct {
	prod *gckafka.Producer
}

// NewGoCommonPublisher builds the shared-producer-backed publisher and registers
// the envelope schema subject for rbac's topic (best-effort).
func NewGoCommonPublisher(ctx context.Context, brokers []string, schemaRegistryURL string) *GoCommonPublisher {
	cfg := gckafka.Config{
		Brokers: brokers,
		SASL:    gckafka.SASLFromEnv(os.Getenv), TLS: gckafka.TLSFromEnv(os.Getenv),
	}
	if schemaRegistryURL != "" {
		cfg.SchemaRegistry = gckafka.NewSchemaRegistry(schemaRegistryURL)
	}
	prod := gckafka.NewProducer(cfg)
	if cfg.SchemaRegistry != nil {
		_, _ = prod.RegisterEnvelopeSubject(ctx, Topic)
	}
	return &GoCommonPublisher{prod: prod}
}

// Publish maps the rbac envelope onto the master envelope and publishes it.
func (p *GoCommonPublisher) Publish(ctx context.Context, topic string, env Envelope) error {
	return p.prod.Publish(ctx, topic, toMaster(env))
}

// Close flushes and closes the shared producer.
func (p *GoCommonPublisher) Close() error { return p.prod.Close() }

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
