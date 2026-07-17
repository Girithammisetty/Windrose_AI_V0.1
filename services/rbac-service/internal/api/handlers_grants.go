package api

import (
	"net/http"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/store"
)

type grantSubject struct {
	Type string `json:"type"` // user | group
	ID   string `json:"id"`
}

type createGrantRequest struct {
	WorkspaceID string       `json:"workspace_id"`
	ResourceURN string       `json:"resource_urn"`
	Subject     grantSubject `json:"subject"`
	Level       string       `json:"level"`
}

func (s *Server) handleCreateGrant(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	var req createGrantRequest
	if !decodeBody(w, r, &req) {
		return
	}
	wsID, err := parseUUIDField(req.WorkspaceID)
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "workspace_id must be a uuid", nil)
		return
	}
	g, err := s.Store.CreateGrant(r.Context(), op, store.CreateGrantParams{
		WorkspaceID: wsID,
		ResourceURN: req.ResourceURN,
		SubjectType: domain.SubjectType(req.Subject.Type),
		SubjectID:   req.Subject.ID,
		Level:       domain.GrantLevel(req.Level),
	})
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, g)
}

func (s *Server) handleListGrants(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	urn := r.URL.Query().Get("resource_urn")
	if urn == "" {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "resource_urn query parameter is required", nil)
		return
	}
	entries, err := s.Store.EffectiveAccess(r.Context(), op.Tenant, urn)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	if entries == nil {
		entries = []store.EffectiveAccessEntry{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": entries, "page": PageInfo{HasMore: false}})
}

func (s *Server) handleDeleteGrant(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	claims := ClaimsFrom(r.Context())
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	overrideReason := r.Header.Get("X-Override-Reason")
	if err := s.Store.DeleteGrant(r.Context(), op, id, overrideReason, claims.HasScope(ScopeSuperAdmin)); err != nil {
		writeStoreError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
