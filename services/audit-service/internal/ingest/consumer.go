package ingest

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/segmentio/kafka-go"

	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/meta"
	gcevent "github.com/windrose-ai/go-common/event"
	gckafka "github.com/windrose-ai/go-common/kafka"
)

// Deduper is the idempotency primitive (Redis SETNX). Satisfied by *redisx.Client.
type Deduper interface {
	SetNX(ctx context.Context, key string, ttl time.Duration) (bool, error)
}

// Consumer is audit-service's real Kafka consumer group (AUD-FR-001..006). It
// subscribes to every topic matching the regex subscription, decodes the master
// envelope, dedups on event_id, runs the ingest Processor, quarantines poison to
// a per-source-topic DLQ and pauses (never commits) on transient store outages
// so Kafka is the recovery buffer (BR-6). A periodic metadata rescan picks up
// newly created topics with zero deploys (AC-13).
type Consumer struct {
	Brokers        []string
	GroupID        string
	Sub            *domain.TopicSubscription
	Processor      *Processor
	Dedup          Deduper
	CH             recordExister
	DLQ            *gckafka.Producer
	Meta           *meta.Emitter
	Log            *slog.Logger
	RescanInterval time.Duration
	DedupTTL       time.Duration
}

// recordExister lets the dedup path confirm an event truly landed (closes the
// crash-between-SETNX-and-insert window so no acked event is lost).
type recordExister interface {
	GetEvent(ctx context.Context, tenant, eventID uuid.UUID) (*domain.Record, error)
}

func (c *Consumer) log() *slog.Logger {
	if c.Log != nil {
		return c.Log
	}
	return slog.Default()
}

// DiscoverTopics returns the in-scope topics currently present on the cluster.
func (c *Consumer) DiscoverTopics(ctx context.Context) ([]string, error) {
	conn, err := kafka.DialContext(ctx, "tcp", c.Brokers[0])
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	parts, err := conn.ReadPartitions()
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	for _, p := range parts {
		seen[p.Topic] = true
	}
	var topics []string
	for t := range seen {
		if c.Sub.Matches(t) {
			topics = append(topics, t)
		}
	}
	sort.Strings(topics)
	return topics, nil
}

// Run supervises the reader: (re)discovers topics and restarts consumption when
// the in-scope set changes.
func (c *Consumer) Run(ctx context.Context) {
	if c.RescanInterval <= 0 {
		c.RescanInterval = 60 * time.Second
	}
	for ctx.Err() == nil {
		topics, err := c.DiscoverTopics(ctx)
		if err != nil {
			c.log().Warn("topic discovery failed; retrying", "err", err)
			if !sleep(ctx, 5*time.Second) {
				return
			}
			continue
		}
		if len(topics) == 0 {
			c.log().Info("no subscribed topics yet; waiting", "pattern", domain.DefaultSubscriptionPattern)
			if !sleep(ctx, 5*time.Second) {
				return
			}
			continue
		}
		child, cancel := context.WithCancel(ctx)
		go c.watchTopics(child, cancel, topics)
		c.log().Info("audit ingest consuming", "topics", len(topics), "group", c.GroupID)
		c.consume(child, topics)
		cancel()
	}
}

// watchTopics cancels the child context when the in-scope topic set changes.
func (c *Consumer) watchTopics(ctx context.Context, cancel context.CancelFunc, current []string) {
	t := time.NewTicker(c.RescanInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			next, err := c.DiscoverTopics(ctx)
			if err != nil {
				continue
			}
			if !equalTopics(current, next) {
				c.log().Info("subscription changed; restarting consumer", "was", len(current), "now", len(next))
				cancel()
				return
			}
		}
	}
}

func (c *Consumer) consume(ctx context.Context, topics []string) {
	reader := kafka.NewReader(kafka.ReaderConfig{
		Brokers:     c.Brokers,
		GroupID:     c.GroupID,
		GroupTopics: topics,
		MinBytes:    1,
		MaxBytes:    10 << 20,
	})
	defer reader.Close()
	for {
		msg, err := reader.FetchMessage(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			c.log().Error("kafka fetch failed", "err", err)
			if !sleep(ctx, time.Second) {
				return
			}
			continue
		}
		if err := c.processMsg(ctx, msg); err != nil {
			// Transient store outage: pause — do not commit (BR-6). ctx was
			// cancelled (shutdown/rescan); leave the offset for redelivery.
			return
		}
		if err := reader.CommitMessages(ctx, msg); err != nil && ctx.Err() == nil {
			c.log().Error("kafka commit failed", "err", err)
		}
	}
}

// processMsg handles one message. Returns nil when the offset may be committed
// (success, true-duplicate, or DLQ'd); returns a non-nil error only when it must
// pause without committing (ctx cancelled mid transient-retry).
func (c *Consumer) processMsg(ctx context.Context, msg kafka.Message) error {
	var env domain.Envelope
	if err := json.Unmarshal(msg.Value, &env); err != nil {
		return c.toDLQ(ctx, msg, domain.ReasonPayloadDecode, err)
	}
	src := Source{Topic: msg.Topic, Partition: msg.Partition, Offset: msg.Offset}

	// Dedup (MASTER-FR-032) with crash-window recovery: a "duplicate" that is not
	// actually in the store is reprocessed so no acked event is lost.
	if c.Dedup != nil && env.EventID != uuid.Nil {
		ttl := c.DedupTTL
		if ttl <= 0 {
			ttl = 24 * time.Hour
		}
		fresh, err := c.Dedup.SetNX(ctx, "audit:dedup:"+env.EventID.String(), ttl)
		if err == nil && !fresh {
			if c.CH != nil && env.TenantID != uuid.Nil {
				if rec, gerr := c.CH.GetEvent(ctx, env.TenantID, env.EventID); gerr == nil && rec != nil {
					return nil // genuine duplicate, already stored
				}
			} else {
				return nil
			}
			// fall through: reprocess (recover a lost pre-insert crash window)
		}
	}

	backoff := 200 * time.Millisecond
	for {
		err := c.Processor.Handle(ctx, src, env)
		if err == nil {
			return nil
		}
		var term *TerminalError
		if asTerminal(err, &term) {
			return c.toDLQ(ctx, msg, term.Reason, term.Err)
		}
		// Transient: pause and retry the same message (BR-6).
		c.log().Warn("ingest transient error; pausing", "topic", msg.Topic, "err", err)
		if !sleep(ctx, backoff) {
			return ctx.Err()
		}
		if backoff < 5*time.Second {
			backoff *= 2
		}
	}
}

