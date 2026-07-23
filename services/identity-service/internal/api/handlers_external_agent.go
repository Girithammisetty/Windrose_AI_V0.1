package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/datacern-ai/identity-service/internal/domain"
)

// BRD 60 WS2: self-service external-agent credentials. A tenant admin mints a
// per-agent key (wr_xa_<id>.<secret>) so a customer's OWN agent can exchange it
// for a short-lived agent_autonomous token — without a harness-signed token or
// an agent-registry sync. The minted token still passes through the governed
// external-intent ingress (propose-only, four-eyes, tier ceiling), so the key
// only decides identity + scopes, never bypasses the proposal rails.

// handleExternalAgentTokenExchange implements POST /token/agent/external: the
// customer's agent presents its key (the credential IS the auth, like
// /token/embed) and receives a short-lived agent_autonomous token. Unauth edge.
func (s *Server) handleExternalAgentTokenExchange(w http.ResponseWriter, r *http.Request) {
	var req struct {
		APIKey string `json:"api_key"`
	}
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	resp, err := s.Tokens.ExternalAgentExchange(r.Context(), req.APIKey, TraceIDFrom(r.Context()))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// handleCreateExternalAgentKey implements POST /tenants/self/external-agents:
// an admin mints a credential for a named external agent. The plaintext key is
// returned ONCE in the response and never again (only its hash is stored).
func (s *Server) handleCreateExternalAgentKey(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	var body struct {
		AgentID      string   `json:"agent_id"`
		AgentVersion int      `json:"agent_version"`
		Scopes       []string `json:"scopes"`
		Label        string   `json:"label"`
	}
	if err := decodeBody(r, &body); err != nil {
		writeErr(w, r, err)
		return
	}
	if body.AgentID == "" {
		writeErr(w, r, domain.EValidation("agent_id is required"))
		return
	}
	now := s.Clock().UTC()
	key, plaintext, err := domain.NewExternalAgentKey(
		claims.TenantID, body.AgentID, body.AgentVersion, body.Scopes, body.Label, claims.Subject, now)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if err := s.Store.CreateExternalAgentKey(r.Context(), key); err != nil {
		writeErr(w, r, err)
		return
	}
	// The plaintext key is surfaced exactly once, alongside the stored row.
	writeJSON(w, http.StatusCreated, map[string]any{
		"key":        key,
		"plaintext":  plaintext,
		"shown_once": true,
	})
}

// handleListExternalAgentKeys implements GET /tenants/self/external-agents:
// list the tenant's credentials (metadata only — the secret hash is never
// serialized, and the plaintext is unrecoverable after creation).
func (s *Server) handleListExternalAgentKeys(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	keys, err := s.Store.ListExternalAgentKeys(r.Context(), claims.TenantID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if keys == nil {
		keys = []*domain.ExternalAgentKey{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"keys": keys})
}

// handleRevokeExternalAgentKey implements DELETE
// /tenants/self/external-agents/{id}: deactivate a credential. Tenant-scoped so
// an admin can only revoke their own tenant's keys.
func (s *Server) handleRevokeExternalAgentKey(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.EValidation("malformed key id"))
		return
	}
	if err := s.Store.RevokeExternalAgentKey(r.Context(), claims.TenantID, id); err != nil {
		writeErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
