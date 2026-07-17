package budget

import (
	"testing"
	"time"

	"github.com/windrose-ai/usage-service/internal/domain"
)

func TestCrossedThresholds(t *testing.T) {
	cases := []struct {
		last          int
		consumed      float64
		limit         float64
		wantCrossed   []int
		wantNewLast   int
	}{
		{0, 800_000, 1_000_000, []int{80}, 80},         // AC-3: 80% → one event
		{80, 800_000, 1_000_000, nil, 80},              // re-eval: none
		{80, 960_000, 1_000_000, []int{95}, 95},        // 95% crossing
		{0, 1_500_000, 1_000_000, []int{80, 95, 100}, 100}, // jump to overage → each once
		{0, 10, 1_000_000, nil, 0},                     // below 80%
	}
	for i, c := range cases {
		crossed, newLast := CrossedThresholds(c.last, c.consumed, c.limit)
		if newLast != c.wantNewLast {
			t.Fatalf("case %d: newLast=%d want %d", i, newLast, c.wantNewLast)
		}
		if len(crossed) != len(c.wantCrossed) {
			t.Fatalf("case %d: crossed=%v want %v", i, crossed, c.wantCrossed)
		}
		for j := range crossed {
			if crossed[j] != c.wantCrossed[j] {
				t.Fatalf("case %d: crossed=%v want %v", i, crossed, c.wantCrossed)
			}
		}
	}
}

func TestWindowBounds(t *testing.T) {
	now := time.Date(2026, 7, 10, 14, 30, 0, 0, time.UTC)

	m := WindowBounds(domain.WindowCalendarMonth, now)
	if !m.WindowStart.Equal(time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("month start = %v", m.WindowStart)
	}
	if !m.RangeEnd.Equal(time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("month end = %v", m.RangeEnd)
	}

	d := WindowBounds(domain.WindowCalendarDay, now)
	if !d.WindowStart.Equal(time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("day start = %v", d.WindowStart)
	}

	r := WindowBounds(domain.WindowRolling7d, now)
	if r.RangeEnd.Sub(r.RangeStart) != 7*24*time.Hour {
		t.Fatalf("rolling window span = %v", r.RangeEnd.Sub(r.RangeStart))
	}
}