// toDLQ publishes a poison message to <topic>.<group>.dlq with the reason. It
// BLOCKS-retries the publish until it succeeds and returns nil (so the caller
// may commit), or returns a non-nil error only when ctx is cancelled — the
// caller must then NOT commit, so a poison event whose DLQ produce is failing is
// never silently lost (MEDIUM-1, US-7/AUD-FR-006).
func (c *Consumer) toDLQ(ctx context.Context, msg kafka.Message, reason string, cause error) error {
	if c.DLQ == nil {
		// No DLQ configured is a misconfiguration; refuse to drop — pause instead.
		c.log().Error("no DLQ configured; pausing to avoid data loss", "topic", msg.Topic, "reason", reason)
		<-ctx.Done()
		return ctx.Err()
	}
	tenant := uuid.Nil
	var env domain.Envelope
	if json.Unmarshal(msg.Value, &env) == nil {
		tenant = env.TenantID
	}
	causeStr := ""
	if cause != nil {
		causeStr = cause.Error()
	}
	poison := gcevent.New("audit.dlq.poison", tenant,
		gcevent.Actor{Type: "service", ID: "audit-service"}, "", kafkaHeaders(msg.Headers).trace(),
		map[string]any{
			"reason":       reason,
			"source_topic": msg.Topic,
			"partition":    msg.Partition,
			"offset":       msg.Offset,
			"error":        causeStr,
			"raw":          string(msg.Value),
		})
	dlqTopic := gckafka.DLQTopic(msg.Topic, c.GroupID)
	backoff := 200 * time.Millisecond
	for {
		if err := c.DLQ.Publish(ctx, dlqTopic, poison); err == nil {
			c.log().Warn("event quarantined to DLQ", "dlq", dlqTopic, "reason", reason)
			return nil
		} else {
			c.log().Error("DLQ publish failed; retrying (offset not committed)", "topic", dlqTopic, "err", err)
		}
		if !sleep(ctx, backoff) {
			return ctx.Err()
		}
		if backoff < 5*time.Second {
			backoff *= 2
		}
	}
}

// Redrive re-processes messages currently on a DLQ topic after the producer is
// fixed (AUD-FR-006, AC-15). It reads the raw envelopes, runs them back through
// the Processor and returns the count re-ingested. Bounded by max/timeout.
func (c *Consumer) Redrive(ctx context.Context, dlqTopic string, max int) (int, error) {
	reader := kafka.NewReader(kafka.ReaderConfig{
		Brokers:     c.Brokers,
		GroupID:     c.GroupID + ".redrive",
		GroupTopics: []string{dlqTopic},
		MinBytes:    1,
		MaxBytes:    10 << 20,
	})
	defer reader.Close()
	redriven := 0
	for redriven < max {
		fetchCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		msg, err := reader.FetchMessage(fetchCtx)
		cancel()
		if err != nil {
			break // drained (timeout) or ctx done
		}
		var poison gcevent.Envelope
		if json.Unmarshal(msg.Value, &poison) != nil {
			_ = reader.CommitMessages(ctx, msg)
			continue
		}
		raw, _ := poison.Payload["raw"].(string)
		srcTopic, _ := poison.Payload["source_topic"].(string)
		var env domain.Envelope
		if raw == "" || json.Unmarshal([]byte(raw), &env) != nil {
			_ = reader.CommitMessages(ctx, msg)
			continue
		}
		// Clear any dedup marker so the redriven event is reprocessed.
		if c.Dedup != nil && env.EventID != uuid.Nil {
			_, _ = c.Dedup.SetNX(ctx, "audit:dedup:"+env.EventID.String(), time.Millisecond)
		}
		src := Source{Topic: srcTopic, Partition: msg.Partition, Offset: msg.Offset}
		if err := c.Processor.Handle(ctx, src, env); err != nil {
			// Still bad: leave on DLQ (don't commit) and stop.
			return redriven, fmt.Errorf("redrive of %s failed: %w", env.EventID, err)
		}
		if err := reader.CommitMessages(ctx, msg); err != nil {
			return redriven, err
		}
		redriven++
	}
	return redriven, nil
}

func asTerminal(err error, target **TerminalError) bool {
	for err != nil {
		if t, ok := err.(*TerminalError); ok {
			*target = t
			return true
		}
		type unwrapper interface{ Unwrap() error }
		u, ok := err.(unwrapper)
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

func equalTopics(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func sleep(ctx context.Context, d time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-time.After(d):
		return true
	}
}

// headers is a tiny helper type so DLQ can extract a trace id if present.
type kafkaHeaders []kafka.Header

func (h kafkaHeaders) trace() string {
	for _, hdr := range h {
		if hdr.Key == "trace_id" {
			return strings.TrimSpace(string(hdr.Value))
		}
	}
	return ""
}
