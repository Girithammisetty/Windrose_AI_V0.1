package api

import (
	"net/http"
	"os"

	"github.com/datacern-ai/notification-service/internal/domain"
)

// handleChaosError is a synthetic-fault endpoint for OBSERVABILITY ALERTING
// DRILLS ONLY (see deploy/observability/drill.sh). It exists purely to
// produce a real, on-demand 5xx via the normal writeErr/domain.Error path
// (not a panic -- RecoverMiddleware already proves panics become safe 500s;
// this proves the *metrics + alerting* path instead) so the
// DatacernHighErrorRate Prometheus rule (deploy/helm/datacern/templates/
// prometheusrule.yaml) can be checked against a live Prometheus rule-
// evaluation engine instead of only `helm template`/`helm lint`.
//
// Gated OFF by default: unless this process's own environment has
// CHAOS_ENDPOINTS_ENABLED=true set at boot, every request 404s exactly as if
// the route did not exist -- deliberately indistinguishable from "unknown
// path" so an operator scanning routes/logs in a real environment finds
// nothing live. The var is re-read on every request rather than cached at
// startup, but note a running process's own environment cannot be changed
// from outside the process: flipping this switch always requires a full
// restart of notification-service with the var set (or unset).
//
// NEVER enable CHAOS_ENDPOINTS_ENABLED in a real (staging/prod) environment:
// this handler exists to deliberately break the 5xx SLO for drill purposes.
func (s *Server) handleChaosError(w http.ResponseWriter, r *http.Request) {
	if os.Getenv("CHAOS_ENDPOINTS_ENABLED") != "true" {
		writeErr(w, r, domain.ENotFound())
		return
	}
	writeErr(w, r, &domain.Error{
		Code:    domain.CodeInternal,
		HTTP:    http.StatusInternalServerError,
		Message: "synthetic error (observability alerting drill; CHAOS_ENDPOINTS_ENABLED=true)",
	})
}
