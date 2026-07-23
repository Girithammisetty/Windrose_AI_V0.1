// Package api is the HTTP layer: chi router, master-BRD middleware (trace
// ids, auth, idempotency, error envelope, pagination) and thin handlers over
// the domain services.
package api

import (
	"context"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/datacern-ai/go-common/metricsx"
	"github.com/datacern-ai/identity-service/internal/authz"
	"github.com/datacern-ai/identity-service/internal/domain"
	"github.com/datacern-ai/identity-service/internal/keys"
)

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

// Action names for this service (MASTER-FR-016).
const (
	ActTenantAdmin    = "identity.tenant.admin" // platform staff
	ActUserAdmin      = "identity.user.admin"   // tenant admin
	ActSvcAcctAdmin   = "identity.service_account.admin"
	ActCredentialRead = "identity.credential.read"
)

type Server struct {
	Store    domain.Store
	Tenants  *domain.TenantService
	Users    *domain.UserService
	SAs      *domain.ServiceAccountService
	Tokens   *domain.TokenService
	KM       *keys.KeyManager
	Verifier domain.TokenVerifier
	Authz    authz.Authorizer
	// TrustedSpiffeIDs may call POST /token/agent (IDN-FR-042: agent-runtime).
	TrustedSpiffeIDs map[string]bool
	// TrustSpiffeHeader (F-2) must be explicitly true for the X-Spiffe-Id
	// header to be honored. Default false: the header is ignored and
	// agent-autonomous token minting is refused.
	TrustSpiffeHeader bool
	Clock             func() time.Time
	Log               *slog.Logger

	// Logo persists tenant branding logo bytes (BRD 59 WS3, MinIO in prod).
	// Nil is a valid, honest "not configured" state: the branding color tokens
	// still work, but logo upload/download 501s rather than silently no-op'ing.
	Logo LogoStore

	// ready is checked by /readyz.
	Ready func() error
}

// LogoStore persists tenant branding logo bytes (BRD 59 WS3). The production
// adapter is MinIO/S3 (internal/blob); the pointer/metadata row (object key +
// content type) lives in Postgres (tenant_branding).
type LogoStore interface {
	Put(ctx context.Context, key string, data []byte, contentType string) error
	Get(ctx context.Context, key string) ([]byte, error)
	Delete(ctx context.Context, key string) error
}

