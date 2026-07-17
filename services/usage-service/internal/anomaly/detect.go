// Package anomaly implements z-score spend anomaly detection (USG-FR-050/051).
// The math is pure and unit-tested; the daily job wiring lives in cmd/server.
package anomaly

import "math"

// Result is a z-score evaluation over a trailing series.
type Result struct {
	Observed float64
	Mean     float64
	Stddev   float64
	Z        float64
	Enough   bool // >= minHistory samples
}

// ZScore computes the population z-score of `observed` against `history`
// (the trailing daily totals, excluding the observed day). Requires at least
// minHistory samples; with a zero stddev the score is 0 (flat baseline).
func ZScore(observed float64, history []float64, minHistory int) Result {
	if len(history) < minHistory {
		return Result{Observed: observed, Enough: false}
	}
	var sum float64
	for _, v := range history {
		sum += v
	}
	mean := sum / float64(len(history))
	var ss float64
	for _, v := range history {
		d := v - mean
		ss += d * d
	}
	std := math.Sqrt(ss / float64(len(history)))
	z := 0.0
	if std > 0 {
		z = (observed - mean) / std
	}
	return Result{Observed: observed, Mean: mean, Stddev: std, Z: z, Enough: true}
}

// Log1pSeries maps a series through log1p so zero-usage days after active days
// still register (USG-FR-051).
func Log1pSeries(xs []float64) []float64 {
	out := make([]float64, len(xs))
	for i, x := range xs {
		out[i] = math.Log1p(x)
	}
	return out
}
