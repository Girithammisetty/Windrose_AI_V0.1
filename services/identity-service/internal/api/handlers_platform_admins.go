package api

import (
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// Platform-admin registry endpoints (super-admin gated in server.go). These
// manage the first-class, cross-tenant platform operators — distinct from the
// per-tenant rbac "Admin" role.

// GET /platform/admins
func (s *Server) handleListPlatformAdmins(w http.ResponseWriter, r *http.Request) {
	admins, err := s.Store.ListPlatformAdmins(r.Context())
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": admins})
}

type createPlatformAdminReq struct {
	Email   string `json:"email"`
	UserSub string `json:"user_sub,omitempty"`
}

// POST /platform/admins
func (s *Server) handleCreatePlatformAdmin(w http.ResponseWriter, r *http.Request) {
	var req createPlatformAdminReq
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	email, err := domain.ValidateEmail(req.Email)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	grantedBy := ""
	if c := ClaimsFrom(r.Context()); c != nil {
		grantedBy = c.Subject
	}
	pa := &domain.PlatformAdmin{
		ID:        uuid.New(),
		UserSub:   req.UserSub,
		Email:     email,
		GrantedBy: grantedBy,
		GrantedAt: time.Now().UTC(),
	}
	if err := s.Store.CreatePlatformAdmin(r.Context(), pa); err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, pa)
}

// DELETE /platform/admins/{id}
func (s *Server) handleDeletePlatformAdmin(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if err := s.Store.DeletePlatformAdmin(r.Context(), id); err != nil {
		writeErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
