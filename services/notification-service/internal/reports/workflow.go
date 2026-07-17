package reports

import (
	"time"

	"github.com/google/uuid"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"
)

// TaskQueue is the Temporal task queue notification-service's report worker
// polls, and the queue every per-subscription Schedule's action targets.
const TaskQueue = "notification-reports"

// ActivitySendReportEmail is the registered name of Activities.SendReportEmail
// (used by both the workflow and the worker registration).
const ActivitySendReportEmail = "SendReportEmail"

// ReportRunInput is the ReportWorkflow argument. It carries the subscription's
// own tenant so the activity's store read stays inside the normal per-tenant
// RLS path (withTenant) — no cross-tenant platform-role bypass needed.
type ReportRunInput struct {
	SubscriptionID uuid.UUID
	TenantID       uuid.UUID
}

func activityOptions() workflow.ActivityOptions {
	return workflow.ActivityOptions{
		StartToCloseTimeout: 2 * time.Minute,
		RetryPolicy: &temporal.RetryPolicy{
			InitialInterval:    5 * time.Second,
			BackoffCoefficient: 2.0,
			MaximumInterval:    time.Minute,
			MaximumAttempts:    3,
		},
	}
}

// ReportWorkflow is the durable per-fire unit of work for one scheduled
// dashboard report. A real Temporal Schedule (one per enabled
// ReportSubscription, see Scheduler) invokes this on every cron tick; it runs
// the SendReportEmail activity exactly once, with real retries on transient
// failure (chart-service/SMTP hiccups) — Temporal's durable execution is the
// scheduling primitive, not an in-process timer.
func ReportWorkflow(ctx workflow.Context, in ReportRunInput) error {
	ctx = workflow.WithActivityOptions(ctx, activityOptions())
	return workflow.ExecuteActivity(ctx, ActivitySendReportEmail, in).Get(ctx, nil)
}