func (s *Server) Router() http.Handler {
	r := chi.NewRouter()
	// RED metrics (MASTER-FR-050): real /metrics + per-request rate/errors/duration,
	// replacing the former identity_up text stub.
	metrics := metricsx.New("identity-service")
	r.Use(traceMiddleware, recoverMiddleware, s.spiffeMiddleware, metrics.Middleware(chiRoutePattern))

	// Ops + public endpoints.
	r.Get("/healthz", s.handleHealthz)
	r.Get("/readyz", s.handleReadyz)
	r.Handle("/metrics", metrics.Handler())
	r.Get("/.well-known/jwks.json", s.handleJWKS)

	r.Route("/api/v1", func(r chi.Router) {
		// Pre-auth endpoints (own credential checks inside).
		r.Post("/invitations/{token}/accept", s.handleAcceptInvitation)
		r.Post("/token/obo", s.handleOBO) // subject_token carries the auth
		r.Post("/token/apikey", s.handleAPIKeyExchange)
		r.Post("/token/embed", s.handleEmbedToken)       // tenant embed secret in body
		r.Post("/token/embed/oidc", s.handleEmbedOIDC)   // embed-federated SSO: user OIDC id_token (task #84)
		r.Post("/token/oidc", s.handleOIDCLogin)         // external OIDC id_token in body (BYO-P4)
		r.Post("/token/agent", s.handleAgentToken) // SPIFFE-gated
		// BRD 60 WS2: a customer's own agent exchanges a tenant-admin-minted
		// external-agent key (wr_xa_...) for a short-lived agent_autonomous
		// token. The key IS the credential (like /token/embed); no bearer.
		r.Post("/token/agent/external", s.handleExternalAgentTokenExchange)

		// Authenticated API.
		r.Group(func(r chi.Router) {
			r.Use(s.authMiddleware, s.idempotencyMiddleware)

			// Tenants: platform-scoped admin (IDN-FR-025); GET /tenants/{id}
			// additionally serves a tenant's own admins. F-3: require an admin
			// scope so a zero-scope user/service/agent token cannot read
			// registry internals (owner_email, quotas, namespace, cell).
			// The cross-tenant case is still 404 + audit inside the handler.
			// Member-safe subset of the CALLER's own tenant (name/display/status
			// only) — no admin scope: any member may see their org's name.
			// Registered before /tenants/{id} so "self" never parses as an id.
			r.Get("/tenants/self", s.handleGetTenantSelf)
			// GET /users/profiles — member-visible {id,email,full_name} batch
			// lookup for display-only hydration (case assignee, comment author,
			// activity actor). No admin scope: mirrors /tenants/self. Registered
			// as a static route ahead of the ActUserAdmin-gated group below (and
			// ahead of /users/{id}) so "profiles" never parses as an {id}.
			r.Get("/users/profiles", s.handleUserProfiles)
			// GET /users/assignable — member-visible directory of ACTIVE users
			// (id/email/full_name only) for assignment/mention pickers. Same
			// member-safe tier as /users/profiles and /tenants/self: no admin
			// scope, so a case worker holding case.case.assign can pick an
			// assignee without the tenant user-directory admin scope. Registered
			// as a static route ahead of the ActUserAdmin-gated /users/{id} below
			// so "assignable" never parses as an {id}.
			r.Get("/users/assignable", s.handleAssignableUsers)
			// Per-tenant display-label overlays (BRD 23 inc3): READ is member-safe
			// (every member's UI overlays them at bootstrap); WRITES are tenant-admin
			// scoped (labels are tenant-wide presentation). Static routes registered
			// ahead of /tenants/{id} so "self" never parses as an id.
			r.Get("/tenants/self/labels", s.handleGetTenantLabels)
			r.With(s.requireScope(ActUserAdmin)).Put("/tenants/self/labels", s.handleSetTenantLabels)
			r.With(s.requireScope(ActUserAdmin)).Delete("/tenants/self/labels/{key}", s.handleDeleteTenantLabel)
			r.With(s.requireScope(ActUserAdmin)).Get("/tenants/{id}", s.handleGetTenant)
			r.With(s.requireScope(ActUserAdmin)).Get("/tenants/{id}/embed-config", s.handleGetEmbedConfig)
			r.With(s.requireScope(ActUserAdmin)).Put("/tenants/{id}/embed-config", s.handleSetEmbedConfig)
			// BYO-P4: a tenant admin registers their OWN OIDC IdP (self-scoped;
			// keyed on the caller's tenant claim, no {id} to spoof).
			r.With(s.requireScope(ActUserAdmin)).Get("/tenants/self/idp", s.handleGetTenantIdp)
			r.With(s.requireScope(ActUserAdmin)).Put("/tenants/self/idp", s.handleSetTenantIdp)
			r.With(s.requireScope(ActUserAdmin)).Delete("/tenants/self/idp", s.handleDeleteTenantIdp)
			// BRD 59 WS3: white-label branding, self-scoped like idp/labels. GETs
			// are member-safe (the app shell + embed surfaces need to read the
			// brand); writes need identity.user.admin.
			r.Get("/tenants/self/branding", s.handleGetTenantBranding)
			r.Get("/tenants/self/branding/logo", s.handleGetTenantLogo)
			r.With(s.requireScope(ActUserAdmin)).Put("/tenants/self/branding", s.handleSetTenantBranding)
			r.With(s.requireScope(ActUserAdmin)).Post("/tenants/self/branding/logo", s.handleUploadTenantLogo)
			r.With(s.requireScope(ActUserAdmin)).Delete("/tenants/self/branding", s.handleDeleteTenantBranding)
			// BRD 60 WS2: an admin manages the tenant's external-agent
			// credentials (mint returns the plaintext key once; list is
			// metadata-only; delete revokes). Self-scoped on the caller's
			// tenant claim, tenant-admin gated.
			r.With(s.requireScope(ActUserAdmin)).Get("/tenants/self/external-agents", s.handleListExternalAgentKeys)
			r.With(s.requireScope(ActUserAdmin)).Post("/tenants/self/external-agents", s.handleCreateExternalAgentKey)
			r.With(s.requireScope(ActUserAdmin)).Delete("/tenants/self/external-agents/{id}", s.handleRevokeExternalAgentKey)
			r.Group(func(r chi.Router) {
				r.Use(s.requireSuperAdmin)
				r.Post("/tenants", s.handleCreateTenant)
				r.Get("/tenants", s.handleListTenants)
				r.Patch("/tenants/{id}", s.handlePatchTenant)
				r.Delete("/tenants/{id}", s.handleDeleteTenant)
				r.Post("/tenants/{id}/publish", s.handlePublishTenant)
				r.Post("/tenants/{id}/suspend", s.handleSuspendTenant)
				r.Post("/tenants/{id}/reactivate", s.handleReactivateTenant)
				r.Post("/tenants/{id}/provisioning/retry", s.handleRetryProvisioning)
				r.Get("/tenants/{id}/provisioning", s.handleProvisioningStatus)
				r.Post("/keys/rotate", s.handleRotateKeys)
				// First-class platform-admin registry (cross-tenant operators).
				r.Get("/platform/admins", s.handleListPlatformAdmins)
				r.Post("/platform/admins", s.handleCreatePlatformAdmin)
				r.Delete("/platform/admins/{id}", s.handleDeletePlatformAdmin)
				// IDN-FR-009 (Should): platform version registry — stub.
				r.Get("/platform-versions", s.handleNotImplemented("platform version registry (IDN-FR-009)"))
			})

			// Tenant-scoped user directory (tenant admins).
			r.Group(func(r chi.Router) {
				r.Use(s.requireScope(ActUserAdmin))
				r.Post("/users/invite", s.handleInviteUser)
				r.Get("/users", s.handleListUsers)
				r.Get("/users/{id}", s.handleGetUser)
				r.Patch("/users/{id}", s.handlePatchUser)
				r.Post("/users/{id}/activate", s.handleActivateUser)
				r.Post("/users/{id}/deactivate", s.handleDeactivateUser)
				r.Post("/users/{id}/invite/resend", s.handleResendInvite)
				r.Delete("/users/{id}", s.handleDeleteUser)
			})

			// Service accounts / API keys.
			r.Group(func(r chi.Router) {
				r.Use(s.requireScope(ActSvcAcctAdmin))
				r.Post("/service-accounts", s.handleCreateSA)
				r.Get("/service-accounts", s.handleListSAs)
				r.Post("/service-accounts/{id}/rotate", s.handleRotateSA)
				r.Delete("/service-accounts/{id}", s.handleRevokeSA)
			})

			r.With(s.requireScope(ActCredentialRead)).Get("/credentials", s.handleCredentials)

			// IDN-FR-024 (Should): SCIM 2.0 — stub.
			r.HandleFunc("/scim/v2/*", s.handleNotImplementedF("SCIM 2.0 provisioning (IDN-FR-024)"))
		})
	})
	return r
}

func (s *Server) handleNotImplemented(what string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeErr(w, r, domain.ENotImplemented(what+" is not implemented yet"))
	}
}

func (s *Server) handleNotImplementedF(what string) http.HandlerFunc {
	return s.handleNotImplemented(what)
}

func (s *Server) handleHealthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleReadyz(w http.ResponseWriter, r *http.Request) {
	if s.Ready != nil {
		if err := s.Ready(); err != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "unready", "error": err.Error()})
			return
		}
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}

func (s *Server) handleJWKS(w http.ResponseWriter, r *http.Request) {
	jwks, err := s.KM.JWKS()
	if err != nil {
		writeErr(w, r, err)
		return
	}
	w.Header().Set("Cache-Control", "max-age=300") // IDN-FR-051
	writeJSON(w, http.StatusOK, jwks)
}
