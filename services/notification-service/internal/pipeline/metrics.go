package pipeline

import "github.com/prometheus/client_golang/prometheus"

// Metrics are the pipeline's Prometheus counters (MASTER-FR-050, US-7).
type Metrics struct {
	Unmapped        prometheus.Counter
	RateLimited     prometheus.Counter
	InAppCreated    prometheus.Counter
	EmailSent       prometheus.Counter
	EmailFailed     prometheus.Counter
	EmailSuppressed prometheus.Counter
	EmailNoAddress  prometheus.Counter
	DigestBuffered  prometheus.Counter
	WebhookSent     prometheus.Counter
	WebhookFailed   prometheus.Counter
	CircuitOpened   prometheus.Counter
}

// NewMetrics registers the pipeline counters with a registry.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	f := func(name, help string) prometheus.Counter {
		c := prometheus.NewCounter(prometheus.CounterOpts{Name: name, Help: help})
		reg.MustRegister(c)
		return c
	}
	return &Metrics{
		Unmapped:        f("notif_events_unmapped_total", "Consumed events with no mapping."),
		RateLimited:     f("notif_rate_limited_total", "Notifications converted to digest by rate limit."),
		InAppCreated:    f("notif_inapp_created_total", "In-app notifications persisted."),
		EmailSent:       f("notif_email_sent_total", "Emails accepted by a provider."),
		EmailFailed:     f("notif_email_failed_total", "Email send failures."),
		EmailSuppressed: f("notif_email_suppressed_total", "Emails skipped due to suppression."),
		EmailNoAddress:  f("notif_email_no_address_total", "Emails skipped: recipient has no email address on file."),
		DigestBuffered:  f("notif_digest_buffered_total", "Notifications routed to a digest buffer."),
		WebhookSent:     f("notif_webhook_sent_total", "Webhook deliveries accepted (2xx)."),
		WebhookFailed:   f("notif_webhook_failed_total", "Webhook delivery failures."),
		CircuitOpened:   f("notif_webhook_circuit_opened_total", "Webhook circuits opened."),
	}
}

func (m *Metrics) inc(c prometheus.Counter) {
	if m != nil && c != nil {
		c.Inc()
	}
}

// bump increments a selected counter, nil-safe when Metrics is unset (tests).
func (p *Pipeline) bump(sel func(*Metrics) prometheus.Counter) {
	if p.Metrics == nil {
		return
	}
	if c := sel(p.Metrics); c != nil {
		c.Inc()
	}
}
