// Package metrics exports usage-service's Prometheus instrumentation
// (USG-FR-012/015, NFR §9): ingest lag, unmapped events, budget eval duration
// and enforcement latency.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Metrics bundles the exported collectors.
type Metrics struct {
	IngestLag        prometheus.Histogram
	Unmapped         *prometheus.CounterVec
	Ingested         *prometheus.CounterVec
	BudgetEval       prometheus.Histogram
	EnforcementLat   prometheus.Histogram
	DLQ              *prometheus.CounterVec
}

// New registers the collectors on r (or the default registerer when nil).
func New(r prometheus.Registerer) *Metrics {
	f := promauto.With(r)
	return &Metrics{
		IngestLag: f.NewHistogram(prometheus.HistogramOpts{
			Name: "usage_ingest_lag_seconds", Help: "Event publish→raw ingest lag",
			Buckets: []float64{1, 5, 15, 30, 60, 120, 300},
		}),
		Unmapped: f.NewCounterVec(prometheus.CounterOpts{
			Name: "usage_unmapped_events_total", Help: "Consumed events matching no meter mapping",
		}, []string{"event_type"}),
		Ingested: f.NewCounterVec(prometheus.CounterOpts{
			Name: "usage_ingested_records_total", Help: "Raw meter records ingested",
		}, []string{"meter_key"}),
		BudgetEval: f.NewHistogram(prometheus.HistogramOpts{
			Name: "budget_eval_duration_seconds", Help: "Budget evaluation duration",
			Buckets: prometheus.DefBuckets,
		}),
		EnforcementLat: f.NewHistogram(prometheus.HistogramOpts{
			Name: "budget_enforcement_latency_seconds", Help: "Threshold crossing→event latency",
			Buckets: []float64{1, 5, 15, 30, 60, 120},
		}),
		DLQ: f.NewCounterVec(prometheus.CounterOpts{
			Name: "usage_ingest_dlq_total", Help: "Events routed to the ingest DLQ",
		}, []string{"reason"}),
	}
}

// IncUnmapped implements ingest.Metrics.
func (m *Metrics) IncUnmapped(eventType string) { m.Unmapped.WithLabelValues(eventType).Inc() }

// IncIngested implements ingest.Metrics.
func (m *Metrics) IncIngested(meterKey string, n int) {
	m.Ingested.WithLabelValues(meterKey).Add(float64(n))
}

// ObserveIngestLag implements ingest.Metrics.
func (m *Metrics) ObserveIngestLag(seconds float64) {
	if seconds >= 0 {
		m.IngestLag.Observe(seconds)
	}
}
