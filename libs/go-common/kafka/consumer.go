package kafka

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"github.com/google/uuid"
	"github.com/segmentio/kafka-go"

	"github.com/windrose-ai/go-common/event"
)

// Handler processes one decoded envelope. Returning an error triggers retry;
// after MaxRetries the message is routed to the DLQ. Handlers must be
// idempotent (replays are safe — the consumer dedups on event_id but at-least-
// once delivery can still redeliver on crash between handle and commit).
type Handler func(ctx context.Context, env event.Envelope) error

// Deduper is the idempotency check (MASTER-FR-032): SetNX returns true when the
// key was newly set (first delivery), false when it already existed (dup).
// Satisfied by *redisx.Client.
type Deduper interface {
	SetNX(ctx context.Context, key string, ttl time.Duration) (bool, error)
}

// DLQPublisher publishes poison messages to the dead-letter topic. Satisfied by
// *Producer.
type DLQPublisher interface {
	Publish(ctx context.Context, topic string, env event.Envelope) error
}

// ConsumerConfig configures a ConsumerGroup.
type ConsumerConfig struct {
	Brokers    []string
	GroupID    string
	Topics     []string
	Handler    Handler
	Dedup      Deduper      // nil disables dedup
	DLQ        DLQPublisher // nil disables DLQ (poison then blocks retry loop budget)
	MaxRetries int          // default 5 (MASTER-FR-033)
	DedupTTL   time.Duration
	Log        *slog.Logger
	// SASL/TLS configure broker authentication — see Config's fields on the
	// Producer side; the zero value (nil/false) is the existing unauthenticated
	// self-hosted Kafka/Redpanda default.
	SASL *SASLConfig
	TLS  bool
}

// ConsumerGroup consumes topics as a Kafka consumer group with manual commit,
// Redis SETNX dedup, retry-with-exponential-backoff, and DLQ routing after
// MaxRetries (MASTER-FR-032/033). The DLQ topic is "<topic>.<group>.dlq".
type ConsumerGroup struct {
	reader     *kafka.Reader
	handler    Handler
	dedup      Deduper
	dlq        DLQPublisher
	group      string
	brokers    []string
	topics     []string
	maxRetries int
	dedupTTL   time.Duration
	log        *slog.Logger
	transport  *kafka.Transport // nil unless SASL/TLS configured; reused for ensureTopics' admin Client
}

// NewConsumerGroup builds a ConsumerGroup.
func NewConsumerGroup(cfg ConsumerConfig) *ConsumerGroup {
	if cfg.MaxRetries <= 0 {
		cfg.MaxRetries = 5
	}
	if cfg.DedupTTL <= 0 {
		cfg.DedupTTL = 24 * time.Hour
	}
	if cfg.Log == nil {
		cfg.Log = slog.Default()
	}
	return &ConsumerGroup{
		reader: kafka.NewReader(kafka.ReaderConfig{
			Brokers:     cfg.Brokers,
			GroupID:     cfg.GroupID,
			GroupTopics: cfg.Topics,
			MinBytes:    1,
			MaxBytes:    10 << 20,
			// Dialer is a concrete *kafka.Dialer field (not an interface), so a
			// genuine nil here safely falls back to kafka-go's DefaultDialer.
			Dialer: buildDialer(cfg.SASL, cfg.TLS),
		}),
		handler:    cfg.Handler,
		dedup:      cfg.Dedup,
		dlq:        cfg.DLQ,
		group:      cfg.GroupID,
		brokers:    cfg.Brokers,
		topics:     cfg.Topics,
		maxRetries: cfg.MaxRetries,
		dedupTTL:   cfg.DedupTTL,
		log:        cfg.Log,
		transport:  buildTransport(cfg.SASL, cfg.TLS),
	}
}

// Run consumes until ctx is cancelled.
func (c *ConsumerGroup) Run(ctx context.Context) {
	// Ensure the subscribed topics exist before the group joins. A consumer
	// group that joins a not-yet-created topic is assigned no partitions and
	// never picks the topic up once it is auto-created by a producer — it would
	// then silently consume nothing forever. Creating the topics up front (idem-
	// potent) guarantees the group is always assigned its partitions on join.
	c.ensureTopics(ctx)
	for {
		msg, err := c.reader.FetchMessage(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			c.log.Error("kafka fetch failed", "err", err)
			continue
		}
		if err := c.process(ctx, msg); err != nil {
			// process could not safely dispose of this message: its DLQ publish
			// itself failed (or ctx was cancelled). Do NOT commit — leaving the
			// offset uncommitted means Kafka redelivers the event instead of it
			// being permanently lost (data-loss fix). On shutdown just return.
			if ctx.Err() != nil {
				return
			}
			c.log.Error("event not disposed; offset left uncommitted for redelivery", "topic", msg.Topic, "err", err)
			continue
		}
		if err := c.reader.CommitMessages(ctx, msg); err != nil && ctx.Err() == nil {
			c.log.Error("kafka commit failed", "err", err)
		}
	}
}

