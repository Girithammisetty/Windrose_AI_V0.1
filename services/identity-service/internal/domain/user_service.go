package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// LastAdminChecker guards BR-9 (a tenant's last admin cannot be deactivated).
// The real implementation reads the rbac-service projection; the default
// AllowAll stands in until rbac-service ships (documented adapter).
type LastAdminChecker interface {
	IsLastAdmin(ctx context.Context, tenantID, userID uuid.UUID) (bool, error)
}

type AllowAllLastAdminChecker struct{}

func (AllowAllLastAdminChecker) IsLastAdmin(context.Context, uuid.UUID, uuid.UUID) (bool, error) {
	return false, nil
}

// UserService owns the user directory + invite flow (IDN-FR-020..023).
type UserService struct {
	Store     Store
	Keycloak  KeycloakAdmin
	LastAdmin LastAdminChecker
	Clock     func() time.Time
}

func (s *UserService) now() time.Time { return s.Clock().UTC() }

// InviteRequest is the POST /users/invite body (IDN-FR-021).
type InviteRequest struct {
	Email    string   `json:"email"`
	FullName string   `json:"full_name,omitempty"`
	Groups   []string `json:"groups,omitempty"`
}

// Invite creates an invited user plus a 7-day invitation token; the token
// travels only in the user.invited outbox event (notification-service email).
func (s *UserService) Invite(ctx context.Context, tenant *Tenant, req InviteRequest, actor Actor) (*User, error) {
	if tenant.Status == TenantSuspended {
		return nil, ETenantSuspended()
	}
	if tenant.Status != TenantActive {
		return nil, EConflict("tenant is not active")
	}
	email, err := ValidateEmail(req.Email)
	if err != nil {
		return nil, err
	}
	if _, err := s.Store.GetUserByEmail(ctx, tenant.ID, email); err == nil {
		return nil, EConflict("a user with this email already exists")
	}
	now := s.now()
	idp, err := s.Keycloak.CreateUser(ctx, tenant.Name, email, req.FullName)
	if err != nil {
		return nil, err
	}
	uid, _ := uuid.NewV7()
	u := &User{
		ID: uid, TenantID: tenant.ID, Email: email, FullName: req.FullName,
		Status: UserInvited, IdpSubject: &idp, CreatedAt: now, UpdatedAt: now,
	}
	tok, hash, err := NewInvitationToken()
	if err != nil {
		return nil, err
	}
	// user_id + groups let rbac-service's consumer place the invited user into
	// their initial permission groups (IDN-FR-021), so they arrive with a role
	// instead of zero permissions. Groups are optional; omitted -> no membership.
	payload := map[string]any{
		"email": email, "activation_token": tok, "expires_at": now.Add(InvitationTTL),
		"user_id": u.ID.String(),
	}
	if len(req.Groups) > 0 {
		payload["groups"] = req.Groups
	}
	if err := s.Store.CreateUser(ctx, u,
		NewEvent(EvUserInvited, tenant.ID, actor, u.URN(), now, payload)); err != nil {
		return nil, err
	}
	invID, _ := uuid.NewV7()
	if err := s.Store.CreateInvitation(ctx, &Invitation{
		ID: invID, TenantID: tenant.ID, UserID: u.ID, TokenHash: hash,
		ExpiresAt: now.Add(InvitationTTL), CreatedAt: now, UpdatedAt: now,
	}); err != nil {
		return nil, err
	}
	return u, nil
}

// ResendInvite invalidates outstanding tokens and issues a fresh one (AC-5).
func (s *UserService) ResendInvite(ctx context.Context, tenantID, userID uuid.UUID, actor Actor) (*User, error) {
	u, err := s.Store.GetUser(ctx, tenantID, userID)
	if err != nil {
		return nil, err
	}
	if u.Status != UserInvited {
		return nil, EConflict("user is not in invited state")
	}
	now := s.now()
	if err := s.Store.InvalidateInvitations(ctx, tenantID, userID, now); err != nil {
		return nil, err
	}
	tok, hash, err := NewInvitationToken()
	if err != nil {
		return nil, err
	}
	invID, _ := uuid.NewV7()
	if err := s.Store.CreateInvitation(ctx, &Invitation{
		ID: invID, TenantID: tenantID, UserID: userID, TokenHash: hash,
		ExpiresAt: now.Add(InvitationTTL), CreatedAt: now, UpdatedAt: now,
	}, NewEvent(EvUserInvited, tenantID, actor, u.URN(), now, map[string]any{
		"email": u.Email, "activation_token": tok, "expires_at": now.Add(InvitationTTL), "resend": true,
	})); err != nil {
		return nil, err
	}
	return u, nil
}

