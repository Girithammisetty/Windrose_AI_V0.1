package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/store"
)

type groupRequest struct {
	Name        *string `json:"name"`
	Description *string `json:"description"`
	GroupType   string  `json:"group_type"`
}

func (s *Server) handleCreateGroup(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	var req groupRequest
	if !decodeBody(w, r, &req) {
		return
	}
	name, desc := "", ""
	if req.Name != nil {
		name = *req.Name
	}
	if req.Description != nil {
		desc = *req.Description
	}
	g, err := s.Store.CreateGroup(r.Context(), op, name, desc, domain.GroupType(req.GroupType), false)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, g)
}

func (s *Server) handleGetGroup(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	g, err := s.Store.GetGroup(r.Context(), op.Tenant, id)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, g)
}

func (s *Server) handleListGroups(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	claims := ClaimsFrom(r.Context())
	includeAuto := r.URL.Query().Get("include_auto") == "true" && claims.HasScope(ScopeSuperAdmin)
	cursor, limit := pageParams(r)
	page, err := s.Store.ListGroups(r.Context(), op.Tenant,
		domain.GroupType(r.URL.Query().Get("type")), includeAuto, cursor, limit)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writePage(w, page)
}

func (s *Server) handleUpdateGroup(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	var req groupRequest
	if !decodeBody(w, r, &req) {
		return
	}
	g, err := s.Store.UpdateGroup(r.Context(), op, id, req.Name, req.Description)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, g)
}

func (s *Server) handleDeleteGroup(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	if err := s.Store.DeleteGroup(r.Context(), op, id); err != nil {
		writeStoreError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleListMembers(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	cursor, limit := pageParams(r)
	page, err := s.Store.ListMembers(r.Context(), op.Tenant, id, cursor, limit)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writePage(w, page)
}

// handleListGroupRoles reads the roles currently bound to a group (the read
// side of PUT/DELETE /groups/{id}/roles/{role_id}); cursor-paginated.
func (s *Server) handleListGroupRoles(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	cursor, limit := pageParams(r)
	page, err := s.Store.RolesForGroup(r.Context(), op.Tenant, id, cursor, limit)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writePage(w, page)
}

// handleListUserGroups reads the groups a user belongs to (reverse of group
// membership); cursor-paginated. The user id is an opaque subject string.
func (s *Server) handleListUserGroups(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	userID := chi.URLParam(r, "id")
	cursor, limit := pageParams(r)
	page, err := s.Store.GroupsForUser(r.Context(), op.Tenant, userID, cursor, limit)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writePage(w, page)
}

type addMemberRequest struct {
	ExpiresAt *string `json:"expires_at"`
}

func (s *Server) handleAddMember(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	userID := chi.URLParam(r, "user_id")
	var req addMemberRequest
	if r.ContentLength > 0 && !decodeBody(w, r, &req) {
		return
	}
	created, err := s.Store.AddMember(r.Context(), op, id, userID, req.ExpiresAt)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	status := http.StatusOK // duplicate add: idempotent no-op 200 (AC-9)
	if created {
		status = http.StatusCreated
	}
	writeJSON(w, status, map[string]any{"group_id": id.String(), "user_id": userID, "created": created})
}

func (s *Server) handleRemoveMember(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	claims := ClaimsFrom(r.Context())
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	userID := chi.URLParam(r, "user_id")
	overrideReason := r.Header.Get("X-Override-Reason")
	err := s.Store.RemoveMember(r.Context(), op, id, userID, overrideReason, claims.HasScope(ScopeSuperAdmin))
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

type bulkMembersRequest struct {
	Operations []store.BulkMemberOp `json:"operations"`
}

func (s *Server) handleBulkMembers(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	var req bulkMembersRequest
	if !decodeBody(w, r, &req) {
		return
	}
	results, err := s.Store.BulkMembers(r.Context(), op, id, req.Operations)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	failed := 0
	for _, res := range results {
		if !res.OK {
			failed++
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"results": results, "succeeded": len(results) - failed, "failed": failed,
	})
}

func (s *Server) handleBindRole(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	groupID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	roleID, ok := parseIDParam(w, r, "role_id")
	if !ok {
		return
	}
	if err := s.Store.BindGroupRole(r.Context(), op, groupID, roleID); err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"group_id": groupID.String(), "role_id": roleID.String(), "status": "bound"})
}

func (s *Server) handleUnbindRole(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	groupID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	roleID, ok := parseIDParam(w, r, "role_id")
	if !ok {
		return
	}
	if err := s.Store.UnbindGroupRole(r.Context(), op, groupID, roleID); err != nil {
		writeStoreError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
