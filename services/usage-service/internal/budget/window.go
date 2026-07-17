// Package budget holds window math and threshold logic for budget evaluation
// (USG-FR-030/031). The DB-bound evaluator (exactly-once via row lock) lives in
// the store; these helpers are pure and unit-tested without infra.
package budget

import (
	"time"

	"github.com/windrose-ai/usage-service/internal/domain"
)

// Bounds describes a budget window instance: the instance key (window_start,
// used to key budget_states) and the consumption range [RangeStart, RangeEnd).
type Bounds struct {
	WindowStart time.Time
	RangeStart  time.Time
	RangeEnd    time.Time
}

// WindowBounds computes the current window instance for a budget window kind at
// time now (UTC). rolling_7d rolls daily so it has a stable per-day instance
// key while consumption covers the trailing 7×24h.
func WindowBounds(window string, now time.Time) Bounds {
	now = now.UTC()
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	switch window {
	case domain.WindowCalendarDay:
		return Bounds{WindowStart: day, RangeStart: day, RangeEnd: day.Add(24 * time.Hour)}
	case domain.WindowRolling7d:
		start := day.AddDate(0, 0, -6)
		return Bounds{WindowStart: start, RangeStart: now.Add(-7 * 24 * time.Hour), RangeEnd: now}
	default: // calendar_month
		mStart := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
		mEnd := mStart.AddDate(0, 1, 0)
		return Bounds{WindowStart: mStart, RangeStart: mStart, RangeEnd: mEnd}
	}
}

// CrossedThresholds returns the thresholds newly crossed given the prior
// last_threshold and current consumption/limit. It returns the ordered list of
// newly-crossed thresholds and the new last_threshold. Consumption may exceed
// limit (overage reported, never clipped — BR-2).
func CrossedThresholds(last int, consumed, limit float64) (crossed []int, newLast int) {
	newLast = last
	if limit <= 0 {
		return nil, last
	}
	pct := consumed / limit * 100
	for _, t := range domain.Thresholds {
		if t > last && pct >= float64(t) {
			crossed = append(crossed, t)
			newLast = t
		}
	}
	return crossed, newLast
}
