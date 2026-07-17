// Package events is realtime-hub's Kafka fan-in (RTH-FR-020): it consumes the
// producer topics in broadcast mode (a unique consumer group per pod), routes
// each envelope to a hub topic via the routing table, and hands it to the
// fan-out engine. It also consumes rbac.events.v1 to drive subscription
// revocation (RTH-FR-013). The hub is transport, not consumer-of-record:
// unroutable/oversize events are skipped-and-counted, never DLQ'd
// (documented deviation from MASTER-FR-033, per RTH-FR-020).
package events

import (
	"context"
	"encoding/json"
	"log/slog"
	"os"

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"

	"github.com/windrose-ai/realtime-hub/internal/fanout"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

// PayloadCap is the max per-event payload the hub will fan out (RTH-FR-022).
const PayloadCap = 64 << 10

// FanoutTopics are the producer topics the hub consumes for fan-out (§6).
//
// ai.proposal.v1 and experiment.events.v1 were added after an audit (task
// #78) found agent-runtime and experiment-service both publish real,
// correctly-shaped envelopes that the hub simply never subscribed to — their
// routing rules (below) were dead code with no event ever reaching them.
var FanoutTopics = []string{
	"pipeline.events.v1",
	"ingestion.events.v1",
	"inference.events.v1",
	"chart.events.v1",
	"case.events.v1",
	"notification.events.v1",
	"ai.events.v1",
	"ai.proposal.v1",
	"experiment.events.v1",
}

// RevocationTopic carries rbac grant/role changes (RTH-FR-013).
const RevocationTopic = "rbac.events.v1"

// Sink is the fan-out surface the consumers drive (implemented by *fanout.Hub).
type Sink interface {
	IngestKafka(ctx context.Context, tenant, topic string, ev fanout.Event) error
	Revoke(tenant, urn string) int
}

// SkipCounter counts unroutable/oversize events (RTH-FR-020 metric-only skip).
type SkipCounter interface{ Skipped(reason string) }

// Consumer wires the go-common Kafka consumer groups to the hub. It builds the
// client-facing event body from the master envelope and routes it.
type Consumer struct {
	Router *topics.Router
	Sink   Sink
	Skips  SkipCounter
	Log    *slog.Logger

	fanoutCG *gckafka.ConsumerGroup
	rbacCG   *gckafka.ConsumerGroup
}

// Start launches the fan-out and revocation consumer groups. podID makes the
// consumer group unique per pod so every pod sees every event (broadcast mode,
// RTH-FR-041). It returns immediately; the groups run until ctx is cancelled.
func (c *Consumer) Start(ctx context.Context, brokers []string, podID string) {
	if c.Log == nil {
		c.Log = slog.Default()
	}
	sasl, tlsOn := gckafka.SASLFromEnv(os.Getenv), gckafka.TLSFromEnv(os.Getenv)
	c.fanoutCG = gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
		Brokers: brokers,
		GroupID: "hub-fanout-" + podID, // unique per pod: broadcast, no shared offsets
		Topics:  FanoutTopics,
		Handler: c.handleFanout,
		Log:     c.Log,
		SASL:    sasl, TLS: tlsOn,
	})
	c.rbacCG = gckafka.NewConsumerGroup(gckafka.ConsumerConfig{
		Brokers: brokers,
		GroupID: "hub-rbac-" + podID,
		Topics:  []string{RevocationTopic},
		Handler: c.handleRevocation,
		Log:     c.Log,
		SASL:    sasl, TLS: tlsOn,
	})
	go c.fanoutCG.Run(ctx)
	go c.rbacCG.Run(ctx)
}

// Close stops the consumer readers.
func (c *Consumer) Close() {
	if c.fanoutCG != nil {
		_ = c.fanoutCG.Close()
	}
	if c.rbacCG != nil {
		_ = c.rbacCG.Close()
	}
}

// handleFanout routes one envelope to a hub topic and ingests it. It always
// returns nil: the hub never blocks a Kafka partition on delivery (RTH-FR-020).
func (c *Consumer) handleFanout(ctx context.Context, env gcevent.Envelope) error {
	topic, ok := c.Router.Route(env)
	if !ok {
		c.skip("unroutable")
		return nil
	}
	data := ClientBody(env)
	if len(data) > PayloadCap {
		c.skip("oversize")
		c.Log.Warn("oversize event skipped", "event_type", env.EventType, "bytes", len(data))
		return nil
	}
	ev := fanout.Event{
		ID:    env.EventID.String(),
		Topic: topic,
		Data:  data,
		Chat:  false,
	}
	if err := c.Sink.IngestKafka(ctx, env.TenantID.String(), topic, ev); err != nil {
		c.Log.Error("ingest failed", "topic", topic, "err", err)
	}
	return nil
}

// handleRevocation re-evaluates subscriptions affected by an rbac change
// (RTH-FR-013): it terminates subscriptions on the changed resource URN.
func (c *Consumer) handleRevocation(_ context.Context, env gcevent.Envelope) error {
	if env.ResourceURN == "" {
		return nil
	}
	n := c.Sink.Revoke(env.TenantID.String(), env.ResourceURN)
	if n > 0 {
		c.Log.Info("revoked subscriptions", "urn", env.ResourceURN, "count", n)
	}
	return nil
}

func (c *Consumer) skip(reason string) {
	if c.Skips != nil {
		c.Skips.Skipped(reason)
	}
}

// ClientBody is the JSON the client receives for a fan-out event: the event
// type, payload, and occurrence time (§5 wire example). Producers own payload
// semantics; the hub is transport.
func ClientBody(env gcevent.Envelope) json.RawMessage {
	b, _ := json.Marshal(map[string]any{
		"event_type":   env.EventType,
		"payload":      env.Payload,
		"occurred_at":  env.OccurredAt,
		"resource_urn": env.ResourceURN,
	})
	return b
}
