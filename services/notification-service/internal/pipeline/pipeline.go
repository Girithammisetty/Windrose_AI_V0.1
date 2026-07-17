// Package pipeline is notification-service's core: for each consumed event it
// runs mapping lookup → audience resolution → per-recipient preference filter →
// rate-limit/digest gate → render → deliver → record (BRD 19 §5). Every stage
// is idempotent; the unique delivery key makes Kafka redelivery a no-op (BR-1).
package pipeline

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/channels/inapp"
	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/events"
	"github.com/windrose-ai/notification-service/internal/preferences"
	"github.com/windrose-ai/notification-service/internal/registry"
	"github.com/windrose-ai/notification-service/internal/subscriptions"
)

// RateLimiter is the per-recipient/per-tenant limit surface (NOTIF-FR-031/032),
// satisfied by *ratelimit.Limiter; faked in unit tests.
type RateLimiter interface {
	AllowEmail(ctx context.Context, tenant, user string) (bool, error)
	AllowTenantEmail(ctx context.Context, tenant string) (bool, error)
	AllowWebhook(ctx context.Context, endpointID string) (bool, error)
}

// Store is the persistence surface the pipeline needs (satisfied by *store.PG;
// faked in unit tests).
type Store interface {
	ActiveRulesForEvent(ctx context.Context, tenant uuid.UUID) ([]*domain.SubscriptionRule, error)
	GetPreferences(ctx context.Context, tenant uuid.UUID, user string) (*domain.UserPreferences, error)
	IsSuppressed(ctx context.Context, tenant uuid.UUID, emailHash string) (bool, error)
	ResolveTemplate(ctx context.Context, tenant uuid.UUID, key, channel, locale string) (*domain.Template, error)
	InsertNotificationTx(ctx context.Context, n *domain.Notification, d *domain.Delivery, payload map[string]any, envs []gcevent.Envelope) (bool, error)
	InsertDelivery(ctx context.Context, d *domain.Delivery, payload map[string]any) (bool, error)
	UpdateDeliveryStatus(ctx context.Context, tenant, id uuid.UUID, status, providerMsgID, lastErr string, attempts int, nextRetry *time.Time, envs []gcevent.Envelope) error
	AppendDigest(ctx context.Context, tenant uuid.UUID, user, channel, class string, item domain.DigestItem, windowEnd time.Time) (int, error)
	MarkDigestDue(ctx context.Context, tenant uuid.UUID, user, channel, class string, at time.Time) error
	ActiveWebhooksForEvent(ctx context.Context, tenant uuid.UUID, eventType string) ([]*domain.WebhookEndpoint, error)
	CountInAppToday(ctx context.Context, tenant uuid.UUID, user string) (int, error)
	UpdateWebhook(ctx context.Context, e *domain.WebhookEndpoint) error
	EmitAudit(ctx context.Context, env gcevent.Envelope) error
}

// Pipeline wires the notification pipeline dependencies.
type Pipeline struct {
	Store    Store
	Registry *registry.Registry
	Groups   GroupResolver
	Dir      UserDirectory
	Email    *email.Sender
	Webhook  *webhook.Sender
	Realtime inapp.Publisher
	Limiter  RateLimiter
	Metrics  *Metrics
	Log      *slog.Logger

	// DigestWindow is the default digest flush window (NOTIF-FR-030, default 1h).
	DigestWindow time.Duration
	// InAppDailyCap is the per-user in-app cap (NOTIF-FR-031, default 500).
	InAppDailyCap int

	// QueuedForEndpoint optionally returns an endpoint's queued webhook
	// deliveries in event-id order (in-order flush on circuit close, AC-5).
	QueuedForEndpoint func(ctx context.Context, tenant, endpoint uuid.UUID) ([]queuedDelivery, error)

	now func() time.Time
}

// queuedDelivery is a queued webhook delivery with its envelope to re-POST.
type queuedDelivery struct {
	Delivery domain.Delivery
	Envelope gcevent.Envelope
}

// QueuedDelivery is the exported shape for wiring QueuedForEndpoint from the
// store without a package cycle.
type QueuedDelivery struct {
	Delivery domain.Delivery
	Envelope gcevent.Envelope
}

