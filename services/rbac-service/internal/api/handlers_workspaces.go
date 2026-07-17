package api

import (
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/rbac-service/internal/store"
)

func parseIDParam(w http.ResponseWriter, r *http.Request, name string) (uuid.UUID, bool) {
	id, err := uuid.Parse(chi.URLParam(r, name))
	if err != nil {
		// Malformed ids read as nonexistent resources.
		writeError(w, r, http.StatusNotFound, "NOT_FOUND", "resource not found", nil)
		return uuid.Nil, false
	}
	return id, true
}

func pageParams(r *http.Request) (cursor string, limit int) {
	q := r.URL.Query()
	cursor = q.Get("cursor")
	limit, _ = strconv.Atoi(q.Get("limit"))
	return cursor, limit
}

type workspaceRequest struct {
	Name        *string `json:"name"`
	Description *string `json:"description"`
	Public      *bool   `json:"public"`
}

func (s *Server) handleCreateWorkspace(w http.ResponseWriter, r *http.Request) {
	op, ok := opFrom(r)
	if !ok {
		writeError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "missing claims", nil)
		return
	}
	var req workspaceRequest
	if !decodeBody(w, r, &req) {
		return
	}
	name, desc, public := "", "", false
	if req.Name != nil {
		name = *req.Name
	}
	if req.Description != nil {
		desc = *req.Description
	}
	if req.Public != nil {
		public = *req.Public
	}
	ws, err := s.Store.CreateWorkspace(r.Context(), op, name, desc, public)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, ws)
}

func (s *Server) handleGetWorkspace(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	tenant, err := claims.Tenant()
	if err != nil {
		writeError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "bad tenant claim", nil)
		return
	}
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	admin, err := s.Store.IsTenantAdmin(r.Context(), tenant, claims.EffectiveUser())
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	ws, err := s.Store.GetWorkspace(r.Context(), tenant, id, claims.EffectiveUser(), admin)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, ws)
}

func (s *Server) handleListWorkspaces(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	tenant, err := claims.Tenant()
	if err != nil {
		writeError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "bad tenant claim", nil)
		return
	}
	admin, err := s.Store.IsTenantAdmin(r.Context(), tenant, claims.EffectiveUser())
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	cursor, limit := pageParams(r)
	page, err := s.Store.ListWorkspaces(r.Context(), tenant, claims.EffectiveUser(), admin,
		store.ArchivedFilter(r.URL.Query().Get("archived")), cursor, limit)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writePage(w, page)
}

func (s *Server) handleUpdateWorkspace(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	var req workspaceRequest
	if !decodeBody(w, r, &req) {
		return
	}
	ws, err := s.Store.UpdateWorkspace(r.Context(), op, id, store.UpdateWorkspaceParams{
		Name: req.Name, Description: req.Description, Public: req.Public,
	})
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, ws)
}

func (s *Server) handleArchiveWorkspace(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	ws, err := s.Store.ArchiveWorkspace(r.Context(), op, id)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, ws)
}

func (s *Server) handleRestoreWorkspace(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	ws, err := s.Store.RestoreWorkspace(r.Context(), op, id)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, ws)
}

func (s *Server) handleLinkGroup(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	wsID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	groupID, ok := parseIDParam(w, r, "group_id")
	if !ok {
		return
	}
	if err := s.Store.LinkContentGroup(r.Context(), op, wsID, groupID); err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"workspace_id": wsID.String(), "group_id": groupID.String(), "status": "linked"})
}

func (s *Server) handleUnlinkGroup(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	wsID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	groupID, ok := parseIDParam(w, r, "group_id")
	if !ok {
		return
	}
	if err := s.Store.UnlinkContentGroup(r.Context(), op, wsID, groupID); err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"workspace_id": wsID.String(), "group_id": groupID.String(), "status": "unlinked"})
}
