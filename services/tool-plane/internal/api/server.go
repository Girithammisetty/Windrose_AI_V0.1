package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/tool-plane/internal/authz"
	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/embed"
	"github.com/windrose-ai/tool-plane/internal/enforce"
	"github.com/windrose-ai/tool-plane/internal/register"
	"github.com/windrose-ai/tool-plane/internal/store"
)

// RegistryServer wires the tool-registry HTTP layer (catalog + admin).
type RegistryServer struct {
	Store    *store.PG
	Embedder embed.Embedder
	Kill     *enforce.KillRegistry
	Health   *enforce.HealthStore
	Verifier *authjwt.Verifier
	// Authz authorizes every /api/v1 admin route (MASTER-FR-012/016). The real
	// wiring is *authz.AdminOPA; nil fails closed (requireAction denies).
	Authz authz.AdminAuthorizer
	// RegStatus gates /readyz on action-catalog registration (RBC-FR-022);
	// nil skips the gate (unit tests / dev wiring).
	RegStatus *register.Status
}

// Router builds the tool-registry chi router (base path /api/v1). Every
// /api/v1 route is guarded by requireAction with its canonical action
// (MASTER-FR-016); list/read-shaped handlers map to the `.read` action per
// platform convention.
func (s *RegistryServer) Router() http.Handler {
	r := chi.NewRouter()
	r.Use(traceMiddleware, recoverMiddleware)

	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", func(w http.ResponseWriter, req *http.Request) {
		if err := s.Store.Ping(req.Context()); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		if s.RegStatus != nil {
			if ok, reason := s.RegStatus.Ready(); !ok {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusServiceUnavailable)
				_, _ = w.Write([]byte(`{"status":"unavailable","reason":` + jsonString(reason) + `}`))
				return
			}
		}
		w.WriteHeader(http.StatusOK)
	})
	r.Handle("/metrics", promhttp.Handler())

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(s.Verifier.Middleware(func(w http.ResponseWriter, req *http.Request, _ error) {
			writeErr(w, req, domain.EUnauthenticated("invalid or missing token"))
		}))
		r.Use(s.idempotencyMiddleware)

		// Catalog / registration (TPL-FR-001/002/003).
		r.With(s.requireAction(authz.ActionToolCreate)).Post("/tools", s.handleRegisterTool)
		r.With(s.requireAction(authz.ActionToolRead)).Get("/tools", s.handleListTools)
		r.With(s.requireAction(authz.ActionToolUpdate)).Post("/tools/{id}/versions", s.handleAddVersion)
		r.With(s.requireAction(authz.ActionToolRead)).Get("/tools/{id}/schema", s.handleGetSchema)
		r.With(s.requireAction(authz.ActionToolRead)).Post("/tools/{id}/diff", s.handleDiff)
		r.With(s.requireAction(authz.ActionToolUpdate)).Post("/tools/{id}/versions/{v}/publish", s.handlePublish)
		r.With(s.requireAction(authz.ActionToolUpdate)).Post("/tools/{id}/versions/{v}/deprecate", s.handleDeprecate)
		r.With(s.requireAction(authz.ActionToolDelete)).Post("/tools/{id}/versions/{v}/retire", s.handleRetire)
		r.With(s.requireAction(authz.ActionToolRead)).Get("/tools/{id}/health", s.handleHealth)

		// Discovery (TPL-FR-020) — read-shaped.
		r.With(s.requireAction(authz.ActionToolRead)).Post("/discovery/search", s.handleDiscovery)

		// Per-tenant enablement (TPL-FR-004).
		r.With(s.requireAction(authz.ActionEnablementUpdate)).Put("/tenants/self/tools/{id}", s.handleEnablement)

		// Kill switches (TPL-FR-052).
		r.With(s.requireAction(authz.ActionKillRead)).Get("/kill-switches", s.handleListKills)
		r.With(s.requireAction(authz.ActionKillCreate)).Post("/kill-switches", s.handleCreateKill)
		r.With(s.requireAction(authz.ActionKillDelete)).Delete("/kill-switches/{id}", s.handleDeleteKill)

		// BYO onboarding (TPL-FR-040). The queue list (Tier 2b) is guarded by the
		// approve action — it is the approver's work list, not a general read.
		r.With(s.requireAction(authz.ActionBYOApprove)).Get("/byo", s.handleBYOList)
		r.With(s.requireAction(authz.ActionBYOCreate)).Post("/byo", s.handleBYOSubmit)
		r.With(s.requireAction(authz.ActionBYOApprove)).Post("/byo/{id}/approve", s.handleBYOApprove)
		r.With(s.requireAction(authz.ActionBYOApprove)).Post("/byo/{id}/reject", s.handleBYOReject)
	})
	return r
}
