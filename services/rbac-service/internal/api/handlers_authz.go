package api

import (
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/rbac-service/internal/authz"
	"github.com/windrose-ai/rbac-service/internal/domain"
)

func parseUUIDField(s string) (uuid.UUID, error) { return uuid.Parse(s) }

// meCapabilitiesResponse is the caller's own display view for the UI gate.
type meCapabilitiesResponse struct {
	UserID       string   `json:"user_id"`
	TenantID     string   `json:"tenant_id"`
	Roles        []string `json:"roles"`
	Capabilities []string `json:"capabilities"`
	Admin        bool     `json:"admin"`
	// Display name of the workspace the token is scoped to (workspace_id
	// claim), resolved under the caller's own visibility rules (public /
	// linked-group / admin, MASTER-FR-003). Empty when the claim is absent
	// or the workspace is invisible to the caller.
	WorkspaceName string `json:"workspace_name,omitempty"`
}

// handleMeCapabilities returns the CALLER's own roles + allowed action names
// from the materialized projection (RBC-FR-040), keyed by the verified JWT
// subject + tenant. This is a read of one's own permissions for UI display —
// NOT an authorization decision (MASTER-FR-002): the domain services still
// enforce every action. Guarded by "authenticated" only (any principal may
// read its own capabilities). Admins return the "*" wildcard capability.
func (s *Server) handleMeCapabilities(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	if claims == nil {
		writeError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing claims", nil)
		return
	}
	tenant, err := claims.Tenant()
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "invalid tenant claim", nil)
		return
	}
	user := claims.EffectiveUser()
	roles, actions, admin, _, err := s.Reader.EffectiveCapabilities(r.Context(), tenant.String(), user)
	if err != nil {
		writeError(w, r, http.StatusInternalServerError, "INTERNAL", "capabilities read failed", nil)
		return
	}
	caps := actions
	if admin {
		// The admin flag short-circuits every action check (BR-7); surface it
		// to the UI as the "*" wildcard rather than enumerating the catalog.
		caps = []string{"*"}
		if len(roles) == 0 {
			roles = []string{domain.RoleAdmin}
		}
	}
	if roles == nil {
		roles = []string{}
	}
	if caps == nil {
		caps = []string{}
	}
	// Resolve the token workspace's display name for the UI shell — same
	// visibility rules as GET /workspaces/{id}; a lookup failure only means
	// the name is omitted, never an error for the capabilities read itself.
	var wsName string
	if claims.WorkspaceID != "" {
		if wsID, perr := uuid.Parse(claims.WorkspaceID); perr == nil {
			if ws, gerr := s.Store.GetWorkspace(r.Context(), tenant, wsID, user, admin); gerr == nil {
				wsName = ws.Name
			}
		}
	}
	writeJSON(w, http.StatusOK, meCapabilitiesResponse{
		UserID: user, TenantID: tenant.String(), Roles: roles, Capabilities: caps, Admin: admin,
		WorkspaceName: wsName,
	})
}

type checkRequest struct {
	Subject     authz.Subject `json:"subject"`
	Action      string        `json:"action"`
	ResourceURN string        `json:"resource_urn,omitempty"`
	WorkspaceID string        `json:"workspace_id,omitempty"`
	Tenant      string        `json:"tenant"`
}

// handleAuthzCheck is the OPA cold-start / Redis-miss fallback (RBC-FR-045):
// SQL ground truth, and warms the projection keys as a side effect.
func (s *Server) handleAuthzCheck(w http.ResponseWriter, r *http.Request) {
	var req checkRequest
	if !decodeBody(w, r, &req) {
		return
	}
	if req.Subject.ID == "" || req.Action == "" || req.Tenant == "" {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "subject.id, action and tenant are required", nil)
		return
	}
	// Bind the requested tenant to the caller's identity (MASTER-FR-002:
	// tenant ids in payloads are never trusted for authorization). A service
	// token may only check within its own tenant; super-admins may check any
	// tenant (cross-cell operator tooling).
	claims := ClaimsFrom(r.Context())
	if req.Tenant != claims.TenantID && !claims.HasScope(ScopeSuperAdmin) {
		writeError(w, r, http.StatusForbidden, "PERMISSION_DENIED", "tenant does not match caller identity", nil)
		return
	}
	d, err := s.Checker.Check(r.Context(), authz.Input{
		Subject: req.Subject, Action: req.Action,
		ResourceURN: req.ResourceURN, WorkspaceID: req.WorkspaceID, Tenant: req.Tenant,
	})
	if err != nil {
		writeError(w, r, http.StatusInternalServerError, "INTERNAL", "check failed", nil)
		return
	}
	writeJSON(w, http.StatusOK, d)
}

type explainRequest struct {
	UserID      string   `json:"user_id"`
	Typ         string   `json:"typ,omitempty"`
	Scopes      []string `json:"scopes,omitempty"`
	Action      string   `json:"action"`
	ResourceURN string   `json:"resource_urn,omitempty"`
	WorkspaceID string   `json:"workspace_id,omitempty"`
}

// handleAuthzExplain answers "why can user X do Y" (RBC-FR-046, US-7).
// Tenant comes from the caller's verified token, never the body.
func (s *Server) handleAuthzExplain(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	var req explainRequest
	if !decodeBody(w, r, &req) {
		return
	}
	if req.UserID == "" || req.Action == "" {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "user_id and action are required", nil)
		return
	}
	typ := req.Typ
	if typ == "" {
		typ = domain.TypUser
	}
	subject := authz.Subject{ID: req.UserID, Typ: typ, Scopes: req.Scopes}
	if typ == domain.TypAgentOBO {
		subject.ID = "agent:" + req.UserID
		subject.OboSub = req.UserID
	}
	exp, err := s.Checker.Explain(r.Context(), authz.Input{
		Subject: subject, Action: req.Action,
		ResourceURN: req.ResourceURN, WorkspaceID: req.WorkspaceID,
		Tenant: claims.TenantID,
	})
	if err != nil {
		writeError(w, r, http.StatusInternalServerError, "INTERNAL", "explain failed", nil)
		return
	}
	writeJSON(w, http.StatusOK, exp)
}
