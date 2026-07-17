package reports

import (
	"testing"

	"github.com/windrose-ai/notification-service/internal/domain"
)

func TestCronExpr_Daily(t *testing.T) {
	sub := &domain.ReportSubscription{Cadence: domain.CadenceDaily, SendHour: 8}
	got := cronExpr(sub)
	want := "0 8 * * *"
	if got != want {
		t.Fatalf("cronExpr(daily) = %q, want %q", got, want)
	}
}

func TestCronExpr_Weekly(t *testing.T) {
	weekday := 1 // Monday
	sub := &domain.ReportSubscription{Cadence: domain.CadenceWeekly, SendHour: 9, SendWeekday: &weekday}
	got := cronExpr(sub)
	want := "0 9 * * 1"
	if got != want {
		t.Fatalf("cronExpr(weekly) = %q, want %q", got, want)
	}
}

func TestScheduleID_IsDeterministicPerSubscription(t *testing.T) {
	sub := domain.NewID()
	a := ScheduleID(sub)
	b := ScheduleID(sub)
	if a != b {
		t.Fatalf("ScheduleID not deterministic: %q vs %q", a, b)
	}
	if a != "report-"+sub.String() {
		t.Fatalf("unexpected schedule id shape: %q", a)
	}
}