// AcceptInvitation activates the user. Full production flow flips status on
// first SSO login (IDN-FR-021); the accept endpoint models that callback by
// receiving the Keycloak subject (documented simplification).
func (s *UserService) AcceptInvitation(ctx context.Context, token, idpSubject string) (*User, error) {
	inv, err := s.Store.GetInvitationByTokenHash(ctx, HashInvitationToken(token))
	if err != nil {
		return nil, ENotFound("invitation")
	}
	now := s.now()
	if err := inv.Usable(now); err != nil {
		return nil, err
	}
	u, err := s.Store.GetUser(ctx, inv.TenantID, inv.UserID)
	if err != nil {
		return nil, err
	}
	inv.AcceptedAt = &now
	inv.UpdatedAt = now
	if err := s.Store.UpdateInvitation(ctx, inv); err != nil {
		return nil, err
	}
	u.Status = UserActive
	if idpSubject != "" {
		u.IdpSubject = &idpSubject
	}
	u.LastLoginAt = &now
	u.UpdatedAt = now
	if err := s.Store.UpdateUser(ctx, u,
		NewEvent(EvUserActivated, u.TenantID, Actor{Type: "user", ID: u.ID.String()}, u.URN(), now, nil)); err != nil {
		return nil, err
	}
	return u, nil
}

// Deactivate per IDN-FR-022 + BR-9. Effective for OBO immediately (AC-6).
func (s *UserService) Deactivate(ctx context.Context, tenant *Tenant, userID uuid.UUID, actor Actor, override bool) (*User, error) {
	u, err := s.Store.GetUser(ctx, tenant.ID, userID)
	if err != nil {
		return nil, err
	}
	if u.Status == UserDeactivated {
		return u, nil // idempotent
	}
	last, err := s.LastAdmin.IsLastAdmin(ctx, tenant.ID, userID)
	if err != nil {
		return nil, err
	}
	if last && !(override && actor.Scope == "platform") {
		return nil, EConflict("cannot deactivate the last admin of a tenant (BR-9)")
	}
	now := s.now()
	if u.IdpSubject != nil {
		if err := s.Keycloak.DisableUser(ctx, tenant.Name, *u.IdpSubject); err != nil {
			return nil, err
		}
		if err := s.Keycloak.RevokeSessions(ctx, tenant.Name, *u.IdpSubject); err != nil {
			return nil, err
		}
	}
	u.Status = UserDeactivated
	u.UpdatedAt = now
	if err := s.Store.UpdateUser(ctx, u,
		NewEvent(EvUserDeactivated, tenant.ID, actor, u.URN(), now, nil)); err != nil {
		return nil, err
	}
	return u, nil
}

// SoftDelete per IDN-FR-023 (no hard deletion; memory/RAG erasure cascade
// rides the user.deleted event).
func (s *UserService) SoftDelete(ctx context.Context, tenantID, userID uuid.UUID, actor Actor) error {
	u, err := s.Store.GetUser(ctx, tenantID, userID)
	if err != nil {
		return err
	}
	now := s.now()
	u.DeletedAt = &now
	u.Status = UserDeactivated
	u.UpdatedAt = now
	return s.Store.UpdateUser(ctx, u,
		NewEvent(EvUserDeleted, tenantID, actor, u.URN(), now, nil))
}

// Patch updates mutable fields (full_name).
func (s *UserService) Patch(ctx context.Context, tenantID, userID uuid.UUID, fullName *string, actor Actor) (*User, error) {
	u, err := s.Store.GetUser(ctx, tenantID, userID)
	if err != nil {
		return nil, err
	}
	if fullName != nil {
		u.FullName = *fullName
	}
	u.UpdatedAt = s.now()
	if err := s.Store.UpdateUser(ctx, u,
		NewEvent(EvUserUpdated, tenantID, actor, u.URN(), u.UpdatedAt, nil)); err != nil {
		return nil, err
	}
	return u, nil
}
