package pipeline

import (
	"github.com/prometheus/client_golang/prometheus"

	"context"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/events"
)

// deliverInApp persists an in-app notification + its delivery row and pushes a
// realtime event (NOTIF-FR-020). Over the daily cap the overflow collapses into
// one rollup row per day (NOTIF-FR-031, BR-2 preserves count fidelity).
func (p *Pipeline) deliverInApp(ctx context.Context, env gcevent.Envelope, m mapping, user string, pl *plan, data map[string]any, locale string) error {
	title, _, _ := p.renderFor(ctx, env.TenantID, m.TemplateKey, domain.ChannelInApp, locale, data, defaultTitle(env.EventType))
	deepLink, _ := data["DeepLink"].(string)

	count, err := p.Store.CountInAppToday(ctx, env.TenantID, user)
	if err != nil {
		return err
	}
	if count >= p.inAppCap() {
		return p.rollup(ctx, env, user)
	}

	nID := domain.NewID()
	dID := domain.NewID()
	notif := &domain.Notification{
		ID: nID, TenantID: env.TenantID, UserID: user, EventID: env.EventID, EventType: env.EventType,
		SeverityClass: m.Class, Title: title, Body: title, ResourceURN: env.ResourceURN, DeepLink: deepLink,
		MatchedRules: pl.matchedRules, CreatedAt: p.Now(),
	}
	del := &domain.Delivery{
		ID: dID, TenantID: env.TenantID, NotificationID: &nID, EventID: env.EventID, Recipient: user,
		Channel: domain.ChannelInApp, Provider: "in_app", Status: domain.StatusDelivered, CreatedAt: p.Now(), UpdatedAt: p.Now(),
	}
	created, err := p.Store.InsertNotificationTx(ctx, notif, del, map[string]any{"notification_id": nID.String()},
		[]gcevent.Envelope{events.New(events.EvNotificationCreated, env.TenantID,
			gcevent.Actor{Type: "service", ID: "notification-service"}, env.ResourceURN, env.TraceID,
			map[string]any{"notification_id": nID.String(), "user_id": user, "event_type": env.EventType, "severity": m.Class})})
	if err != nil {
		return err
	}
	if !created {
		return nil // BR-1 dedup no-op
	}
	p.bump(func(m *Metrics) prometheus.Counter { return m.InAppCreated })

	// Realtime push (NOTIF-FR-020, AC-1).
	if p.Realtime != nil {
		_ = p.Realtime.Push(ctx, env.TenantID.String(), user, map[string]any{
			"id": nID.String(), "event_type": env.EventType, "title": title, "deep_link": deepLink,
			"severity": m.Class, "resource_urn": env.ResourceURN,
		})
	}
	return nil
}

// rollup writes/updates a single per-day "N more events" row (NOTIF-FR-031).
func (p *Pipeline) rollup(ctx context.Context, env gcevent.Envelope, user string) error {
	day := p.Now().Format("20060102")
	rollupEventID := uuid.NewSHA1(uuid.NameSpaceOID, []byte("rollup:"+env.TenantID.String()+":"+user+":"+day))
	nID := domain.NewID()
	dID := domain.NewID()
	notif := &domain.Notification{
		ID: nID, TenantID: env.TenantID, UserID: user, EventID: rollupEventID, EventType: "notification.rollup",
		SeverityClass: domain.SeverityInfo, Title: "You have more events today", Body: "Daily in-app cap reached; more events are available.",
		CreatedAt: p.Now(),
	}
	del := &domain.Delivery{
		ID: dID, TenantID: env.TenantID, NotificationID: &nID, EventID: rollupEventID, Recipient: user,
		Channel: domain.ChannelInApp, Provider: "in_app", Status: domain.StatusDelivered, CreatedAt: p.Now(), UpdatedAt: p.Now(),
	}
	_, err := p.Store.InsertNotificationTx(ctx, notif, del, map[string]any{"rollup": true}, nil)
	return err
}

func defaultTitle(eventType string) string { return "Notification: " + eventType }
