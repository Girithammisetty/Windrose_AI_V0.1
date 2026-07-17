package api

import (
	"crypto/rand"
	"encoding/base64"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

func parseID(r *http.Request) (uuid.UUID, error) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		return uuid.Nil, domain.ENotFound("resource")
	}
	return id, nil
}

// POST /tenants — 202 + operation_id when publish=true (MASTER-FR-027).
func (s *Server) handleCreateTenant(w http.ResponseWriter, r *http.Request) {
	var req domain.CreateTenantRequest
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	t, opID, err := s.Tenants.Create(r.Context(), req, actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if req.Publish {
		writeJSON(w, http.StatusAccepted, map[string]any{"operation_id": opID, "tenant": t})
		return
	}
	writeJSON(w, http.StatusCreated, t)
}

// GET /tenants/{id} — super-admin sees any tenant; a tenant admin sees only
// its own. Cross-tenant reads return 404 + security.cross_tenant_denied
// (MASTER-FR-003, AC-12).
func (s *Server) handleGetTenant(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if !claims.IsSuperAdmin() && claims.TenantID != id {
		now := s.Clock().UTC()
		ev := domain.NewEvent(domain.EvCrossTenantDenied, claims.TenantID, actorFrom(claims),
			domain.PlatformURN("tenant", id.String()), now, map[string]any{
				"endpoint": "GET /tenants/{id}", "target_tenant": id.String(),
			})
		ev.TraceID = TraceIDFrom(r.Context())
		_ = s.Store.AppendOutbox(r.Context(), ev)
		writeErr(w, r, domain.ENotFound("tenant"))
		return
	}
	t, err := s.Store.GetTenant(r.Context(), id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, t)
}

// GET /tenants/self — the SAFE, member-visible subset of the caller's own
// tenant (name/display name/status). Any authenticated member of a tenant may
// see what their organization is called — the admin gate on GET /tenants/{id}
// protects registry internals (owner_email, quotas, namespace, cell), none of
// which are returned here.
func (s *Server) handleGetTenantSelf(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	t, err := s.Store.GetTenant(r.Context(), claims.TenantID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"id": t.ID, "name": t.Name, "display_name": t.DisplayName, "status": t.Status,
	})
}

// PUT /tenants/{id}/embed-config (IDN-FR-043): set the tenant's allowed
// embedding origins and (re)generate the embed secret. Returns the plaintext
// secret ONCE (like an API key). Tenant-admin scoped; a tenant admin may only
// configure its own tenant.
func (s *Server) handleSetEmbedConfig(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if !claims.IsSuperAdmin() && claims.TenantID != id {
		writeErr(w, r, domain.ENotFound("tenant"))
		return
	}
	var body struct {
		AllowedOrigins []string `json:"allowed_origins"`
	}
	if err := decodeBody(r, &body); err != nil {
		writeErr(w, r, err)
		return
	}
	// Validate origins before minting a secret: they become the CSP
	// frame-ancestors of every embed of this tenant. Reject '*'/wildcards
	// (clickjacking) and header-injection characters.
	if err := domain.ValidateEmbedOrigins(body.AllowedOrigins); err != nil {
		writeErr(w, r, err)
		return
	}
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		writeErr(w, r, domain.EInternal("secret generation failed"))
		return
	}
	secret := "wes_" + base64.RawURLEncoding.EncodeToString(buf)
	cfg := &domain.TenantEmbedConfig{
		TenantID:       id,
		SecretHash:     domain.HashEmbedSecret(secret),
		AllowedOrigins: body.AllowedOrigins,
	}
	if err := s.Store.UpsertTenantEmbedConfig(r.Context(), cfg); err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"embed_secret":    secret, // shown once
		"allowed_origins": body.AllowedOrigins,
	})
}

// GET /tenants/{id}/embed-config (IDN-FR-043): read back the tenant's current
// embed configuration for the admin screen — never the secret itself (only
// SecretHash is stored), just whether one has been generated, the allowed
// origins, and when it was last changed. A 404 means "never configured",
// distinct from "configured with zero origins".
func (s *Server) handleGetEmbedConfig(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	cfg, err := s.Store.GetTenantEmbedConfig(r.Context(), id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"configured":      cfg.SecretHash != "",
		"allowed_origins": cfg.AllowedOrigins,
		"updated_at":      cfg.UpdatedAt,
	})
}

// GET /tenants — filters: status, cell, cloud (MASTER-FR-023).
func (s *Server) handleListTenants(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	page, err := domain.ParsePage(q.Get("limit"), q.Get("cursor"))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	f := domain.TenantFilter{
		Status: q.Get("filter[status]"),
		CellID: q.Get("filter[cell]"),
		Cloud:  q.Get("filter[cloud]"),
	}
	items, info, err := s.Store.ListTenants(r.Context(), f, page)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writePage(w, items, info)
}

func (s *Server) handlePatchTenant(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	var req domain.PatchTenantRequest
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	t, err := s.Tenants.Patch(r.Context(), id, req, actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, t) // mutations return the full resource (MASTER-FR-026)
}

func (s *Server) handlePublishTenant(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	opID, err := s.Tenants.Publish(r.Context(), id, actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"operation_id": opID})
}

func (s *Server) handleSuspendTenant(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	t, err := s.Tenants.Suspend(r.Context(), id, actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, t)
}

func (s *Server) handleReactivateTenant(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	t, drift, err := s.Tenants.Reactivate(r.Context(), id, actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"tenant": t, "drift": drift})
}

func (s *Server) handleRetryProvisioning(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	opID, err := s.Tenants.RetryProvisioning(r.Context(), id, actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"operation_id": opID})
}

func (s *Server) handleProvisioningStatus(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	steps, err := s.Tenants.ProvisioningStatus(r.Context(), id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if steps == nil {
		steps = []*domain.ProvisioningStep{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"steps": steps})
}

// DELETE /tenants/{id}?mode=archive|destroy&force=true (IDN-FR-008).
func (s *Server) handleDeleteTenant(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	mode := r.URL.Query().Get("mode")
	force := r.URL.Query().Get("force") == "true"
	t, err := s.Tenants.Delete(r.Context(), id, mode, force, actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, t)
}

// POST /keys/rotate — on-demand signing key rotation (IDN-FR-052).
func (s *Server) handleRotateKeys(w http.ResponseWriter, r *http.Request) {
	kid, err := s.KM.Rotate(r.Context(), actorFrom(ClaimsFrom(r.Context())))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"kid": kid})
}
