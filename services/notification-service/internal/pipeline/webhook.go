package pipeline

import (
	"github.com/prometheus/client_golang/prometheus"

	"context"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/events"
)

// webhookDeliveryID is the deterministic delivery id for (event, endpoint) so
// the same event never double-delivers to an endpoint (BR-1) and retries can
// address the row without a lookup.
func webhookDeliveryID(tenant, event, endpoint uuid.UUID) uuid.UUID {
	return uuid.NewSHA1(uuid.NameSpaceOID, []byte("wh:"+tenant.String()+":"+event.String()+":"+endpoint.String()))
}

// deliverWebhooks fans an event to every active endpoint subscribing to its
// type (NOTIF-FR-022). A first delivery row is created idempotently; the first
// attempt runs inline, retries via the sweeper.
func (p *Pipeline) deliverWebhooks(ctx context.Context, env gcevent.Envelope) error {
	endpoints, err := p.Store.ActiveWebhooksForEvent(ctx, env.TenantID, env.EventType)
	if err != nil {
		return err
	}
	for _, ep := range endpoints {
		dID := webhookDeliveryID(env.TenantID, env.EventID, ep.ID)
		now := p.Now()
		del := &domain.Delivery{
			ID: dID, TenantID: env.TenantID, WebhookEndpointID: &ep.ID, EventID: env.EventID,
			Recipient: ep.ID.String(), Channel: domain.ChannelWebhook, Provider: "webhook",
			Status: domain.StatusQueued, NextRetryAt: &now, CreatedAt: now, UpdatedAt: now,
		}
		created, err := p.Store.InsertDelivery(ctx, del, map[string]any{"envelope": env})
		if err != nil {
			p.log().Warn("insert webhook delivery failed", "endpoint", ep.ID, "err", err)
			continue
		}
		if !created {
			continue // BR-1 dedup no-op (redelivery)
		}
		p.AttemptWebhook(ctx, ep, env, dID, 1)
	}
	return nil
}

// AttemptWebhook performs one signed delivery attempt and applies the retry /
// circuit-breaker state machine (NOTIF-FR-023). attempt is 1-based. Exported so
// the retry sweeper can drive due deliveries.
func (p *Pipeline) AttemptWebhook(ctx context.Context, ep *domain.WebhookEndpoint, env gcevent.Envelope, deliveryID uuid.UUID, attempt int) {
	now := p.Now()

	// Circuit open: suspend deliveries, queue for the next probe (NOTIF-FR-023).
	if ep.CircuitState == domain.CircuitOpen {
		probe := now.Add(webhook.ProbeInterval)
		if ep.CircuitOpenedAt != nil {
			probe = ep.CircuitOpenedAt.Add(webhook.ProbeInterval)
			for probe.Before(now) {
				probe = probe.Add(webhook.ProbeInterval)
			}
		}
		_ = p.Store.UpdateDeliveryStatus(ctx, env.TenantID, deliveryID, domain.StatusQueued, "", "circuit open", attempt-1, &probe, nil)
		return
	}

	// Per-endpoint rate limit (NOTIF-FR-031): token bucket. Backpressure, not a
	// failure — reschedule shortly.
	if p.Limiter != nil {
		if ok, err := p.Limiter.AllowWebhook(ctx, ep.ID.String()); err == nil && !ok {
			next := now.Add(time.Second)
			_ = p.Store.UpdateDeliveryStatus(ctx, env.TenantID, deliveryID, domain.StatusQueued, "", "rate limited", attempt-1, &next, nil)
			return
		}
	}

	status, err := p.Webhook.Deliver(ctx, ep, env, now)
	if err == nil {
		_ = p.Store.UpdateDeliveryStatus(ctx, env.TenantID, deliveryID, domain.StatusDelivered, "", "", attempt, nil, nil)
		p.bump(func(m *Metrics) prometheus.Counter { return m.WebhookSent })
		p.onWebhookSuccess(ctx, ep, env)
		return
	}
	_ = status
	p.bump(func(m *Metrics) prometheus.Counter { return m.WebhookFailed })
	p.onWebhookFailure(ctx, ep, env, deliveryID, attempt, err.Error())
}