// ensureTopics creates the subscribed topics if absent (idempotent). It is
// best-effort: a broker that already has the topic, or a transient failure that
// resolves before the first fetch, must not block startup.
func (c *ConsumerGroup) ensureTopics(ctx context.Context) {
	if len(c.brokers) == 0 || len(c.topics) == 0 {
		return
	}
	tctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	kc := &kafka.Client{Addr: kafka.TCP(c.brokers...)}
	// Transport is a RoundTripper interface field: only assign when non-nil
	// (see NewProducer's identical guard) so an unauthenticated group doesn't
	// regress to a wrapped-nil interface.
	if c.transport != nil {
		kc.Transport = c.transport
	}
	cfgs := make([]kafka.TopicConfig, 0, len(c.topics))
	for _, t := range c.topics {
		cfgs = append(cfgs, kafka.TopicConfig{Topic: t, NumPartitions: 1, ReplicationFactor: 1})
	}
	if _, err := kc.CreateTopics(tctx, &kafka.CreateTopicsRequest{Topics: cfgs}); err != nil {
		// Already-exists and races are fine; log anything else and continue.
		c.log.Warn("ensure consumer topics", "topics", c.topics, "err", err)
	}
}

// process handles one message: decode, dedup, retry, DLQ. The dedup claim is
// only kept when the handler ultimately succeeds; on failure the claim is
// released so a redelivery (or DLQ redrive) reprocesses the event rather than
// the SETNX permanently masking a never-applied change (MASTER-FR-032).
func (c *ConsumerGroup) process(ctx context.Context, msg kafka.Message) error {
	var env event.Envelope
	if err := json.Unmarshal(msg.Value, &env); err != nil {
		return c.toDLQ(ctx, msg, fmt.Errorf("decode: %w", err))
	}
	dedupKey := "evt:dedup:" + env.EventID.String()
	if c.dedup != nil {
		fresh, err := c.dedup.SetNX(ctx, dedupKey, c.dedupTTL)
		if err == nil && !fresh {
			return nil // already processed (genuine duplicate)
		}
	}
	var lastErr error
	backoff := 100 * time.Millisecond
	for attempt := 1; attempt <= c.maxRetries; attempt++ {
		if lastErr = c.handler(ctx, env); lastErr == nil {
			return nil // success: keep the dedup claim so replays are no-ops
		}
		if attempt == c.maxRetries {
			break
		}
		select {
		case <-ctx.Done():
			c.releaseDedup(ctx, dedupKey)
			return ctx.Err()
		case <-time.After(backoff):
		}
		backoff *= 2
	}
	// Handler exhausted retries: release the dedup claim so the event can be
	// reprocessed (redelivery/redrive) instead of being silently dropped.
	c.releaseDedup(ctx, dedupKey)
	return c.toDLQ(ctx, msg, lastErr)
}

// dedupReleaser is the optional release capability (satisfied by *redisx.Client
// via Del). Kept as a narrow optional interface so existing Deduper
// implementations that cannot release stay compatible.
type dedupReleaser interface {
	Del(ctx context.Context, keys ...string) error
}

// releaseDedup drops the dedup claim for a key when the handler did not succeed,
// so the event is not permanently masked. No-op when the deduper cannot release.
func (c *ConsumerGroup) releaseDedup(ctx context.Context, key string) {
	if c.dedup == nil {
		return
	}
	if r, ok := c.dedup.(dedupReleaser); ok {
		// Use a short independent timeout so a cancelled ctx still releases.
		rctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
		defer cancel()
		if err := r.Del(rctx, key); err != nil {
			c.log.Warn("dedup release failed", "key", key, "err", err)
		}
	}
}

// DLQTopic is the dead-letter topic name for a source topic and group.
func DLQTopic(topic, group string) string {
	return fmt.Sprintf("%s.%s.dlq", topic, group)
}

// toDLQ quarantines a poison message to <topic>.<group>.dlq. It BLOCKS-retries
// the publish with bounded exponential backoff and returns nil once the message
// is safely on the DLQ (the caller may then commit). It returns a non-nil error
// ONLY when ctx is cancelled before the publish succeeds — the caller must then
// NOT commit, so a poison event whose DLQ produce keeps failing is redelivered
// rather than silently dropped (mirrors audit-service's bespoke consumer). A nil
// DLQ is a misconfiguration; rather than drop the event it pauses on ctx so the
// offset is never committed.
func (c *ConsumerGroup) toDLQ(ctx context.Context, msg kafka.Message, cause error) error {
	causeStr := ""
	if cause != nil {
		causeStr = cause.Error()
	}
	if c.dlq == nil {
		c.log.Error("no DLQ configured; pausing to avoid event loss", "topic", msg.Topic, "cause", causeStr)
		<-ctx.Done()
		return ctx.Err()
	}
	env := event.New("consumer.poison", uuid.Nil, event.Actor{Type: "service", ID: c.group}, "", "",
		map[string]any{"topic": msg.Topic, "error": causeStr, "raw": string(msg.Value)})
	dlqTopic := DLQTopic(msg.Topic, c.group)
	backoff := 200 * time.Millisecond
	for {
		if err := c.dlq.Publish(ctx, dlqTopic, env); err == nil {
			c.log.Warn("event quarantined to DLQ", "dlq", dlqTopic, "cause", causeStr)
			return nil
		} else {
			c.log.Error("DLQ publish failed; retrying (offset not committed)", "dlq", dlqTopic, "err", err, "cause", causeStr)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
		if backoff < 5*time.Second {
			backoff *= 2
		}
	}
}

// Close closes the reader.
func (c *ConsumerGroup) Close() error { return c.reader.Close() }
