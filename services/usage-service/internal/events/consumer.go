package events

import (
	"context"
	"log/slog"
	"os"

	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
	"github.com/windrose-ai/go-common/redisx"
)

// IngestHandler processes one decoded metering envelope (implemented by
// ingest.Pipeline).
type IngestHandler interface {
	Handle(ctx context.Context, env gcevent.Envelope) error
}

// IngestConsumer is the real inbound consumer group over Redpanda: it consumes
// the metering topics (USG-FR-010), dedups on event_id via Redis SETNX
// (MASTER-FR-032), and routes poison messages to <topic>.usage-ingest.dlq after
// 5 retries (MASTER-FR-033). No in-memory fallback — this is the runtime path.
type IngestConsumer struct {
	group *gckafka.ConsumerGroup
}

// NewIngestConsumer wires the go-common consumer group to the ingest pipeline.
func NewIngestConsumer(brokers []string, redis *redisx.Client, dlq *gckafka.Producer, h IngestHandler) *IngestConsumer {
	cfg := gckafka.ConsumerConfig{
		Brokers: brokers,
		GroupID: IngestGroup,
		Topics:  ConsumedTopics(),
		Handler: h.Handle,
		Dedup:   redis, // *redisx.Client satisfies Deduper
		DLQ:     dlq,   // *gckafka.Producer satisfies DLQPublisher
		Log:     slog.Default(),
		SASL:    gckafka.SASLFromEnv(os.Getenv), TLS: gckafka.TLSFromEnv(os.Getenv),
	}
	return &IngestConsumer{group: gckafka.NewConsumerGroup(cfg)}
}

// Run consumes until ctx is cancelled.
func (c *IngestConsumer) Run(ctx context.Context) { c.group.Run(ctx) }

// Close closes the reader.
func (c *IngestConsumer) Close() error { return c.group.Close() }
