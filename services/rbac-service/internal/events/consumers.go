package events

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/segmentio/kafka-go"
)

// ConsumerStore is the slice of store behavior the inbound handlers need.
type ConsumerStore interface {
	SeedTenantFromEvent(ctx context.Context, tenant uuid.UUID, actorID, traceID string) error
	CreateImplicitOwnerGrantFromEvent(ctx context.Context, tenant uuid.UUID, workspaceID uuid.UUID, resourceURN, creatorUserID, traceID string) error
	MarkDirty(ctx context.Context, tenant uuid.UUID, users []string, reason string) error
	RemoveUserProjection(ctx context.Context, tenant uuid.UUID, userID string) error
	GrantOwnerAdminFromEvent(ctx context.Context, tenant uuid.UUID, userID, actorID, traceID string) error
	AssignUserToGroupsFromEvent(ctx context.Context, tenant uuid.UUID, userID string, groups []string, actorID, traceID string) error
}

// Handler processes inbound events (BRD §6 Consumes):
//
//   - identity.events.v1 tenant.provisioned  -> seed system roles/groups/default workspace
//   - identity.events.v1 user.invited (is_owner=true only) -> grant the tenant
//     owner Admin group membership (BR-7 bootstrap; a regular admin-driven
//     invite carries no is_owner flag and is NOT auto-granted anything)
//   - identity.events.v1 user.created|activated -> warm projection (mark dirty)
//   - identity.events.v1 user.deactivated|deleted -> drop projection keys
//     (memberships retained for the 30d restore grace window)
//   - any *.events.v1 `<resource>.created` with a workspace_id -> implicit
//     owner grant for the creator (RBC-FR-032/AC-13)
type Handler struct {
	Store ConsumerStore
	Log   *slog.Logger
}

