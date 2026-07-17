package events

import (
	"context"
	"log/slog"

	"github.com/google/uuid"
)

// BrokerReactions is the surface the consumers drive (implemented by
// exec.Broker) — BRD 05 §6 consumed events.
type BrokerReactions interface {
	HandleDatasetDeleted(ctx context.Context, tenant uuid.UUID, urn string)
	SuspendTenant(ctx context.Context, tenant uuid.UUID)
	ResumeTenant(tenant uuid.UUID)
}

// ResolverInvalidation lets the dataset consumer drop cached plans.
type ResolverInvalidation interface {
	Delete(tenant uuid.UUID, name string)
}

// Consumer dispatches inbound envelopes. In production it sits behind a
// Kafka consumer group with Redis event_id dedup and a DLQ
// (MASTER-FR-032/033); tests dispatch directly (CONVENTIONS: fakes from
// contracts, never live services).
type Consumer struct {
	Broker   BrokerReactions
	Resolver ResolverInvalidation

	// Dedup implements MASTER-FR-032 consumer-side idempotency; the default
	// in-memory set stands in for Redis SETNX.
	seen map[uuid.UUID]bool
}

// Handle processes one envelope; safe to replay (MASTER-FR-032).
func (c *Consumer) Handle(ctx context.Context, env Envelope) {
	if c.seen == nil {
		c.seen = map[uuid.UUID]bool{}
	}
	if c.seen[env.EventID] {
		return // duplicate delivery
	}
	c.seen[env.EventID] = true

	switch env.EventType {
	case "dataset.deleted":
		// Invalidate cached plans/results for the URN and fail queued
		// executions referencing it (§6).
		if name, ok := env.Payload["name"].(string); ok && c.Resolver != nil {
			c.Resolver.Delete(env.TenantID, name)
		}
		c.Broker.HandleDatasetDeleted(ctx, env.TenantID, env.ResourceURN)
	case "dataset.version_created":
		// Result-cache entries keyed to older versions naturally miss (the
		// cache key pins versions, QRY-FR-046); latest-plan caches refresh
		// on the resolver's short TTL. No action required.
	case "tenant.suspended":
		c.Broker.SuspendTenant(ctx, env.TenantID)
	case "tenant.resumed":
		c.Broker.ResumeTenant(env.TenantID)
	default:
		slog.Debug("ignoring event", "type", env.EventType)
	}
}
