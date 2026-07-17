// Package metrics holds realtime-hub's Prometheus instrumentation
// (RTH-FR-050): active connections, subscribe denials, fan-in→write latency,
// dropped-event/gap counters, replay hits/resets, heartbeat/slow closes, and
// buffer occupancy.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Metrics bundles the hub's collectors.
type Metrics struct {
	ActiveConns     *prometheus.GaugeVec
	SubscribeDenied prometheus.Counter
	FaninWriteSec   prometheus.Histogram
	DroppedEvents   *prometheus.CounterVec
	ReplayHits      prometheus.Counter
	ReplayResets    prometheus.Counter
	SlowCloses      prometheus.Counter
	ConnLimited     prometheus.Counter
	Revocations     prometheus.Counter
}

// New registers and returns the metrics on reg (use a fresh registry in tests).
func New(reg prometheus.Registerer) *Metrics {
	f := promauto.With(reg)
	return &Metrics{
		ActiveConns: f.NewGaugeVec(prometheus.GaugeOpts{
			Name: "rth_active_connections", Help: "Active connections by tenant and transport.",
		}, []string{"tenant", "transport"}),
		SubscribeDenied: f.NewCounter(prometheus.CounterOpts{
			Name: "rth_subscribe_denied_total", Help: "Per-topic subscribe denials (TOPIC_FORBIDDEN).",
		}),
		FaninWriteSec: f.NewHistogram(prometheus.HistogramOpts{
			Name: "rth_fanin_write_seconds", Help: "Fan-in receive to client-write latency.",
			Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2},
		}),
		DroppedEvents: f.NewCounterVec(prometheus.CounterOpts{
			Name: "rth_dropped_events_total", Help: "Events dropped by the slow-client policy, by topic class.",
		}, []string{"topic_class"}),
		ReplayHits: f.NewCounter(prometheus.CounterOpts{
			Name: "rth_replay_hits_total", Help: "Last-Event-ID resumes served from the replay buffer.",
		}),
		ReplayResets: f.NewCounter(prometheus.CounterOpts{
			Name: "rth_replay_resets_total", Help: "Resumes where the id had aged out of the window (reset).",
		}),
		SlowCloses: f.NewCounter(prometheus.CounterOpts{
			Name: "rth_slow_closes_total", Help: "Chat connections closed 4409 for slowness.",
		}),
		ConnLimited: f.NewCounter(prometheus.CounterOpts{
			Name: "rth_connection_limited_total", Help: "Connections refused for exceeding a cap (429).",
		}),
		Revocations: f.NewCounter(prometheus.CounterOpts{
			Name: "rth_revocations_total", Help: "Topic subscriptions terminated by revocation.",
		}),
	}
}

// Interface adapters used by the fanout package (keeps fanout free of a hard
// prometheus dependency in signatures).

// Dropped records a dropped event by topic-class prefix.
func (m *Metrics) Dropped(topic string) { m.DroppedEvents.WithLabelValues(topicClass(topic)).Inc() }

// SlowClose records a slow-client chat disconnect.
func (m *Metrics) SlowClose(chat bool) {
	if chat {
		m.SlowCloses.Inc()
	}
}

func topicClass(topic string) string {
	for i := 0; i < len(topic); i++ {
		if topic[i] == ':' {
			return topic[:i]
		}
	}
	return "unknown"
}
