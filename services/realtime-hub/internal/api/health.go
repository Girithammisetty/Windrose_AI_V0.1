package api

import (
	"net/http"
	"sync/atomic"

	"github.com/windrose-ai/go-common/httpx"
)

// RegGate tracks action-catalog registration state for /readyz gating
// (M1 hardening: registration is NOT silent best-effort). While registration
// is pending or has failed, /readyz reports 503 with the reason so the pod is
// not admitted to serve subscribes that would all fail action_known=false.
// A nil *RegGate on Server means registration was intentionally skipped
// (dev mode: RBAC_URL / signing key unset) and readiness is ungated.
type RegGate struct {
	reason atomic.Value // string; "" once registration succeeded
}

// NewRegGate returns a gate in the pending state.
func NewRegGate() *RegGate {
	g := &RegGate{}
	g.reason.Store("action catalog registration pending")
	return g
}

// Fail records a (retryable) registration failure reason.
func (g *RegGate) Fail(reason string) {
	g.reason.Store("action catalog registration failed: " + reason)
}

// Succeed marks registration complete; /readyz stops reporting degraded.
func (g *RegGate) Succeed() { g.reason.Store("") }

// Reason returns the current degradation reason ("" = registered).
func (g *RegGate) Reason() string {
	v, _ := g.reason.Load().(string)
	return v
}

// handleHealthz is liveness (no deps, MASTER-FR-051).
func (s *Server) handleHealthz(w http.ResponseWriter, r *http.Request) {
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"status": "ok"})
}

// handleReadyz checks Redis (replay/bus/tickets/counters) and action-catalog
// registration. Redis being down degrades replay but existing connections keep
// live-tailing from Kafka, so the hub reports degraded-replay rather than a
// hard fail (AC-13 / BR-9). Registration pending/failed IS a hard fail (503):
// without a registered catalog every OPA-decided subscribe would be denied as
// unknown_action, so the pod must not be admitted (RBC-FR-022).
func (s *Server) handleReadyz(w http.ResponseWriter, r *http.Request) {
	status := "ready"
	code := http.StatusOK
	checks := map[string]string{}
	if err := s.Redis.Ping(r.Context()); err != nil {
		checks["redis"] = "down"
		status = "degraded-replay"
		// Still 200: the service can serve live Kafka fan-out without Redis.
	} else {
		checks["redis"] = "ok"
	}
	if s.RegGate != nil {
		if reason := s.RegGate.Reason(); reason != "" {
			checks["action_registration"] = reason
			status = "degraded"
			code = http.StatusServiceUnavailable
		} else {
			checks["action_registration"] = "ok"
		}
	}
	httpx.WriteJSON(w, code, map[string]any{"status": status, "checks": checks})
}
