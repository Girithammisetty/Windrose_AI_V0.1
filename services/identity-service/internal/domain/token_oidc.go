package domain

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strings"

	"github.com/google/uuid"
)

// OIDCLoginRequest carries the ID token the web tier obtained from the tenant's
// OIDC IdP after the authorization-code + PKCE exchange (BYO-P4).
type OIDCLoginRequest struct {
	IDToken string `json:"id_token"`
}

// unverifiedIssuer reads the `iss` claim from an ID token WITHOUT verifying it —
// used only to ROUTE the token to a tenant's registered IdP config; that config
// then verifies signature+issuer+audience for real. A forged `iss` just routes
// to a config whose real JWKS won't validate the forged signature.
func unverifiedIssuer(raw string) string {
	parts := strings.Split(raw, ".")
	if len(parts) < 2 {
		return ""
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return ""
	}
	var claims struct {
		Iss string `json:"iss"`
	}
	if json.Unmarshal(payload, &claims) != nil {
		return ""
	}
	return claims.Iss
}

// OIDCLogin implements the real interactive-login half of BYO-P4: it verifies an
// external OIDC ID token against the IdP's own keys, resolves it to the Windrose
// user by email within the token's tenant, and mints the platform session JWT.
//
// Two-layer IdP resolution: first the token's `iss` is matched against a tenant's
// own registered IdP config (tenant_idp_configs — each tenant brings their own
// Okta/Auth0/Entra/Keycloak); only if none matches does it fall back to the
// legacy deployment-wide IDP + OIDCTenantID (unchanged behavior). A first
// successful SSO login activates an invited account and links the IdP subject.
// Downstream authorization runs off the RBAC projection (not JWT scopes), so the
// minted token carries identity + tenant and an empty scope list.
func (s *TokenService) OIDCLogin(ctx context.Context, req OIDCLoginRequest, traceID string) (*TokenResponse, error) {
	if req.IDToken == "" {
		return nil, EValidation("id_token is required", FieldError{Field: "id_token", Message: "required"})
	}

	idp, tenantID, err := s.resolveIdp(ctx, req.IDToken)
	if err != nil {
		return nil, err
	}

	ident, err := idp.VerifyIDToken(ctx, req.IDToken)
	if err != nil {
		return nil, EUnauthenticated("invalid id_token")
	}
	if ident.Email == "" {
		return nil, EUnauthenticated("id_token has no email claim to resolve a user")
	}

	tenant, err := s.Store.GetTenant(ctx, tenantID)
	if err != nil {
		return nil, EPermissionDenied("unknown tenant")
	}
	if err := tenantIssuable(tenant); err != nil {
		return nil, err
	}

	user, err := s.Store.GetUserByEmail(ctx, tenantID, ident.Email)
	if err != nil {
		// No pre-provisioned Windrose user for this verified identity. JIT
		// provisioning is a documented follow-up; for now deny cleanly.
		return nil, EPermissionDenied("no windrose user is provisioned for this identity")
	}
	if user.Status == UserDeactivated || user.DeletedAt != nil {
		return nil, EPermissionDenied("user is deactivated")
	}

	// First SSO login accepts the invitation and binds the IdP subject so
	// subsequent logins (and DisableUser/RevokeSessions) can key on it.
	now := s.now()
	dirty := false
	if user.Status == UserInvited {
		user.Status = UserActive
		dirty = true
	}
	if user.IdpSubject == nil || *user.IdpSubject != ident.Subject {
		sub := ident.Subject
		user.IdpSubject = &sub
		dirty = true
	}
	user.LastLoginAt = &now
	if dirty {
		user.UpdatedAt = now
	}
	// Best-effort: a login must not fail because the profile write lagged.
	_ = s.Store.UpdateUser(ctx, user)

	// First-class platform admin: a user in the platform_admins registry logs in
	// with the platform scopes + platform_admin claim, so every existing operator
	// predicate (IsSuperAdmin, RequireSuperAdmin, require_operator, IsPlatform)
	// lights up. OIDC normally mints empty scopes (authz runs off the rbac
	// projection); this is the one deliberate, registry-gated exception.
	scopes := []string{}
	platformAdmin := false
	if isPA, _ := s.Store.IsPlatformAdmin(ctx, user.ID.String(), user.Email); isPA {
		scopes = []string{"platform.admin", "super_admin", "operator", "ai.platform.admin"}
		platformAdmin = true
	}
	tok, expiresIn, err := s.Issuer.Issue(Claims{
		Subject:       user.ID.String(),
		TenantID:      tenant.ID,
		Typ:           TypUser,
		Scopes:        scopes,
		PlatformAdmin: platformAdmin,
	})
	if err != nil {
		return nil, err
	}
	return &TokenResponse{AccessToken: tok, TokenType: "Bearer", ExpiresIn: expiresIn}, nil
}

// resolveIdp picks the IdP + tenant to verify an ID token against: the tenant
// whose registered IdP config matches the token's issuer (BYO per-tenant), else
// the legacy deployment-wide IDP. Returns a clean "not enabled" error when
// neither is configured.
func (s *TokenService) resolveIdp(ctx context.Context, rawIDToken string) (IdentityProvider, uuid.UUID, error) {
	if iss := unverifiedIssuer(rawIDToken); iss != "" && s.IdpBuild != nil {
		if cfg, err := s.Store.GetTenantIdpConfigByIssuer(ctx, iss); err == nil && cfg != nil {
			if !cfg.Enabled {
				return nil, uuid.Nil, EPermissionDenied("the tenant's identity provider is disabled")
			}
			return s.providerFor(cfg), cfg.TenantID, nil
		}
	}
	// Legacy single-IdP deployment fallback (unchanged behavior).
	if s.IDP != nil && s.OIDCTenantID != uuid.Nil {
		return s.IDP, s.OIDCTenantID, nil
	}
	return nil, uuid.Nil, EValidation("no identity provider is configured for this token's issuer")
}
