package exec

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Service-specific Prometheus metrics (BRD 05 §9), exported on /metrics.
var (
	metricExecutionsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "query_executions_total",
		Help: "Executions by engine, terminal status and caller class.",
	}, []string{"engine", "status", "caller_class"})

	metricScanBytesTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "query_scan_bytes_total",
		Help: "Actual bytes scanned per engine.",
	}, []string{"engine"})

	metricCeilingRejections = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "query_ceiling_rejections_total",
		Help: "Plan-time and runtime ceiling rejections by ceiling kind.",
	}, []string{"ceiling"})

	metricCacheHits = promauto.NewCounter(prometheus.CounterOpts{
		Name: "query_cache_hits_total",
		Help: "Result-cache hits (QRY-FR-046).",
	})

	metricQueueDepth = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "query_queue_depth",
		Help: "Queued executions per tenant.",
	}, []string{"tenant"})
)

func observeTerminal(engineName, status string, caller string, scanBytes int64) {
	metricExecutionsTotal.WithLabelValues(engineName, status, caller).Inc()
	if scanBytes > 0 {
		metricScanBytesTotal.WithLabelValues(engineName).Add(float64(scanBytes))
	}
}
