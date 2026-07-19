package api

import (
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/go-common/metricsx"
	"github.com/windrose-ai/query-service/internal/authz"
	"github.com/windrose-ai/query-service/internal/exec"
	"github.com/windrose-ai/query-service/internal/results"
	"github.com/windrose-ai/query-service/internal/store"
)

// Server wires the HTTP layer.
type Server struct {
	Store    store.Store
	Broker   *exec.Broker
	Results  *results.Store
	Authz    authz.Authorizer
	Verifier *Verifier
	// ExportSecret signs export download URLs (QRY-FR-062).
	ExportSecret []byte
	// Datasets resolves case row-fetch dataset URNs to logical names via
	// dataset-service's public API under the caller's token (GET /api/v1/rows).
	Datasets DatasetNamer
	// RegGate gates /readyz on rbac action-catalog registration (RBC-FR-022,
	// M1 hardening): while registration is pending or failed the service
	// reports degraded instead of silently serving 403s. Nil = ungated (unit
	// tests / dev without RBAC_URL).
	RegGate *RegGate
}

// Router builds the chi router (base path /api/v1, MASTER-FR-020).
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	// RED metrics (MASTER-FR-050): real /metrics + per-request rate/errors/
	// duration via the shared middleware, replacing the bare runtime-only stub.
	metrics := metricsx.New("query-service")
	r.Use(TraceMiddleware, RecoverMiddleware, metrics.Middleware(chiRoutePattern))

	// Health endpoints (MASTER-FR-051): liveness has no deps.
	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", func(w http.ResponseWriter, r *http.Request) {
		if err := s.Store.Ping(r.Context()); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		// Fail loudly when the rbac action-catalog registration has not
		// succeeded: an unregistered manifest means OPA denies EVERY guarded
		// route (action_known=false), which must surface as degraded — never
		// as a silently "ready" service that 403s everything.
		if s.RegGate != nil {
			if reason := s.RegGate.Reason(); reason != "" {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusServiceUnavailable)
				_, _ = w.Write([]byte(`{"status":"degraded","checks":{"action_registration":` + strconv.Quote(reason) + `}}`))
				return
			}
		}
		w.WriteHeader(http.StatusOK)
	})
	r.Handle("/metrics", metrics.Handler())

	// Signed download links are pre-authenticated by signature (QRY-FR-062).
	r.Get("/api/v1/downloads/{token}", s.handleDownload)

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(AuthMiddleware(s.Verifier), s.IdempotencyMiddleware)

		r.Route("/queries", func(r chi.Router) {
			r.With(s.RequireAction(authz.ActionQueryCreate)).Post("/", s.handleCreateQuery)
			r.With(s.RequireAction(authz.ActionQueryRead)).Get("/", s.handleListQueries)
			r.With(s.RequireAction(authz.ActionQueryRead)).Get("/{id}", s.handleGetQuery)
			r.With(s.RequireAction(authz.ActionQueryUpdate)).Patch("/{id}", s.handlePatchQuery)
			r.With(s.RequireAction(authz.ActionQueryDelete)).Delete("/{id}", s.handleDeleteQuery)
			r.With(s.RequireAction(authz.ActionQueryRead)).Get("/{id}/versions", s.handleListVersions)
			r.With(s.RequireAction(authz.ActionExecRun)).Post("/{id}/run", s.handleRunSavedQuery)
		})

		r.Route("/sql", func(r chi.Router) {
			r.With(s.RequireAction(authz.ActionExecRun)).Post("/run", s.handleRunSQL)
			r.With(s.RequireAction(authz.ActionExecRun)).Post("/dry-run", s.handleDryRun)
		})

		r.Route("/executions", func(r chi.Router) {
			r.With(s.RequireAction(authz.ActionExecRead)).Get("/", s.handleListExecutions)
			r.With(s.RequireAction(authz.ActionExecRead)).Get("/{id}", s.handleGetExecution)
			r.With(s.RequireAction(authz.ActionExecRead)).Get("/{id}/results", s.handleResults)
			// Cancel is an execution-control op on the execute capability
			// ("cancel" is not a canonical rbac verb; MASTER-FR-016).
			r.With(s.RequireAction(authz.ActionExecRun)).Post("/{id}/cancel", s.handleCancel)
			r.With(s.RequireAction(authz.ActionExecExport)).Post("/{id}/export", s.handleExport)
		})

		// Live row fetch for case-service (CASE-FR-001 ?with_row=true): runs a
		// parameterized SELECT through the same plan/broker path as /sql/run.
		r.With(s.RequireAction(authz.ActionExecRun)).Get("/rows", s.handleGetRow)

		r.With(s.RequireAction(authz.ActionStatsRead)).Get("/stats/queries", s.handleStats)
		r.With(s.RequireAction(authz.ActionLimitsRead)).Get("/limits", s.handleGetLimits)
		r.With(s.RequireAction(authz.ActionLimitsUpdate)).Put("/limits", s.handlePutLimits)
	})
	return r
}

// chiRoutePattern resolves the matched chi route template for a bounded metrics
// route label (evaluated after routing), falling back to "other".
func chiRoutePattern(r *http.Request) string {
	if rc := chi.RouteContext(r.Context()); rc != nil {
		if p := rc.RoutePattern(); p != "" {
			return p
		}
	}
	return "other"
}
