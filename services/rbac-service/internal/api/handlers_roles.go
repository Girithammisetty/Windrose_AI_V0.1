package api

import (
	"net/http"

	"github.com/windrose-ai/rbac-service/internal/domain"
)

type roleRequest struct {
	Name    string   `json:"name"`
	Actions []string `json:"actions"`
}

func (s *Server) handleCreateRole(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	var req roleRequest
	if !decodeBody(w, r, &req) {
		return
	}
	role, err := s.Store.CreateCustomRole(r.Context(), op, req.Name, req.Actions)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, role)
}

func (s *Server) handleGetRole(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	role, err := s.Store.GetRole(r.Context(), op.Tenant, id)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, role)
}

func (s *Server) handleListRoles(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	cursor, limit := pageParams(r)
	page, err := s.Store.ListRoles(r.Context(), op.Tenant, cursor, limit)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writePage(w, page)
}

// updateRoleRequest is a partial patch of a custom role: both fields are
// optional (nil = leave unchanged) so a caller can rename, recompose the
// action set, or both in a single atomic PATCH.
type updateRoleRequest struct {
	Name    *string   `json:"name"`
	Actions *[]string `json:"actions"`
}

func (s *Server) handleUpdateRole(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	var req updateRoleRequest
	if !decodeBody(w, r, &req) {
		return
	}
	role, err := s.Store.UpdateRole(r.Context(), op, id, req.Name, req.Actions)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, role)
}

func (s *Server) handleSetRoleActions(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	var req roleRequest
	if !decodeBody(w, r, &req) {
		return
	}
	role, err := s.Store.SetRoleActions(r.Context(), op, id, req.Actions)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, role)
}

func (s *Server) handleDeleteRole(w http.ResponseWriter, r *http.Request) {
	op, _ := opFrom(r)
	id, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	if err := s.Store.DeleteRole(r.Context(), op, id); err != nil {
		writeStoreError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleListActions(w http.ResponseWriter, r *http.Request) {
	cursor, limit := pageParams(r)
	page, err := s.Store.ListActions(r.Context(), cursor, limit)
	if err != nil {
		writeStoreError(w, r, err)
		return
	}
	writePage(w, page)
}

type registerActionsRequest struct {
	Actions []domain.ActionDef `json:"actions"`
}

// handleRegisterActions is the idempotent deploy-time registration API
// (RBC-FR-022); refreshes the Redis catalog key afterwards.
func (s *Server) handleRegisterActions(w http.ResponseWriter, r *http.Request) {
	var req registerActionsRequest
	if !decodeBody(w, r, &req) {
		return
	}
	if len(req.Actions) == 0 {
		writeError(w, r, http.StatusBadRequest, "VALIDATION_FAILED", "actions list is required", nil)
		return
	}
	if err := s.Store.RegisterActions(r.Context(), req.Actions); err != nil {
		writeStoreError(w, r, err)
		return
	}
	if s.Writer != nil {
		if catalog, err := s.Store.CatalogMap(r.Context()); err == nil {
			if v, err := s.Store.NextVersion(r.Context()); err == nil {
				_ = s.Writer.WriteCatalog(r.Context(), catalog, v)
			}
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"registered": len(req.Actions)})
}
