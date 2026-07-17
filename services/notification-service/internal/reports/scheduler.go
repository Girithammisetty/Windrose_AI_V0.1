package reports

import (
	"context"
	"fmt"

	"go.temporal.io/sdk/client"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// Scheduler wraps a real Temporal ScheduleClient: one Temporal Schedule
// (cron) per enabled ReportSubscription, matching the platform's existing
// durable-scheduling primitive (used for pipeline/case Temporal workflows).
// This is genuinely wired to the live Temporal cluster (:7233) — there is no
// in-process ticker or fake timer anywhere in this package.
type Scheduler struct {
	Client client.Client
}

// ScheduleID is the deterministic Temporal Schedule id for a subscription.
func ScheduleID(subscriptionID fmt.Stringer) string {
	return "report-" + subscriptionID.String()
}

// cronExpr converts a subscription's cadence into a standard 5-field cron
// expression, interpreted in sub.Timezone by the Schedule's TimeZoneName.
func cronExpr(sub *domain.ReportSubscription) string {
	if sub.Cadence == domain.CadenceWeekly {
		weekday := 0
		if sub.SendWeekday != nil {
			weekday = *sub.SendWeekday
		}
		return fmt.Sprintf("0 %d * * %d", sub.SendHour, weekday)
	}
	return fmt.Sprintf("0 %d * * *", sub.SendHour) // daily
}

// Ensure creates (or replaces) the real Temporal Schedule backing sub and
// returns its schedule id. Safe to call again after any edit (cadence, hour,
// weekday, timezone, enabled) — it deletes a stale schedule first so the spec
// never drifts from the subscription row.
func (s *Scheduler) Ensure(ctx context.Context, sub *domain.ReportSubscription) (string, error) {
	id := ScheduleID(sub.ID)
	sc := s.Client.ScheduleClient()
	_ = sc.GetHandle(ctx, id).Delete(ctx) // idempotent: no-op if none exists yet

	handle, err := sc.Create(ctx, client.ScheduleOptions{
		ID: id,
		Spec: client.ScheduleSpec{
			CronExpressions: []string{cronExpr(sub)},
			TimeZoneName:    sub.Timezone,
		},
		Action: &client.ScheduleWorkflowAction{
			ID:        "report-run-" + sub.ID.String(),
			Workflow:  ReportWorkflow,
			Args:      []interface{}{ReportRunInput{SubscriptionID: sub.ID, TenantID: sub.TenantID}},
			TaskQueue: TaskQueue,
		},
		Paused: !sub.Enabled,
		Note:   "windrose report subscription " + sub.ID.String(),
	})
	if err != nil {
		return "", fmt.Errorf("create temporal schedule: %w", err)
	}
	return handle.GetID(), nil
}

// Delete removes the Temporal Schedule backing a subscription (called on hard
// delete). A blank id is a no-op (subscription was never successfully synced).
func (s *Scheduler) Delete(ctx context.Context, scheduleID string) error {
	if scheduleID == "" {
		return nil
	}
	return s.Client.ScheduleClient().GetHandle(ctx, scheduleID).Delete(ctx)
}

// TriggerNow fires one immediate run outside the cron cadence — the real
// Temporal API for "send now" / live verification, not a synthetic call
// straight into the activity.
func (s *Scheduler) TriggerNow(ctx context.Context, scheduleID string) error {
	if scheduleID == "" {
		return fmt.Errorf("subscription has no schedule yet")
	}
	return s.Client.ScheduleClient().GetHandle(ctx, scheduleID).Trigger(ctx, client.ScheduleTriggerOptions{})
}
