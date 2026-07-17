package events

import (
	"context"
	"log/slog"
	"os"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
)

// AuditTopic is the platform audit stream consumed by audit-service
// (MASTER-FR-040).
const AuditTopic = "audit.events.v1"

// Auditor emits audit events for security-relevant occurrences (RTH-FR-012:
// subscribe denials emit security.topic_denied; admin kills likewise).
type Auditor interface {
	TopicDenied(ctx context.Context, tenant, subject, topic, reason string)
	AdminKill(ctx context.Context, tenant, subject, connID string)
}

// KafkaAuditor publishes audit envelopes through the real go-common producer.
type KafkaAuditor struct {
	prod *gckafka.Producer
	log  *slog.Logger
}

// NewKafkaAuditor builds a Kafka-backed auditor.
func NewKafkaAuditor(brokers []string, log *slog.Logger) *KafkaAuditor {
	cfg := gckafka.Config{
		Brokers: brokers,
		SASL:    gckafka.SASLFromEnv(os.Getenv), TLS: gckafka.TLSFromEnv(os.Getenv),
	}
	return &KafkaAuditor{prod: gckafka.NewProducer(cfg), log: log}
}

// TopicDenied emits security.topic_denied (RTH-FR-012 / AC-5).
func (a *KafkaAuditor) TopicDenied(ctx context.Context, tenant, subject, topic, reason string) {
	a.emit(ctx, tenant, "security.topic_denied", subject, map[string]any{"topic": topic, "reason": reason})
}

// AdminKill emits security.connection_killed.
func (a *KafkaAuditor) AdminKill(ctx context.Context, tenant, subject, connID string) {
	a.emit(ctx, tenant, "security.connection_killed", subject, map[string]any{"conn_id": connID})
}

func (a *KafkaAuditor) emit(ctx context.Context, tenant, eventType, subject string, payload map[string]any) {
	tid, err := uuid.Parse(tenant)
	if err != nil {
		tid = uuid.Nil
	}
	env := gcevent.New(eventType, tid, gcevent.Actor{Type: "user", ID: subject}, "", "", payload)
	if err := a.prod.Publish(ctx, AuditTopic, env); err != nil && a.log != nil {
		a.log.Warn("audit emit failed", "event_type", eventType, "err", err)
	}
}

// Close flushes the producer.
func (a *KafkaAuditor) Close() error { return a.prod.Close() }

// NoopAuditor drops audit events (dev without Kafka / unit tests).
type NoopAuditor struct{}

func (NoopAuditor) TopicDenied(context.Context, string, string, string, string) {}
func (NoopAuditor) AdminKill(context.Context, string, string, string)           {}
