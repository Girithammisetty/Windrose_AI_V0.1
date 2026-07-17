package pipeline

import (
	"github.com/prometheus/client_golang/prometheus"

	"context"
	"time"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/preferences"
)

// digestMaxItems is the per-buffer early-flush cap (NOTIF-FR-030).
const digestMaxItems = 200

// deliverEmail sends (or defers/digests/suppresses) an email for one recipient
// (NOTIF-FR-021/031, BR-2/BR-3). Rate-limit and budget breaches convert to the
// digest path (never dropped); suppressed addresses are recorded and skipped.
func (p *Pipeline) deliverEmail(ctx context.Context, env gcevent.Envelope, m mapping, user string, info UserInfo, data map[string]any, locale string, dec preferences.Decision) error {
	addr := info.Email
	// No email on file for this recipient (genuine directory miss). Do NOT invent
	// an address — skip the email channel, count it, and record the skip. In-app
	// delivery for the same recipient is unaffected (handled on its own channel).
	if addr == "" {
		p.recordEmail(ctx, env, user, "", domain.StatusSkipped, "no email address on file")
		p.bump(func(m *Metrics) prometheus.Counter { return m.EmailNoAddress })
		return nil
	}
	critical := m.Class == domain.SeverityCritical
	hash := emailHash(addr)

	// Suppression list (AC-10): hard bounce/complaint auto-mutes the address.
	if suppressed, err := p.Store.IsSuppressed(ctx, env.TenantID, hash); err == nil && suppressed {
		p.recordEmail(ctx, env, user, "", domain.StatusSuppressed, "address suppressed")
		p.bump(func(m *Metrics) prometheus.Counter { return m.EmailSuppressed })
		return nil
	}

	// Quiet hours: defer non-critical email to the window end (AC-13).
	if !critical && !dec.DeferEmailTo.IsZero() {
		return p.queueDeferredEmail(ctx, env, m, user, addr, data, locale, dec.DeferEmailTo)
	}

	// Digest routing: opt-in, tenant budget exhaustion, or rate-limit breach.
	// When already routing to digest (opt-in) we must NOT consume the hourly
	// email token — that would spuriously rate-limit genuine immediate sends.
	toDigest := dec.Digest && m.Digestible
	rateLimited := false
	if !critical && !toDigest {
		if ok, err := p.Limiter.AllowTenantEmail(ctx, env.TenantID.String()); err == nil && !ok {
			toDigest = true // budget exhausted → only critical sends (NOTIF-FR-032)
		}
		if !toDigest {
			if ok, err := p.Limiter.AllowEmail(ctx, env.TenantID.String(), user); err == nil && !ok {
				toDigest, rateLimited = true, true
			}
		}
	}
	if toDigest {
		return p.bufferDigest(ctx, env, m, user, data, rateLimited)
	}

	// Immediate send via the provider abstraction.
	subject, text, html := p.renderFor(ctx, env.TenantID, m.TemplateKey, domain.ChannelEmail, locale, data, defaultTitle(env.EventType))
	res := p.Email.Send(ctx, email.Message{To: addr, Subject: subject, HTML: html, Text: text})
	if res.Class == email.ClassNone {
		p.recordEmail(ctx, env, user, res.ProviderMsgID, domain.StatusSent, "")
		p.bump(func(m *Metrics) prometheus.Counter { return m.EmailSent })
		return nil
	}
	p.bump(func(m *Metrics) prometheus.Counter { return m.EmailFailed })
	p.recordEmail(ctx, env, user, "", domain.StatusFailed, res.Err.Error())
	return nil
}

// bufferDigest appends to the recipient's digest buffer (NOTIF-FR-030).
func (p *Pipeline) bufferDigest(ctx context.Context, env gcevent.Envelope, m mapping, user string, data map[string]any, rateLimited bool) error {
	title, _ := data["Title"].(string)
	if title == "" {
		title = defaultTitle(env.EventType)
	}
	deepLink, _ := data["DeepLink"].(string)
	item := domain.DigestItem{EventID: env.EventID, EventType: env.EventType, Title: title, ResourceURN: env.ResourceURN, DeepLink: deepLink, At: p.Now()}
	windowEnd := p.Now().Add(p.digestWindow())
	count, err := p.Store.AppendDigest(ctx, env.TenantID, user, domain.ChannelEmail, m.Class, item, windowEnd)
	if err != nil {
		return err
	}
	// NOTIF-FR-030: flush on window OR 200 items. Once the buffer hits the cap,
	// mark it due-now so the worker flushes it on its next tick (a >200 burst in
	// one window early-flushes instead of waiting for window end).
	if count >= digestMaxItems {
		if err := p.Store.MarkDigestDue(ctx, env.TenantID, user, domain.ChannelEmail, m.Class, p.Now()); err != nil {
			p.log().Warn("mark digest due failed", "err", err)
		}
	}
	p.bump(func(m *Metrics) prometheus.Counter { return m.DigestBuffered })
	if rateLimited {
		p.bump(func(m *Metrics) prometheus.Counter { return m.RateLimited })
	}
	p.recordEmail(ctx, env, user, "", domain.StatusRateLimitedDigested, "")
	return nil
}

// queueDeferredEmail stores a queued email delivery to be sent at window end by
// the due-email sweeper (quiet hours, AC-13).
func (p *Pipeline) queueDeferredEmail(ctx context.Context, env gcevent.Envelope, m mapping, user, addr string, data map[string]any, locale string, at time.Time) error {
	subject, text, html := p.renderFor(ctx, env.TenantID, m.TemplateKey, domain.ChannelEmail, locale, data, defaultTitle(env.EventType))
	dID := domain.NewID()
	del := &domain.Delivery{
		ID: dID, TenantID: env.TenantID, EventID: env.EventID, Recipient: user, Channel: domain.ChannelEmail,
		Provider: "deferred", Status: domain.StatusQueued, NextRetryAt: &at, CreatedAt: p.Now(), UpdatedAt: p.Now(),
	}
	_, err := p.Store.InsertDelivery(ctx, del, map[string]any{"email": map[string]any{"to": addr, "subject": subject, "text": text, "html": html}})
	return err
}

// recordEmail writes an email delivery row idempotently (NOTIF-FR-050).
func (p *Pipeline) recordEmail(ctx context.Context, env gcevent.Envelope, user, providerMsgID, status, lastErr string) {
	dID := domain.NewID()
	del := &domain.Delivery{
		ID: dID, TenantID: env.TenantID, EventID: env.EventID, Recipient: user, Channel: domain.ChannelEmail,
		Provider: "email", Status: status, ProviderMsgID: providerMsgID, LastError: lastErr,
		Attempts: 1, CreatedAt: p.Now(), UpdatedAt: p.Now(),
	}
	if _, err := p.Store.InsertDelivery(ctx, del, nil); err != nil {
		p.log().Warn("record email delivery failed", "err", err)
	}
}