// onWebhookSuccess closes a previously open/half-open circuit and flushes the
// endpoint's queued deliveries in event-id order (NOTIF-FR-023, AC-5).
func (p *Pipeline) onWebhookSuccess(ctx context.Context, ep *domain.WebhookEndpoint, env gcevent.Envelope) {
	wasClosed := ep.CircuitState == domain.CircuitClosed && ep.ConsecutiveFailures == 0
	if wasClosed {
		return
	}
	ep.CircuitState = domain.CircuitClosed
	ep.CircuitOpenedAt = nil
	ep.ConsecutiveFailures = 0
	if err := p.Store.UpdateWebhook(ctx, ep); err != nil {
		p.log().Warn("close circuit failed", "endpoint", ep.ID, "err", err)
		return
	}
	p.emit(ctx, env, events.EvCircuitClosed, map[string]any{"endpoint_id": ep.ID.String()})

	// Flush queued deliveries for this endpoint in order, now that it recovered.
	queued, err := p.queuedForEndpoint(ctx, ep.TenantID, ep.ID)
	if err != nil {
		return
	}
	for _, dd := range queued {
		if dd.Delivery.ID == webhookDeliveryID(env.TenantID, env.EventID, ep.ID) {
			continue // just delivered
		}
		p.AttemptWebhook(ctx, ep, dd.Envelope, dd.Delivery.ID, dd.Delivery.Attempts+1)
	}
}

// queuedForEndpoint is set by the server to the store's QueuedForEndpoint (kept
// as a field so the pipeline package needn't import the store concretely).
func (p *Pipeline) queuedForEndpoint(ctx context.Context, tenant, endpoint uuid.UUID) ([]queuedDelivery, error) {
	if p.QueuedForEndpoint == nil {
		return nil, nil
	}
	return p.QueuedForEndpoint(ctx, tenant, endpoint)
}

// onWebhookFailure schedules the next retry, opens the circuit after the
// threshold, and dead-letters when the schedule is exhausted (NOTIF-FR-023).
func (p *Pipeline) onWebhookFailure(ctx context.Context, ep *domain.WebhookEndpoint, env gcevent.Envelope, deliveryID uuid.UUID, attempt int, cause string) {
	now := p.Now()

	// A failed half-open probe reopens the circuit with a fresh probe timer.
	if ep.CircuitState == domain.CircuitHalfOpen {
		ep.CircuitState = domain.CircuitOpen
		ep.CircuitOpenedAt = &now
		_ = p.Store.UpdateWebhook(ctx, ep)
	}

	next, ok := webhook.NextRetryAt(now, attempt)
	if ok {
		_ = p.Store.UpdateDeliveryStatus(ctx, env.TenantID, deliveryID, domain.StatusQueued, "", cause, attempt, &next, nil)
	} else {
		// Schedule exhausted → mark failed (dead-lettered).
		_ = p.Store.UpdateDeliveryStatus(ctx, env.TenantID, deliveryID, domain.StatusFailed, "", cause, attempt, nil, nil)
	}

	ep.ConsecutiveFailures++
	if ep.CircuitState == domain.CircuitClosed && ep.ConsecutiveFailures >= webhook.CircuitOpenThreshold {
		ep.CircuitState = domain.CircuitOpen
		ep.CircuitOpenedAt = &now
		if err := p.Store.UpdateWebhook(ctx, ep); err == nil {
			p.bump(func(m *Metrics) prometheus.Counter { return m.CircuitOpened })
			p.emit(ctx, env, events.EvCircuitOpened, map[string]any{"endpoint_id": ep.ID.String(), "consecutive_failures": ep.ConsecutiveFailures})
		}
		return
	}
	if err := p.Store.UpdateWebhook(ctx, ep); err != nil {
		p.log().Warn("update endpoint failures failed", "endpoint", ep.ID, "err", err)
	}
}
