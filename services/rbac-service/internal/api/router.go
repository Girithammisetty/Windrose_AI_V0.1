package api

import (
	"context"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"

	"github.com/windrose-ai/rbac-service/internal/authz"
	"github.com/windrose-ai/rbac-service/internal/projection"
	"github.com/windrose-ai/rbac-service/internal/store"
)

// Server aggregates the dependencies of the HTTP layer.
type Server struct {
	Store    *store.Store
	Checker  *authz.Checker
	Writer   *projection.RedisWriter
	Reader   *projection.RedisReader
	Verifier *Verifier
	Redis    redis.UniversalClient
}

// Router wires all routes (base path /api/v1, MASTER-FR-020).
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	r.Use(TraceMiddleware, RecoverMiddleware)

	// Health & metrics (MASTER-FR-051) — unauthenticated.
	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	r.Get("/readyz", s.handleReadyz)
	r.Handle("/metrics", promhttp.Handler())

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(AuthMiddleware(s.Verifier), s.IdempotencyMiddleware)

		// Workspaces — list/get are visibility-filtered for any
		// authenticated principal (US-4/BR-12); writes need actions.
		r.Get("/workspaces", s.handleListWorkspaces)
		r.Get("/workspaces/{id}", s.handleGetWorkspace)
		r.With(s.RequireAction("rbac.workspace.create")).Post("/workspaces", s.handleCreateWorkspace)
		r.With(s.RequireAction("rbac.workspace.update")).Patch("/workspaces/{id}", s.handleUpdateWorkspace)
		r.With(s.RequireAction("rbac.workspace.admin")).Post("/workspaces/{id}/archive", s.handleArchiveWorkspace)
		r.With(s.RequireAction("rbac.workspace.admin")).Post("/workspaces/{id}/restore", s.handleRestoreWorkspace)
		r.With(s.RequireAction("rbac.workspace.update")).Put("/workspaces/{id}/content-groups/{group_id}", s.handleLinkGroup)
		r.With(s.RequireAction("rbac.workspace.update")).Delete("/workspaces/{id}/content-groups/{group_id}", s.handleUnlinkGroup)

		// Groups & membership.
		r.With(s.RequireAction("rbac.group.list")).Get("/groups", s.handleListGroups)
		r.With(s.RequireAction("rbac.group.read")).Get("/groups/{id}", s.handleGetGroup)
		r.With(s.RequireAction("rbac.group.create")).Post("/groups", s.handleCreateGroup)
		r.With(s.RequireAction("rbac.group.update")).Patch("/groups/{id}", s.handleUpdateGroup)
		r.With(s.RequireAction("rbac.group.delete")).Delete("/groups/{id}", s.handleDeleteGroup)
		r.With(s.RequireAction("rbac.group.read")).Get("/groups/{id}/members", s.handleListMembers)
		r.With(s.RequireAction("rbac.group.read")).Get("/groups/{id}/roles", s.handleListGroupRoles)
		r.With(s.RequireAction("rbac.group.assign")).Put("/groups/{id}/members/{user_id}", s.handleAddMember)
		r.With(s.RequireAction("rbac.group.assign")).Delete("/groups/{id}/members/{user_id}", s.handleRemoveMember)
		r.With(s.RequireAction("rbac.group.assign")).Post("/groups/{id}/members:bulk", s.handleBulkMembers)
		r.With(s.RequireAction("rbac.group.update")).Put("/groups/{id}/roles/{role_id}", s.handleBindRole)
		r.With(s.RequireAction("rbac.group.update")).Delete("/groups/{id}/roles/{role_id}", s.handleUnbindRole)

		// Users — read a user's group memberships (reverse of group membership;
		// same read authz as the sibling group endpoints).
		r.With(s.RequireAction("rbac.group.read")).Get("/users/{id}/groups", s.handleListUserGroups)

		// Roles.
		r.With(s.RequireAction("rbac.role.list")).Get("/roles", s.handleListRoles)
		r.With(s.RequireAction("rbac.role.read")).Get("/roles/{id}", s.handleGetRole)
		r.With(s.RequireAction("rbac.role.create")).Post("/roles", s.handleCreateRole)
		r.With(s.RequireAction("rbac.role.update")).Patch("/roles/{id}", s.handleUpdateRole)
		r.With(s.RequireAction("rbac.role.update")).Put("/roles/{id}/actions", s.handleSetRoleActions)
		r.With(s.RequireAction("rbac.role.delete")).Delete("/roles/{id}", s.handleDeleteRole)

		// Caller's own roles + capabilities for the UI gate — any authenticated
		// principal may read its OWN projection (display data, not a decision).
		r.Get("/me/capabilities", s.handleMeCapabilities)

		// Action catalog — readable by any authenticated principal.
		r.Get("/actions", s.handleListActions)
		r.With(RequireServiceOrSuperAdmin).Post("/actions/register", s.handleRegisterActions)

		// Content grants.
		r.With(s.RequireAction("rbac.grant.list")).Get("/grants", s.handleListGrants)
		r.With(s.RequireAction("rbac.grant.create")).Post("/grants", s.handleCreateGrant)
		r.With(s.RequireAction("rbac.grant.delete")).Delete("/grants/{id}", s.handleDeleteGrant)

		// Decision paths.
		r.With(RequireServiceOrSuperAdmin).Post("/authz/check", s.handleAuthzCheck)
		r.With(s.RequireAction("audit.log.read")).Post("/authz/explain", s.handleAuthzExplain)

		// Admin / platform-operator.
		r.Route("/admin", func(r chi.Router) {
			r.Use(RequireSuperAdmin)
			r.Post("/projection/rebuild", s.handleProjectionRebuild)
			r.Post("/projection/verify", s.handleProjectionVerify)
			r.Post("/tenants/{id}/seed", s.handleSeedTenant)
		})
	})
	return r
}

func (s *Server) handleReadyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2e9)
	defer cancel()
	if err := s.Store.Ping(ctx); err != nil {
		writeError(w, r, http.StatusServiceUnavailable, "NOT_READY", "database unavailable", nil)
		return
	}
	if s.Redis != nil {
		if err := s.Redis.Ping(ctx).Err(); err != nil {
			writeError(w, r, http.StatusServiceUnavailable, "NOT_READY", "redis unavailable", nil)
			return
		}
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ready"))
}
