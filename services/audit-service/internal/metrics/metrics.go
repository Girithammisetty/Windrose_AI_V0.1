// Package metrics exports audit-service's WORM-delivery observability (BRD 58
// SEC-2): a durable-checkpoint write failure and a stale seal are both
// silent by default (the ingest path is best-effort by design, and the
// export scheduler runs unattended) -- these give an operator something to
// alert on instead of finding out from a customer.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Metrics bundles the exported collectors.
type Metrics struct {
	// ChainHeadUpsertFailures counts a failed Postgres chain_heads checkpoint
	// write (chain.Manager.Append swallows this error by design -- the chain
	// sequence itself is ClickHouse-anchored, not Postgres-anchored -- but a
	// failure here is exactly what the SEC-2 incident traced back to: a day
	// invisible to chain_heads is also invisible to the seal scheduler).
	ChainHeadUpsertFailures prometheus.Counter
	// SealAgeSeconds is the age (now - chain_date) of the OLDEST unsealed day
	// found on the most recent reconcile pass, or 0 when everything is sealed.
	// A PrometheusRule alerting on this staying above ~2h is what turns "the
	// consumer looks healthy but a day never got exported" into a page.
	SealAgeSeconds prometheus.Gauge
	// ReconciledDays counts a (tenant, date) the reconciler found via
	// ClickHouse that Postgres's chain_heads had no record of at all.
	ReconciledDays prometheus.Counter
}

// New registers the collectors on r (or the default registerer when nil).
func New(r prometheus.Registerer) *Metrics {
	f := promauto.With(r)
	return &Metrics{
		ChainHeadUpsertFailures: f.NewCounter(prometheus.CounterOpts{
			Name: "audit_chain_head_upsert_failures_total",
			Help: "Failed Postgres chain_heads checkpoint writes",
		}),
		SealAgeSeconds: f.NewGauge(prometheus.GaugeOpts{
			Name: "audit_seal_age_seconds",
			Help: "Age of the oldest unsealed chain day as of the last reconcile pass",
		}),
		ReconciledDays: f.NewCounter(prometheus.CounterOpts{
			Name: "audit_seal_reconciled_days_total",
			Help: "Days found in ClickHouse with no Postgres chain_heads checkpoint at all, recovered by the reconciler",
		}),
	}
}
