package enforce

import "testing"

// TPL-FR-033: cost-weight → per-minute cap anchors (weight 1 → 120, weight 10 → 6).
func TestRateForWeight_Anchors(t *testing.T) {
	if got := RateForWeight(1); got != 120 {
		t.Fatalf("weight 1 → 120/min, got %d", got)
	}
	if got := RateForWeight(10); got != 6 {
		t.Fatalf("weight 10 → 6/min, got %d", got)
	}
	// monotonic non-increasing
	prev := 1000
	for w := 1; w <= 10; w++ {
		r := RateForWeight(w)
		if r > prev {
			t.Fatalf("rate must not increase with weight: w=%d r=%d prev=%d", w, r, prev)
		}
		prev = r
	}
}
