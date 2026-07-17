package integration

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/segmentio/kafka-go"

	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/ingest"
	gcevent "github.com/windrose-ai/go-common/event"
)

func domainSub() (*domain.TopicSubscription, error) { return domain.NewSubscription("") }

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

// ingestDirect runs an envelope through the real Processor (real ClickHouse +
// chain), bypassing Kafka — used by the deterministic scenario tests.
func (h *harness) ingestDirect(t *testing.T, e domain.Envelope, topic string) {
	t.Helper()
	if err := h.proc.Handle(context.Background(), ingest.Source{Topic: topic}, e); err != nil {
		t.Fatalf("ingest %s: %v", e.EventType, err)
	}
}

// oboEvent builds an OBO envelope (user actor + via_agent).
func oboEvent(tenant uuid.UUID, user, agent, eventType, urn string) domain.Envelope {
	return domain.Envelope{
		EventID: uuid.New(), EventType: eventType, TenantID: tenant,
		Actor: domain.Actor{Type: "user", ID: user},
		ViaAgent: &domain.ViaAgent{AgentID: agent, Version: "1.0.0"},
		ResourceURN: urn, OccurredAt: time.Now().UTC(), TraceID: uuid.NewString(),
		Payload: map[string]any{"note": "obo action"},
	}
}

// autoEvent builds an autonomous agent envelope (agent actor).
func autoEvent(tenant uuid.UUID, agent, eventType, urn string) domain.Envelope {
	return domain.Envelope{
		EventID: uuid.New(), EventType: eventType, TenantID: tenant,
		Actor: domain.Actor{Type: "agent", ID: agent},
		ViaAgent: &domain.ViaAgent{AgentID: agent, Version: "1.0.0"},
		ResourceURN: urn, OccurredAt: time.Now().UTC(), TraceID: uuid.NewString(),
		Payload: map[string]any{"note": "autonomous action"},
	}
}

// produce publishes an envelope to a real topic via the go-common Kafka producer.
func (h *harness) produce(t *testing.T, topic string, e domain.Envelope) {
	t.Helper()
	env := gcevent.Envelope{
		EventID: e.EventID, EventType: e.EventType, TenantID: e.TenantID,
		Actor: gcevent.Actor{Type: e.Actor.Type, ID: e.Actor.ID},
		ResourceURN: e.ResourceURN, OccurredAt: e.OccurredAt, TraceID: e.TraceID, Payload: e.Payload,
	}
	if e.ViaAgent != nil {
		env.ViaAgent = &gcevent.ViaAgent{AgentID: e.ViaAgent.AgentID, Version: e.ViaAgent.Version}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := h.producer.Publish(ctx, topic, env); err != nil {
		t.Fatalf("produce to %s: %v", topic, err)
	}
}

// waitForDLQ polls a DLQ topic for a poison message carrying the given reason.
func (h *harness) waitForDLQ(t *testing.T, dlqTopic, reason string, timeout time.Duration) bool {
	t.Helper()
	r := kafka.NewReader(kafka.ReaderConfig{
		Brokers: h.brokers, Topic: dlqTopic, GroupID: "it-dlq-" + uuid.NewString()[:8],
		MinBytes: 1, MaxBytes: 10 << 20,
	})
	defer r.Close()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		msg, err := r.ReadMessage(ctx)
		cancel()
		if err != nil {
			continue
		}
		var poison gcevent.Envelope
		if json.Unmarshal(msg.Value, &poison) != nil {
			continue
		}
		if got, _ := poison.Payload["reason"].(string); got == reason {
			return true
		}
	}
	return false
}

// waitForMeta polls audit.events.v1 for a meta event of eventType whose actor id
// matches actorID (auditors-are-audited, AC-10).
func (h *harness) waitForMeta(t *testing.T, eventType, actorID string, timeout time.Duration) bool {
	t.Helper()
	r := kafka.NewReader(kafka.ReaderConfig{
		Brokers: h.brokers, Topic: "audit.events.v1", GroupID: "it-meta-" + uuid.NewString()[:8],
		MinBytes: 1, MaxBytes: 10 << 20, StartOffset: kafka.FirstOffset,
	})
	defer r.Close()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		msg, err := r.ReadMessage(ctx)
		cancel()
		if err != nil {
			continue
		}
		var e gcevent.Envelope
		if json.Unmarshal(msg.Value, &e) != nil {
			continue
		}
		if e.EventType == eventType && e.Actor.ID == actorID {
			return true
		}
	}
	return false
}