// stringSlice coerces a JSON-decoded payload value (list of strings, or a
// single string) into []string, dropping non-strings/blanks.
func stringSlice(v any) []string {
	switch x := v.(type) {
	case []string:
		return x
	case string:
		if x == "" {
			return nil
		}
		return []string{x}
	case []any:
		out := make([]string, 0, len(x))
		for _, e := range x {
			if s, ok := e.(string); ok && s != "" {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

// HandleEvent dispatches one decoded envelope. Idempotent: replays are safe
// (seeding and implicit grants are upsert-shaped; dedup happens upstream).
func (h *Handler) HandleEvent(ctx context.Context, env Envelope) error {
	switch env.EventType {
	case "tenant.provisioned":
		return h.Store.SeedTenantFromEvent(ctx, env.TenantID, env.Actor.ID, env.TraceID)
	case "user.invited":
		userID, _ := env.Payload["user_id"].(string)
		if env.Payload["is_owner"] == true {
			if userID == "" {
				if h.Log != nil {
					h.Log.Warn("user.invited is_owner=true but payload missing user_id",
						"tenant", env.TenantID, "event_id", env.EventID)
				}
				return nil
			}
			return h.Store.GrantOwnerAdminFromEvent(ctx, env.TenantID, userID, env.Actor.ID, env.TraceID)
		}
		// A regular admin-driven invite: honour any initial group assignment so the
		// user arrives with a role instead of zero permissions (IDN-FR-021). No
		// groups -> no-op (unchanged from before).
		groups := stringSlice(env.Payload["groups"])
		if userID == "" || len(groups) == 0 {
			return nil
		}
		return h.Store.AssignUserToGroupsFromEvent(ctx, env.TenantID, userID, groups, env.Actor.ID, env.TraceID)
	case "user.created", "user.activated":
		user, _ := env.Payload["user_id"].(string)
		if user == "" {
			user = env.Actor.ID
		}
		return h.Store.MarkDirty(ctx, env.TenantID, []string{user}, "user.warm")
	case "user.deactivated", "user.deleted":
		user, _ := env.Payload["user_id"].(string)
		if user == "" {
			return nil
		}
		return h.Store.RemoveUserProjection(ctx, env.TenantID, user)
	}
	// Cross-service *.created -> implicit creator grant (RBC-FR-032).
	if strings.HasSuffix(env.EventType, ".created") && env.ResourceURN != "" {
		wsRaw, _ := env.Payload["workspace_id"].(string)
		if wsRaw == "" {
			return nil // tenant-scoped resource: no workspace, no content grant
		}
		wsID, err := uuid.Parse(wsRaw)
		if err != nil {
			return fmt.Errorf("event %s: bad workspace_id %q", env.EventID, wsRaw)
		}
		creator := env.Actor.ID
		if env.Actor.Type != "user" {
			if obo, ok := env.Payload["obo_sub"].(string); ok && obo != "" {
				creator = obo
			}
		}
		return h.Store.CreateImplicitOwnerGrantFromEvent(ctx, env.TenantID, wsID, env.ResourceURN, creator, env.TraceID)
	}
	return nil
}

// KafkaConsumer wires a kafka-go reader group to the Handler with Redis
// SETNX event-id dedup (MASTER-FR-032) and DLQ routing after 5 retries
// (MASTER-FR-033).
type KafkaConsumer struct {
	Reader  *kafka.Reader
	Handler *Handler
	Rdb     redis.UniversalClient
	DLQ     EventPublisher
	Group   string
	Log     *slog.Logger
}

func NewKafkaConsumer(brokers []string, group string, topics []string, h *Handler, rdb redis.UniversalClient, dlq EventPublisher) *KafkaConsumer {
	return &KafkaConsumer{
		Reader: kafka.NewReader(kafka.ReaderConfig{
			Brokers:     brokers,
			GroupID:     group,
			GroupTopics: topics,
			MinBytes:    1,
			MaxBytes:    10 << 20,
		}),
		Handler: h,
		Rdb:     rdb,
		DLQ:     dlq,
		Group:   group,
		Log:     slog.Default(),
	}
}

// Run consumes until ctx is cancelled.
func (c *KafkaConsumer) Run(ctx context.Context) {
	for {
		msg, err := c.Reader.FetchMessage(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			c.Log.Error("kafka fetch failed", "err", err)
			continue
		}
		if err := c.processMessage(ctx, msg); err != nil {
			c.Log.Error("event processing exhausted retries; routed to DLQ", "topic", msg.Topic, "err", err)
		}
		if err := c.Reader.CommitMessages(ctx, msg); err != nil && ctx.Err() == nil {
			c.Log.Error("kafka commit failed", "err", err)
		}
	}
}

func (c *KafkaConsumer) processMessage(ctx context.Context, msg kafka.Message) error {
	var env Envelope
	if err := json.Unmarshal(msg.Value, &env); err != nil {
		return c.toDLQ(ctx, msg, fmt.Errorf("decode: %w", err))
	}
	// Dedup on event_id (24h TTL).
	if c.Rdb != nil {
		ok, err := c.Rdb.SetNX(ctx, "evt:dedup:"+env.EventID.String(), 1, 24*time.Hour).Result()
		if err == nil && !ok {
			return nil // duplicate — already processed
		}
	}
	var lastErr error
	backoff := 100 * time.Millisecond
	for attempt := 1; attempt <= 5; attempt++ {
		lastErr = c.Handler.HandleEvent(ctx, env)
		if lastErr == nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
		backoff *= 2
	}
	return c.toDLQ(ctx, msg, lastErr)
}

func (c *KafkaConsumer) toDLQ(ctx context.Context, msg kafka.Message, cause error) error {
	if c.DLQ == nil {
		return cause
	}
	env := NewEnvelope("consumer.poison", uuid.Nil, Actor{Type: "service", ID: "rbac-service"}, "", "",
		map[string]any{"topic": msg.Topic, "error": cause.Error(), "raw": string(msg.Value)})
	dlqTopic := fmt.Sprintf("%s.%s.dlq", msg.Topic, c.Group)
	if err := c.DLQ.Publish(ctx, dlqTopic, env); err != nil {
		return fmt.Errorf("dlq publish failed: %w (cause: %v)", err, cause)
	}
	return cause
}

func (c *KafkaConsumer) Close() error { return c.Reader.Close() }