// SetQueuedForEndpoint wires the store's queued-delivery lookup.
func (p *Pipeline) SetQueuedForEndpoint(f func(ctx context.Context, tenant, endpoint uuid.UUID) ([]QueuedDelivery, error)) {
	p.QueuedForEndpoint = func(ctx context.Context, tenant, endpoint uuid.UUID) ([]queuedDelivery, error) {
		out, err := f(ctx, tenant, endpoint)
		if err != nil {
			return nil, err
		}
		res := make([]queuedDelivery, len(out))
		for i, d := range out {
			res[i] = queuedDelivery{Delivery: d.Delivery, Envelope: d.Envelope}
		}
		return res, nil
	}
}

// Now returns the pipeline clock (overridable in tests).
func (p *Pipeline) Now() time.Time {
	if p.now != nil {
		return p.now()
	}
	return time.Now().UTC()
}

// SetClock overrides the clock (tests).
func (p *Pipeline) SetClock(f func() time.Time) { p.now = f }

func (p *Pipeline) log() *slog.Logger {
	if p.Log != nil {
		return p.Log
	}
	return slog.Default()
}

func (p *Pipeline) digestWindow() time.Duration {
	if p.DigestWindow > 0 {
		return p.DigestWindow
	}
	return time.Hour
}

func (p *Pipeline) inAppCap() int {
	if p.InAppDailyCap > 0 {
		return p.InAppDailyCap
	}
	return 500
}

// mapping aliases the registry mapping type for brevity in delivery methods.
type mapping = registry.Mapping

// plan is a resolved recipient's channels + the rule ids that matched.
type plan struct {
	channels     map[string]bool
	matchedRules []uuid.UUID
}

// Process runs the pipeline for one consumed event (idempotent, resumable).
func (p *Pipeline) Process(ctx context.Context, env gcevent.Envelope) error {
	m, ok := p.Registry.Lookup(env.EventType)
	if !ok {
		if p.Metrics != nil {
			p.Metrics.Unmapped.Inc()
		}
		return nil // unmapped events are ignored (NOTIF-FR-002)
	}

	recipients, webhookOnly := p.resolveAudience(ctx, env, m)
	_ = webhookOnly

	for user, pl := range recipients {
		if err := p.deliverToRecipient(ctx, env, m, user, pl); err != nil {
			// A per-recipient failure must not abort the whole event; log and
			// continue. The delivery row (if written) drives retries.
			p.log().Warn("recipient delivery failed", "event_type", env.EventType, "user", user, "err", err)
		}
	}

	// Webhook channel: any tenant endpoint subscribed to this event type.
	if err := p.deliverWebhooks(ctx, env); err != nil {
		p.log().Warn("webhook delivery pass failed", "event_type", env.EventType, "err", err)
	}
	return nil
}

