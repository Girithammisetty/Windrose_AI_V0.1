package api

import (
	"net/http"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// POST /token/obo (IDN-FR-041). The subject_token in the body carries the
// authentication; no separate bearer required.
func (s *Server) handleOBO(w http.ResponseWriter, r *http.Request) {
	var req domain.OBORequest
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	resp, err := s.Tokens.OBOExchange(r.Context(), req, TraceIDFrom(r.Context()))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// POST /token/embed (IDN-FR-043): edge exchange of a tenant embed secret +
// user context for a short-lived, workspace-scoped embed token. The secret is
// presented by the tenant's backend (never the browser); no bearer required.
func (s *Server) handleEmbedToken(w http.ResponseWriter, r *http.Request) {
	var req domain.EmbedRequest
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	resp, err := s.Tokens.EmbedExchange(r.Context(), req, TraceIDFrom(r.Context()))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// POST /token/agent (IDN-FR-042): only agent-runtime, SPIFFE-verified.
func (s *Server) handleAgentToken(w http.ResponseWriter, r *http.Request) {
	spiffe, _ := r.Context().Value(ctxSpiffeID).(string)
	if spiffe == "" || !s.TrustedSpiffeIDs[spiffe] {
		writeErr(w, r, domain.EPermissionDenied("caller is not an authorized workload (SPIFFE identity required)"))
		return
	}
	var req domain.AutonomousTokenRequest
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	resp, err := s.Tokens.AutonomousToken(r.Context(), req)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// POST /token/apikey (IDN-FR-032): edge exchange of an API key for a
// short-lived typ=service JWT.
func (s *Server) handleAPIKeyExchange(w http.ResponseWriter, r *http.Request) {
	var req struct {
		APIKey string `json:"api_key"`
	}
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	resp, err := s.Tokens.ExchangeAPIKey(r.Context(), req.APIKey, TraceIDFrom(r.Context()))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// --- service accounts ---

func (s *Server) handleCreateSA(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	var req domain.CreateServiceAccountRequest
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	created, err := s.SAs.Create(r.Context(), claims.TenantID, req, actorFrom(claims))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, created) // api_key shown once (BR-11)
}

func (s *Server) handleListSAs(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	page, err := domain.ParsePage(r.URL.Query().Get("limit"), r.URL.Query().Get("cursor"))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	items, info, err := s.Store.ListServiceAccounts(r.Context(), claims.TenantID, page)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writePage(w, items, info)
}

func (s *Server) handleRotateSA(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	rotated, err := s.SAs.Rotate(r.Context(), claims.TenantID, id, actorFrom(claims))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, rotated)
}

func (s *Server) handleRevokeSA(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if err := s.SAs.Revoke(r.Context(), claims.TenantID, id, actorFrom(claims)); err != nil {
		writeErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// GET /credentials (US-8): active credentials inventory per tenant.
func (s *Server) handleCredentials(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	entries, err := s.SAs.CredentialInventory(r.Context(), claims.TenantID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": entries})
}
