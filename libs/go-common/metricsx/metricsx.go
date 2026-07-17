// Package metricsx provides the shared HTTP RED (Rate, Errors, Duration)
// instrumentation every Go service exposes at /metrics (MASTER-FR-050). One
// middleware records request count (by method/route/status) and a duration
// histogram; Handler renders the Prometheus exposition. This replaces the
// per-service `*_up 1` stubs with real, scrapeable RED metrics.
package metricsx

import (
	"net/http"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Registry is a per-service Prometheus registry. Using a dedicated registry
// (not the global default) keeps services isolated and testable.
type Registry struct {
	reg      *prometheus.Registry
	requests *prometheus.CounterVec
	duration *prometheus.HistogramVec
	inflight prometheus.Gauge
}

// New builds a registry pre-populated with process/Go collectors + the RED
// metrics, all labelled with the service name.
func New(service string) *Registry {
	reg := prometheus.NewRegistry()
	reg.MustRegister(
		collectors.NewGoCollector(),
		collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}),
	)
	labels := prometheus.Labels{"service": service}
	requests := prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "http_requests_total", Help: "HTTP requests by method, route and status.",
		ConstLabels: labels,
	}, []string{"method", "route", "status"})
	duration := prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Name: "http_request_duration_seconds", Help: "HTTP request latency in seconds.",
		ConstLabels: labels,
		Buckets:     []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10},
	}, []string{"method", "route", "status"})
	inflight := prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "http_requests_in_flight", Help: "In-flight HTTP requests.", ConstLabels: labels,
	})
	reg.MustRegister(requests, duration, inflight)
	return &Registry{reg: reg, requests: requests, duration: duration, inflight: inflight}
}

// Registerer exposes the underlying registry so a service can add its own
// domain metrics alongside the RED set.
func (r *Registry) Registerer() prometheus.Registerer { return r.reg }

// Handler renders the Prometheus exposition for /metrics.
func (r *Registry) Handler() http.Handler {
	return promhttp.HandlerFor(r.reg, promhttp.HandlerOpts{})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// routeOf resolves a low-cardinality route label. It prefers a chi route
// pattern when the caller supplies one; otherwise it falls back to the method
// only path bucket "other" to avoid unbounded label cardinality from raw paths.
type routeFunc func(*http.Request) string

// Middleware wraps a handler to record RED metrics. `route` maps a request to a
// bounded route label (pass a chi pattern resolver in chi services, or a small
// closure). If route is nil, the label is "all" — still gives rate/errors/p99
// per service without path cardinality risk.
func (r *Registry) Middleware(route routeFunc) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			start := time.Now()
			r.inflight.Inc()
			defer r.inflight.Dec()
			rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
			next.ServeHTTP(rec, req)
			label := "all"
			if route != nil {
				if rr := route(req); rr != "" {
					label = rr
				}
			}
			status := strconv.Itoa(rec.status)
			r.requests.WithLabelValues(req.Method, label, status).Inc()
			r.duration.WithLabelValues(req.Method, label, status).Observe(time.Since(start).Seconds())
		})
	}
}