// resolveAudience computes recipients (explicit principals ∪ matching rule
// subjects), expands groups (≤500), and dedups per user (NOTIF-FR-003/011).
func (p *Pipeline) resolveAudience(ctx context.Context, env gcevent.Envelope, m registry.Mapping) (map[string]*plan, bool) {
	out := map[string]*plan{}
	workspaceID, _ := env.Payload["workspace_id"].(string)

	add := func(user string, channels []string, ruleID *uuid.UUID) {
		if user == "" {
			return
		}
		pl := out[user]
		if pl == nil {
			pl = &plan{channels: map[string]bool{}}
			out[user] = pl
		}
		for _, c := range channels {
			pl.channels[c] = true
		}
		if ruleID != nil {
			pl.matchedRules = append(pl.matchedRules, *ruleID)
		}
	}

	// Explicit/default audience from the mapping.
	for _, ref := range m.Audience {
		if ref.PayloadField != "" {
			for _, u := range registry.ExtractPayloadPrincipals(env, ref.PayloadField) {
				add(u, m.Channels, nil)
			}
		}
		if ref.Role != "" {
			ids, err := p.Groups.Role(ctx, env.TenantID.String(), workspaceID, ref.Role)
			if err != nil {
				p.log().Warn("role audience resolve failed", "role", ref.Role, "err", err)
			}
			for _, u := range ids {
				add(u, m.Channels, nil)
			}
		}
	}

	// Subscription rules (NOTIF-FR-011): all matching active rules fire.
	rules, err := p.Store.ActiveRulesForEvent(ctx, env.TenantID)
	if err != nil {
		p.log().Warn("rule load failed", "err", err)
	}
	for _, rule := range rules {
		if !subscriptions.Matches(rule, env) {
			continue
		}
		ruleID := rule.ID
		var subjects []string
		switch rule.SubjectType {
		case domain.SubjectGroup:
			ids, err := p.Groups.Group(ctx, env.TenantID.String(), rule.SubjectID)
			if err != nil {
				p.log().Warn("group expand failed", "group", rule.SubjectID, "err", err)
			}
			subjects = ids
		default:
			subjects = []string{rule.SubjectID}
		}
		for _, u := range subjects {
			add(u, rule.Channels, &ruleID)
		}
	}

	// Cap audience at 500 (NOTIF-FR-013): truncate + emit audience.truncated.
	if len(out) > MaxAudience {
		trimmed := map[string]*plan{}
		i := 0
		for u, pl := range out {
			if i >= MaxAudience {
				break
			}
			trimmed[u] = pl
			i++
		}
		p.emit(ctx, env, events.EvAudienceTruncated, map[string]any{"event_type": env.EventType, "resolved": len(out), "kept": MaxAudience})
		out = trimmed
	}
	return out, false
}

// deliverToRecipient applies preferences then delivers on each channel.
func (p *Pipeline) deliverToRecipient(ctx context.Context, env gcevent.Envelope, m registry.Mapping, user string, pl *plan) error {
	prefs, err := p.Store.GetPreferences(ctx, env.TenantID, user)
	if err != nil {
		return err
	}
	channels := make([]string, 0, len(pl.channels))
	for c := range pl.channels {
		channels = append(channels, c)
	}
	dec := preferences.Resolve(prefs, channels, env.EventType, m.Class, env.ResourceURN, m.Class, p.Now())

	info, err := p.Dir.Lookup(ctx, env.TenantID.String(), user)
	if err != nil {
		// Transient directory failure (e.g. Redis down): propagate so the event
		// is retried/DLQ'd rather than delivered with a fabricated address.
		return err
	}
	locale := info.Locale
	if locale == "" {
		locale = "en"
	}
	data := templateData(env)

	for _, ch := range dec.Channels {
		switch ch {
		case domain.ChannelInApp:
			if err := p.deliverInApp(ctx, env, m, user, pl, data, locale); err != nil {
				return err
			}
		case domain.ChannelEmail:
			if err := p.deliverEmail(ctx, env, m, user, info, data, locale, dec); err != nil {
				return err
			}
		}
	}
	return nil
}

// emit writes an ops event to the outbox (best-effort).
func (p *Pipeline) emit(ctx context.Context, env gcevent.Envelope, eventType string, payload map[string]any) {
	e := events.New(eventType, env.TenantID, gcevent.Actor{Type: "service", ID: "notification-service"}, env.ResourceURN, env.TraceID, payload)
	if err := p.Store.EmitAudit(ctx, e); err != nil {
		p.log().Warn("emit failed", "type", eventType, "err", err)
	}
}

// EmitOps writes an ops/audit event to the outbox from outside a consumed event
// (used by the worker for endpoint.disabled etc.).
func (p *Pipeline) EmitOps(ctx context.Context, tenant, resource uuid.UUID, eventType string, payload map[string]any) {
	e := events.New(eventType, tenant, gcevent.Actor{Type: "service", ID: "notification-service"}, resource.String(), "", payload)
	if err := p.Store.EmitAudit(ctx, e); err != nil {
		p.log().Warn("emit ops failed", "type", eventType, "err", err)
	}
}

func emailHash(addr string) string {
	sum := sha256.Sum256([]byte(strings.ToLower(addr)))
	return hex.EncodeToString(sum[:])
}
