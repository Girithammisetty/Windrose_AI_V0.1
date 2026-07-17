package reports

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/channels/email"
	"github.com/windrose-ai/notification-service/internal/domain"
)

// ReportStore is the persistence surface Activities needs (satisfied by
// *store.PG). Kept as an interface here (rather than importing the store
// package directly) so activity logic can be unit-tested with a fake.
type ReportStore interface {
	GetReportSubscription(ctx context.Context, tenant, id uuid.UUID) (*domain.ReportSubscription, error)
	RecordReportRun(ctx context.Context, tenant, id uuid.UUID, status, sendErr string) error
}

// Activities implements the real Temporal activity that renders and sends one
// report run. All IO happens here (never in the workflow): the store read,
// the token mint, the chart-service HTTP calls, and the email send via the
// EXACT same email.Sender used by NOTIF-FR-030 digests — no parallel path.
type Activities struct {
	Store  ReportStore
	Charts *ChartClient
	Tokens *TokenMinter
	Email  *email.Sender
	Log    *slog.Logger
	// now is overridable for deterministic tests.
	now func() time.Time
}

func (a *Activities) clock() time.Time {
	if a.now != nil {
		return a.now()
	}
	return time.Now().UTC()
}

// SendReportEmail is the ONE activity ReportWorkflow drives. It is idempotent
// enough to retry safely: a re-run just re-fetches live data and re-sends
// (Temporal's retry policy caps this at 3 attempts on transient failure).
func (a *Activities) SendReportEmail(ctx context.Context, in ReportRunInput) error {
	sub, err := a.Store.GetReportSubscription(ctx, in.TenantID, in.SubscriptionID)
	if err != nil {
		return fmt.Errorf("load report subscription: %w", err)
	}
	if !sub.Enabled {
		// The Schedule was paused after this tick was already queued — a no-op,
		// not a failure (avoids Temporal retrying/alerting on a benign race).
		a.log().Info("report subscription disabled, skipping run", "subscription_id", sub.ID)
		return nil
	}

	token, err := a.Tokens.MintOBO(sub.TenantID, sub.WorkspaceID, sub.CreatedBy)
	if err != nil {
		_ = a.Store.RecordReportRun(ctx, sub.TenantID, sub.ID, domain.ReportStatusFailed, err.Error())
		return err
	}

	digest, err := a.Charts.FetchDashboardDigest(ctx, token, sub.DashboardID)
	if err != nil {
		_ = a.Store.RecordReportRun(ctx, sub.TenantID, sub.ID, domain.ReportStatusFailed, err.Error())
		return err
	}

	rendered := Render(digest, a.clock())
	html, text := rendered.HTML, rendered.Text
	if sub.Format == domain.ReportFormatText {
		html = ""
	}

	var failures []string
	for _, to := range sub.Recipients {
		res := a.Email.Send(ctx, email.Message{To: to, Subject: rendered.Subject, HTML: html, Text: text})
		if res.Class != email.ClassNone {
			failures = append(failures, to+": "+res.Err.Error())
		}
	}

	if len(failures) > 0 {
		// Any recipient failure marks the whole run "failed" with the concrete
		// per-recipient errors recorded — no silent partial failure, even if some
		// recipients did get the email.
		msg := strings.Join(failures, "; ")
		_ = a.Store.RecordReportRun(ctx, sub.TenantID, sub.ID, domain.ReportStatusFailed, msg)
		return fmt.Errorf("report send: %d/%d recipients failed: %s", len(failures), len(sub.Recipients), msg)
	}
	return a.Store.RecordReportRun(ctx, sub.TenantID, sub.ID, domain.ReportStatusSent, "")
}

func (a *Activities) log() *slog.Logger {
	if a.Log != nil {
		return a.Log
	}
	return slog.Default()
}
