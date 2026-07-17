package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/case-service/internal/authz"
	"github.com/windrose-ai/case-service/internal/search"
	"github.com/windrose-ai/case-service/internal/store"
)

// Server wires the HTTP layer (BRD 08 §5).
type Server struct {
	Store      *store.PG
	Search     *search.Client
	Projector  *search.Projector
	Authz      authz.Authorizer
	Verifier   *Verifier
	RowFetcher RowFetcher
	Snapshots  SnapshotStore
	// Redis backs the per-tenant bulk concurrency gate (CASE-FR-032). Nil
	// disables the gate (unit tests); the runtime always wires it.
	Redis *redisx.Client
}

// Router builds the chi router (base path /api/v1, MASTER-FR-020).
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	r.Use(TraceMiddleware, RecoverMiddleware)

	// Health (MASTER-FR-051): liveness has no deps.
	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", func(w http.ResponseWriter, r *http.Request) {
		if err := s.Store.Ping(r.Context()); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	r.Handle("/metrics", promhttp.Handler())

	// Backend MCP facade the tool-plane federates to (BRD 13 / GAP-2). Not under
	// the JWT-authed /api/v1 group: the peer is the mesh-injected SPIFFE identity
	// and the write is authorized against OPA for the effective human inside.
	r.Post("/internal/v1/mcp/invoke", s.handleToolFacade)

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(AuthMiddleware(s.Verifier), s.IdempotencyMiddleware)

		r.Route("/cases", func(r chi.Router) {
			r.With(s.RequireAction(authz.ActionCaseCreate)).Post("/", s.handleCreateCases)
			r.With(s.RequireAction(authz.ActionCaseRead)).Get("/", s.handleSearchCases)
			r.With(s.RequireAction(authz.ActionCaseBulk)).Post("/bulk", s.handleBulk)
			r.With(s.RequireAction(authz.ActionCaseExport)).Post("/export", s.handleExport)
			r.With(s.RequireAction(authz.ActionCaseRead)).Get("/form", s.handleForm)

			r.With(s.RequireAction(authz.ActionCaseRead)).Get("/{id}", s.handleGetCase)
			r.With(s.RequireAction(authz.ActionCaseUpdate)).Patch("/{id}", s.handlePatchCase)
			r.With(s.RequireAction(authz.ActionCaseRead)).Get("/{id}/timeline", s.handleTimeline)
			r.With(s.RequireAction(authz.ActionCaseComment)).Post("/{id}/comments", s.handleAddComment)

			r.With(s.RequireAction(authz.ActionCaseAssign)).Post("/{id}/assign", s.handleAssign)
			r.With(s.RequireAction(authz.ActionCaseAssign)).Post("/{id}/unassign", s.handleUnassign)
			r.With(s.RequireAction(authz.ActionCaseWork)).Post("/{id}/start", s.handleStart)
			r.With(s.RequireAction(authz.ActionCaseResolve)).Post("/{id}/resolve", s.handleResolve)
			r.With(s.RequireAction(authz.ActionCaseManage)).Post("/{id}/reopen", s.handleReopen)
			r.With(s.RequireAction(authz.ActionCaseManage)).Post("/{id}/close", s.handleClose)
			r.With(s.RequireAction(authz.ActionCaseManage)).Post("/{id}/escalate", s.handleEscalate)

			r.With(s.RequireAction(authz.ActionProposalApply)).Post("/{id}/apply-proposal", s.handleApplyProposal)
		})

		r.Route("/comments", func(r chi.Router) {
			r.With(s.RequireAction(authz.ActionCaseComment)).Patch("/{cid}", s.handleEditComment)
			r.With(s.RequireAction(authz.ActionCaseComment)).Delete("/{cid}", s.handleDeleteComment)
		})

		r.Route("/dispositions", func(r chi.Router) {
			r.With(s.RequireAction(authz.ActionDispositionRead)).Get("/", s.handleListDispositions)
			r.With(s.RequireAction(authz.ActionDispositionCreate)).Post("/", s.handleCreateDisposition)
			r.With(s.RequireAction(authz.ActionDispositionUpdate)).Patch("/{id}", s.handleUpdateDisposition)
		})

		r.Route("/case-fields", func(r chi.Router) {
			r.With(s.RequireAction(authz.ActionFieldRead)).Get("/", s.handleListFields)
			r.With(s.RequireAction(authz.ActionFieldManage)).Post("/", s.handleCreateField)
			r.With(s.RequireAction(authz.ActionFieldManage)).Patch("/{id}", s.handleUpdateField)
			r.With(s.RequireAction(authz.ActionFieldManage)).Delete("/{id}", s.handleDeleteField)
		})

		r.With(s.RequireAction(authz.ActionSLAManage)).Put("/sla-policy", s.handlePutSLAPolicy)
		r.With(s.RequireAction(authz.ActionCaseRead)).Get("/operations/{id}", s.handleGetOperation)
		r.With(s.RequireAction(authz.ActionCaseExport)).Get("/operations/{id}/download", s.handleDownloadExport)
		r.With(s.RequireAction(authz.ActionAdminReindex)).Post("/admin/reindex", s.handleReindex)
	})
	return r
}

// workspaceID resolves the acting workspace from claims (or body override for
// admins). For simplicity the workspace comes from the JWT workspace_id claim.
func workspaceFromClaims(r *http.Request) (uuid.UUID, bool) {
	c := ClaimsFrom(r.Context())
	if c == nil || c.WorkspaceID == "" {
		return uuid.Nil, false
	}
	ws, err := uuid.Parse(c.WorkspaceID)
	if err != nil {
		return uuid.Nil, false
	}
	return ws, true
}
