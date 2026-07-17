package anomaly

import (
	"math"
	"testing"
)

func TestZScore(t *testing.T) {
	// mean 100, stddev 5, observed 130 → z = 6.
	var hist []float64
	for i := 0; i < 28; i++ {
		if i%2 == 0 {
			hist = append(hist, 105)
		} else {
			hist = append(hist, 95)
		}
	}
	r := ZScore(130, hist, 7)
	if !r.Enough {
		t.Fatal("expected enough history")
	}
	if math.Abs(r.Mean-100) > 0.01 {
		t.Fatalf("mean=%v", r.Mean)
	}
	if math.Abs(r.Stddev-5) > 0.01 {
		t.Fatalf("stddev=%v", r.Stddev)
	}
	if math.Abs(r.Z-6) > 0.01 {
		t.Fatalf("z=%v want 6", r.Z)
	}
}

func TestZScoreInsufficientHistory(t *testing.T) {
	r := ZScore(100, []float64{1, 2, 3}, 7)
	if r.Enough {
		t.Fatal("expected not-enough history")
	}
}

func TestZScoreFlatBaseline(t *testing.T) {
	r := ZScore(50, []float64{10, 10, 10, 10, 10, 10, 10}, 7)
	if r.Z != 0 {
		t.Fatalf("flat baseline z should be 0, got %v", r.Z)
	}
}
