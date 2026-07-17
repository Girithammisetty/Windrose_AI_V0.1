package api

import (
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// POST /users/invite (IDN-FR-021). Tenant comes from the verified JWT only
// (MASTER-FR-001/002).
func (s *Server) handleInviteUser(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	var req domain.InviteRequest
	if err := decodeBody(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	tenant, err := s.Store.GetTenant(r.Context(), claims.TenantID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	u, err := s.Users.Invite(r.Context(), tenant, req, actorFrom(claims))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, u)
}

// POST /invitations/{token}/accept — public activation link (IDN-FR-021).
func (s *Server) handleAcceptInvitation(w http.ResponseWriter, r *http.Request) {
	var body struct {
		IdpSubject string `json:"idp_subject"`
	}
	if r.ContentLength > 0 {
		if err := decodeBody(r, &body); err != nil {
			writeErr(w, r, err)
			return
		}
	}
	u, err := s.Users.AcceptInvitation(r.Context(), chi.URLParam(r, "token"), body.IdpSubject)
	if err != nil {
		writeErr(w, r, err) // expired -> 410 + resend hint (AC-5)
		return
	}
	writeJSON(w, http.StatusOK, u)
}

func (s *Server) handleResendInvite(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	u, err := s.Users.ResendInvite(r.Context(), claims.TenantID, id, actorFrom(claims))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, u)
}

func (s *Server) handleListUsers(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	page, err := domain.ParsePage(r.URL.Query().Get("limit"), r.URL.Query().Get("cursor"))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	// filter[id]=a,b,c — batch hydration for bff-graphql's userById loader
	// (one call per page of cases, BFF-FR-030/031).
	filter, err := parseUserIDFilter(r.URL.Query().Get("filter[id]"))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	items, info, err := s.Store.ListUsers(r.Context(), claims.TenantID, filter, page)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writePage(w, items, info)
}

// parseUserIDFilter parses the comma-separated filter[id] query value.
func parseUserIDFilter(raw string) (domain.UserFilter, error) {
	var f domain.UserFilter
	if raw == "" {
		return f, nil
	}
	for _, part := range strings.Split(raw, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		id, err := uuid.Parse(part)
		if err != nil {
			return f, domain.EValidation("invalid filter[id]",
				domain.FieldError{Field: "filter[id]", Message: "must be a comma-separated list of uuids"})
		}
		f.IDs = append(f.IDs, id)
	}
	return f, nil
}

// userProfile is the minimal, non-admin-tier subset of domain.User safe to
// hand to ANY authenticated tenant member for display purposes (e.g. showing
// a case assignee's or comment author's name). It deliberately omits
// status/idp_subject/last_login_at/timestamps — those stay behind
// identity.user.admin on GET /users and GET /users/{id}.
type userProfile struct {
	ID       uuid.UUID `json:"id"`
	Email    string    `json:"email"`
	FullName string    `json:"full_name"`
}

// GET /api/v1/users/profiles?filter[id]=a,b,c — batch id->{id,email,full_name}
// lookup for display-only hydration (bff-graphql's userById loader: case
// assignee, comment author, activity actor). Deliberately NOT gated on
// identity.user.admin: unlike the tenant user directory (GET /users), this
// only ever resolves ids the caller already has from another authorized
// resource (a case, a comment) and returns none of the admin-tier fields —
// mirrors GET /tenants/self's "member-visible, no admin scope needed"
// precedent. filter[id] is REQUIRED (no bare listing) so this can never be
// used to enumerate the tenant's user directory.
func (s *Server) handleUserProfiles(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	raw := r.URL.Query().Get("filter[id]")
	if strings.TrimSpace(raw) == "" {
		writeErr(w, r, domain.EValidation("filter[id] is required",
			domain.FieldError{Field: "filter[id]", Message: "must be a non-empty comma-separated list of uuids"}))
		return
	}
	filter, err := parseUserIDFilter(raw)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if len(filter.IDs) == 0 {
		writeErr(w, r, domain.EValidation("filter[id] is required",
			domain.FieldError{Field: "filter[id]", Message: "must be a non-empty comma-separated list of uuids"}))
		return
	}
	limit := len(filter.IDs)
	if limit > domain.MaxPageLimit {
		limit = domain.MaxPageLimit
	}
	items, info, err := s.Store.ListUsers(r.Context(), claims.TenantID, filter, domain.PageRequest{Limit: limit})
	if err != nil {
		writeErr(w, r, err)
		return
	}
	profiles := make([]userProfile, 0, len(items))
	for _, u := range items {
		profiles = append(profiles, userProfile{ID: u.ID, Email: u.Email, FullName: u.FullName})
	}
	writePage(w, profiles, info)
}

func (s *Server) handleGetUser(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	// Store lookup is tenant-scoped: another tenant's user is a 404
	// (MASTER-FR-003) — no existence leak.
	u, err := s.Store.GetUser(r.Context(), claims.TenantID, id)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, u)
}

func (s *Server) handlePatchUser(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	var body struct {
		FullName *string `json:"full_name"`
	}
	if err := decodeBody(r, &body); err != nil {
		writeErr(w, r, err)
		return
	}
	u, err := s.Users.Patch(r.Context(), claims.TenantID, id, body.FullName, actorFrom(claims))
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, u)
}

func (s *Server) handleDeactivateUser(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	tenant, err := s.Store.GetTenant(r.Context(), claims.TenantID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	override := r.URL.Query().Get("override_last_admin") == "true" // BR-9, super-admin only
	u, err := s.Users.Deactivate(r.Context(), tenant, id, actorFrom(claims), override)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, u)
}

func (s *Server) handleDeleteUser(w http.ResponseWriter, r *http.Request) {
	claims := ClaimsFrom(r.Context())
	id, err := parseID(r)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if err := s.Users.SoftDelete(r.Context(), claims.TenantID, id, actorFrom(claims)); err != nil {
		writeErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
