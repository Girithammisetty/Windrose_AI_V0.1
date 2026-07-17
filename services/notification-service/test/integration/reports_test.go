package integration

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// TestAC15_ReportSubscriptionCRUDAndRLS proves the report_subscriptions
// migration (000004) + its RLS policy behave like every other tenant-scoped
// table in this service: a subscription created for tenant A round-trips
// through the real (non-owner, RLS-forced) Postgres role, and tenant B's
// query for the same id sees nothing (cross-tenant isolation, mirrors
// TestAC14 for notifications).
func TestAC15_ReportSubscriptionCRUDAndRLS(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	tenantA := uuid.New()
	tenantB := uuid.New()
	dashboardID := uuid.New()
	workspaceID := uuid.New()

	weekday := 1
	sub := &domain.ReportSubscription{
		ID: domain.NewID(), TenantID: tenantA, WorkspaceID: workspaceID, DashboardID: dashboardID,
		Name: "Weekly claims summary", Recipients: []string{"manager@demo.windrose"},
		Cadence: domain.CadenceWeekly, SendHour: 8, SendWeekday: &weekday, Timezone: "UTC",
		Format: domain.ReportFormatHTML, Enabled: true, CreatedBy: "manager@demo.windrose",
	}
	sub.CreatedAt = time.Now().UTC()
	sub.UpdatedAt = sub.CreatedAt

	if err := h.pg.CreateReportSubscription(ctx, sub); err != nil {
		t.Fatalf("create: %v", err)
	}

	got, err := h.pg.GetReportSubscription(ctx, tenantA, sub.ID)
	if err != nil {
		t.Fatalf("get (owning tenant): %v", err)
	}
	if got.Name != sub.Name || got.Cadence != domain.CadenceWeekly || len(got.Recipients) != 1 {
		t.Fatalf("round-trip mismatch: %+v", got)
	}
	if got.SendWeekday == nil || *got.SendWeekday != weekday {
		t.Fatalf("send_weekday not persisted: %+v", got.SendWeekday)
	}

	// Cross-tenant: tenant B gets NOT_FOUND, never tenant A's row (RLS FORCE).
	if _, err := h.pg.GetReportSubscription(ctx, tenantB, sub.ID); err == nil {
		t.Fatal("cross-tenant leak: tenant B read tenant A's report subscription")
	}

	list, err := h.pg.ListReportSubscriptions(ctx, tenantA, nil, 10, nil)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(list) != 1 {
		t.Fatalf("expected 1 subscription for tenant A, got %d", len(list))
	}
	listB, err := h.pg.ListReportSubscriptions(ctx, tenantB, nil, 10, nil)
	if err != nil {
		t.Fatalf("list (tenant B): %v", err)
	}
	if len(listB) != 0 {
		t.Fatalf("cross-tenant leak: tenant B listed %d rows", len(listB))
	}

	// Update: flip cadence + pause.
	got.Cadence = domain.CadenceDaily
	got.SendWeekday = nil
	got.Enabled = false
	if err := h.pg.UpdateReportSubscription(ctx, got); err != nil {
		t.Fatalf("update: %v", err)
	}
	afterUpdate, err := h.pg.GetReportSubscription(ctx, tenantA, sub.ID)
	if err != nil {
		t.Fatalf("get after update: %v", err)
	}
	if afterUpdate.Cadence != domain.CadenceDaily || afterUpdate.Enabled {
		t.Fatalf("update did not persist: %+v", afterUpdate)
	}

	// RecordReportRun stamps last_status/last_sent_at (the activity's own write path).
	if err := h.pg.RecordReportRun(ctx, tenantA, sub.ID, domain.ReportStatusSent, ""); err != nil {
		t.Fatalf("record run: %v", err)
	}
	afterRun, err := h.pg.GetReportSubscription(ctx, tenantA, sub.ID)
	if err != nil {
		t.Fatalf("get after run: %v", err)
	}
	if afterRun.LastStatus != domain.ReportStatusSent || afterRun.LastSentAt == nil {
		t.Fatalf("run not recorded: %+v", afterRun)
	}

	// Delete is a soft-delete; the row disappears from Get/List for its own tenant.
	if err := h.pg.DeleteReportSubscription(ctx, tenantA, sub.ID); err != nil {
		t.Fatalf("delete: %v", err)
	}
	if _, err := h.pg.GetReportSubscription(ctx, tenantA, sub.ID); err == nil {
		t.Fatal("expected NOT_FOUND after delete")
	}
}
