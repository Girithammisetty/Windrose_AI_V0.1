// Package worker holds notification-service's durable background sweepers: the
// digest flush worker (NOTIF-FR-030) and the webhook retry / deferred-email /
// circuit-probe worker (NOTIF-FR-023). Timer state lives in Postgres
// (digest_buffers.window_end, deliveries.next_retry_at) so a restart resumes
// pending work — this is the Temporal-equivalent when Temporal is not wired
// (same pattern as case-service's SLA sweep), and is fully real, not a stub.
package worker

import (
	"context"
	"log/slog"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/channels/webhook"
	"github.com/windrose-ai/notification-service/internal/domain"
	"github.com/windrose-ai/notification-service/internal/events"
	"github.com/windrose-ai/notification-service/internal/pipeline"
	"github.com/windrose-ai/notification-service/internal/store"
	"github.com/windrose-ai/notification-service/internal/templates"
)

// Worker runs the durable sweepers.
type Worker struct {
	Store    *store.PG
	Pipeline *pipeline.Pipeline
	Email    *email.Sender
	Interval time.Duration
	Batch    int
	Log      *slog.Logger
	now      func() time.Time
}

// New builds a Worker.
func New(st *store.PG, pl *pipeline.Pipeline, em *email.Sender) *Worker {
	return &Worker{Store: st, Pipeline: pl, Email: em, Interval: time.Second, Batch: 128, Log: slog.Default()}
}

// SetClock overrides the worker clock (tests).
func (w *Worker) SetClock(f func() time.Time) { w.now = f }

func (w *Worker) clock() time.Time {
	if w.now != nil {
		return w.now()
	}
	return time.Now().UTC()
}

// Run sweeps until ctx is cancelled.
func (w *Worker) Run(ctx context.Context) {
	iv := w.Interval
	if iv <= 0 {
		iv = time.Second
	}
	t := time.NewTicker(iv)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			w.Sweep(ctx)
		}
	}
}

// Sweep runs one pass of all sweepers (exposed for deterministic tests).
func (w *Worker) Sweep(ctx context.Context) {
	if err := w.flushDigests(ctx); err != nil {
		w.Log.Warn("digest flush failed", "err", err)
	}
	if err := w.retryWebhooks(ctx); err != nil {
		w.Log.Warn("webhook retry failed", "err", err)
	}
	if err := w.sendDeferredEmail(ctx); err != nil {
		w.Log.Warn("deferred email failed", "err", err)
	}
}

// flushDigests renders + sends one digest email per due buffer (NOTIF-FR-030).
func (w *Worker) flushDigests(ctx context.Context) error {
	now := w.clock()
	buffers, err := w.Store.DueDigestBuffers(ctx, now, w.Batch)
	if err != nil {
		return err
	}
	for _, b := range buffers {
		taken, err := w.Store.TakeDigestBuffer(ctx, b.TenantID, b.ID)
		if err != nil || taken == nil {
			continue // another sweeper claimed it (BR-10)
		}
		if len(taken.Items) == 0 {
			continue
		}
		w.sendDigest(ctx, taken)
	}
	return nil
}

func (w *Worker) sendDigest(ctx context.Context, b *domain.DigestBuffer) {
	data := map[string]any{"Count": len(b.Items), "Items": b.Items}
	key := "digest." + b.EventClass
	subject, text, html := "Your Windrose digest", "", ""
	if t, err := w.Store.ResolveTemplate(ctx, b.TenantID, key, domain.ChannelEmail, "en"); err == nil && t != nil {
		if r, rerr := templates.Render(t.SubjectTpl, t.BodyHTMLTpl, t.BodyTextTpl, data); rerr == nil {
			subject, text, html = r.Subject, r.Text, r.HTML
		}
	}
	if b.Channel == domain.ChannelEmail {
		info, _ := w.Pipeline.Dir.Lookup(ctx, b.TenantID.String(), b.UserID)
		res := w.Email.Send(ctx, email.Message{To: info.Email, Subject: subject, HTML: html, Text: text})
		status := domain.StatusSent
		msgID, lastErr := res.ProviderMsgID, ""
		if res.Class != email.ClassNone {
			status, lastErr = domain.StatusFailed, res.Err.Error()
		}
		digestEventID := uuid.NewSHA1(uuid.NameSpaceOID, []byte("digest:"+b.ID.String()))
		del := &domain.Delivery{
			ID: domain.NewID(), TenantID: b.TenantID, EventID: digestEventID, Recipient: b.UserID,
			Channel: domain.ChannelEmail, Provider: "digest", Status: status, ProviderMsgID: msgID,
			LastError: lastErr, Attempts: 1, CreatedAt: w.clock(), UpdatedAt: w.clock(),
		}
		_, _ = w.Store.InsertDelivery(ctx, del, map[string]any{"digest": true, "count": len(b.Items)})
	}
}

// retryWebhooks drives due webhook deliveries through the circuit state machine.
func (w *Worker) retryWebhooks(ctx context.Context) error {
	now := w.clock()
	due, err := w.Store.DueWebhookDeliveries(ctx, now, w.Batch)
	if err != nil {
		return err
	}
	for _, dd := range due {
		ep, err := w.Store.GetWebhook(ctx, dd.Delivery.TenantID, *dd.Delivery.WebhookEndpointID)
		if err != nil {
			continue
		}
		// Auto-disable an endpoint whose circuit has been open > 72h (NOTIF-FR-023).
		if ep.CircuitState == domain.CircuitOpen && ep.CircuitOpenedAt != nil && now.Sub(*ep.CircuitOpenedAt) >= webhook.DisableAfter {
			ep.Active = false
			ep.CircuitState = domain.CircuitDisabled
			if err := w.Store.UpdateWebhook(ctx, ep); err == nil {
				w.Pipeline.EmitOps(ctx, ep.TenantID, ep.ID, events.EvEndpointDisabled, map[string]any{"endpoint_id": ep.ID.String()})
			}
			continue
		}
		// Circuit open but probe due → half-open probe (NOTIF-FR-023).
		if ep.CircuitState == domain.CircuitOpen && ep.CircuitOpenedAt != nil && now.Sub(*ep.CircuitOpenedAt) >= webhook.ProbeInterval {
			ep.CircuitState = domain.CircuitHalfOpen
		}
		w.Pipeline.AttemptWebhook(ctx, ep, dd.Envelope, dd.Delivery.ID, dd.Delivery.Attempts+1)
	}
	return nil
}

// sendDeferredEmail sends quiet-hours-deferred emails whose window has ended.
func (w *Worker) sendDeferredEmail(ctx context.Context) error {
	now := w.clock()
	due, err := w.Store.DueEmailDeliveries(ctx, now, w.Batch)
	if err != nil {
		return err
	}
	for _, de := range due {
		res := w.Email.Send(ctx, email.Message{To: de.To, Subject: de.Subject, HTML: de.HTML, Text: de.Text})
		status, msgID, lastErr := domain.StatusSent, res.ProviderMsgID, ""
		if res.Class != email.ClassNone {
			status, lastErr = domain.StatusFailed, res.Err.Error()
		}
		_ = w.Store.UpdateDeliveryStatus(ctx, de.Delivery.TenantID, de.Delivery.ID, status, msgID, lastErr, de.Delivery.Attempts+1, nil, nil)
	}
	return nil
}
