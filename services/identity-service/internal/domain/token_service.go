package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// TokenService owns OBO exchange, autonomous agent tokens and API-key
// exchange (IDN-FR-032/041/042/043/044).
type TokenService struct {
	Store    Store
	Issuer   TokenIssuer
	Verifier TokenVerifier
	Limiter  RateLimiter
	Denylist Denylist
	Clock    func() time.Time
}

func (s *TokenService) now() time.Time { return s.Clock().UTC() }

// OBOExchange implements POST /token/obo (IDN-FR-041, US-6).
func (s *TokenService) OBOExchange(ctx context.Context, req OBORequest, traceID string) (*TokenResponse, error) {
	if req.AgentID == "" || req.AgentVersion == "" {
		return nil, EValidation("agent_id and agent_version are required",
			FieldError{Field: "agent_id", Message: "required"}, FieldError{Field: "agent_version", Message: "required"})
	}
	claims, err := s.Verifier.Verify(req.SubjectToken) // RS256-only; alg=none rejected (AC-13)
	if err != nil {
		return nil, EUnauthenticated("invalid subject token")
	}
	if claims.Typ != TypUser {
		return nil, EPermissionDenied("subject token must be a user token (BR-10: OBO tokens are not exchangeable)")
	}
	tenant, err := s.Store.GetTenant(ctx, claims.TenantID)
	if err != nil {
		return nil, EPermissionDenied("unknown tenant")
	}
	if err := tenantIssuable(tenant); err != nil {
		return nil, err // suspended -> TENANT_SUSPENDED (BR-4, AC-10)
	}
	user, err := s.resolveUser(ctx, claims)
	if err != nil {
		return nil, EPermissionDenied("subject user not found")
	}
	if user.Status != UserActive || user.DeletedAt != nil {
		// AC-6: deactivated users are excluded from OBO issuance immediately.
		return nil, EPermissionDenied("subject user is not active")
	}
	agent, err := s.Store.GetAgentPrincipal(ctx, tenant.ID, req.AgentID, req.AgentVersion)
	if err != nil {
		return nil, EAgentDisabled("agent version not enabled for tenant")
	}
	if err := agent.IssuableOBO(); err != nil {
		return nil, err // kill-switch / eval gate (AC-7)
	}
	now := s.now()
	if ok, retry := s.Limiter.Allow(user.ID.String()+"|"+req.AgentID, now); !ok {
		return nil, ERateLimited(retry) // AC-14
	}
	tok, expiresIn, err := s.Issuer.Issue(Claims{
		Subject:      "agent:" + req.AgentID + "@" + req.AgentVersion,
		TenantID:     tenant.ID,
		Typ:          TypAgentOBO,
		AgentID:      req.AgentID,
		AgentVersion: req.AgentVersion,
		OBOSub:       user.ID.String(),
		Scopes:       agent.Scopes, // OPA intersects with user grants at call time (MASTER-FR-015)
		SessionID:    req.SessionID,
	})
	if err != nil {
		return nil, err
	}
	ev := NewEvent(EvTokenOBOIssued, tenant.ID, Actor{Type: "user", ID: user.ID.String()}, agent.URN(), now, map[string]any{
		"agent_id": req.AgentID, "agent_version": req.AgentVersion, "session_id": req.SessionID,
	})
	ev.ViaAgent = &ViaAgent{AgentID: req.AgentID, Version: req.AgentVersion}
	ev.TraceID = traceID
	if err := s.Store.AppendOutbox(ctx, ev); err != nil {
		return nil, err
	}
	return &TokenResponse{AccessToken: tok, TokenType: "Bearer", ExpiresIn: expiresIn}, nil
}

func (s *TokenService) resolveUser(ctx context.Context, claims *Claims) (*User, error) {
	if uid, err := uuid.Parse(claims.Subject); err == nil {
		return s.Store.GetUser(ctx, claims.TenantID, uid)
	}
	return s.Store.GetUserBySub(ctx, claims.TenantID, claims.Subject)
}

// AutonomousToken implements POST /token/agent (IDN-FR-042). SPIFFE caller
// verification happens in the API layer; this validates the principal.
func (s *TokenService) AutonomousToken(ctx context.Context, req AutonomousTokenRequest) (*TokenResponse, error) {
	tenant, err := s.Store.GetTenant(ctx, req.TenantID)
	if err != nil {
		return nil, EPermissionDenied("unknown tenant")
	}
	if err := tenantIssuable(tenant); err != nil {
		return nil, err
	}
	agent, err := s.Store.GetAgentPrincipal(ctx, tenant.ID, req.AgentID, req.AgentVersion)
	if err != nil {
		return nil, EAgentDisabled("agent version not enabled for tenant")
	}
	if err := agent.IssuableOBO(); err != nil {
		return nil, err
	}
	if !agent.AutonomousAllowed {
		return nil, EAgentDisabled("agent version is not allowed to run autonomously for this tenant")
	}
	tok, expiresIn, err := s.Issuer.Issue(Claims{
		Subject:      "agent:" + req.AgentID + "@" + req.AgentVersion,
		TenantID:     tenant.ID,
		Typ:          TypAgentAutonomous,
		AgentID:      req.AgentID,
		AgentVersion: req.AgentVersion,
		Scopes:       agent.Scopes,
	})
	if err != nil {
		return nil, err
	}
	return &TokenResponse{AccessToken: tok, TokenType: "Bearer", ExpiresIn: expiresIn}, nil
}

// ExchangeAPIKey turns a tenant API key into a short-lived typ=service JWT
// (IDN-FR-032). Revoked keys are rejected via the denylist (AC-11); keys of
// suspended tenants get TENANT_SUSPENDED (AC-10).
func (s *TokenService) ExchangeAPIKey(ctx context.Context, apiKey, traceID string) (*TokenResponse, error) {
	saID, secret, err := ParseAPIKey(apiKey)
	if err != nil {
		return nil, err
	}
	if s.Denylist.IsRevoked(saID.String()) {
		return nil, EUnauthenticated("api key revoked")
	}
	tenantID, err := s.Store.ResolveAPIKeyTenant(ctx, saID)
	if err != nil {
		return nil, EUnauthenticated("unknown api key")
	}
	sa, err := s.Store.GetServiceAccount(ctx, tenantID, saID)
	if err != nil {
		return nil, EUnauthenticated("unknown api key")
	}
	now := s.now()
	if sa.RevokedAt != nil {
		return nil, EUnauthenticated("api key revoked")
	}
	if sa.ExpiresAt != nil && now.After(*sa.ExpiresAt) {
		return nil, EUnauthenticated("api key expired")
	}
	if !sa.VerifyPresentedSecret(secret, now) {
		return nil, EUnauthenticated("invalid api key")
	}
	tenant, err := s.Store.GetTenant(ctx, tenantID)
	if err != nil {
		return nil, EUnauthenticated("unknown api key")
	}
	if err := tenantIssuable(tenant); err != nil {
		if de, ok := AsError(err); ok && de.Code == CodeTenantSuspended {
			// AC-10: audit the blocked exchange.
			ev := NewEvent("security.suspended_tenant_denied", tenant.ID,
				Actor{Type: "service", ID: "sa:" + sa.ID.String()}, sa.URN(), now, map[string]any{"credential": "api_key"})
			ev.TraceID = traceID
			_ = s.Store.AppendOutbox(ctx, ev)
		}
		return nil, err
	}
	sa.LastUsedAt = &now // IDN-FR-033 last-used tracking
	sa.UpdatedAt = now
	if err := s.Store.UpdateServiceAccount(ctx, sa); err != nil {
		return nil, err
	}
	tok, expiresIn, err := s.Issuer.Issue(Claims{
		Subject:  "sa:" + sa.ID.String(),
		TenantID: tenant.ID,
		Typ:      TypService,
		Scopes:   sa.Scopes,
	})
	if err != nil {
		return nil, err
	}
	return &TokenResponse{AccessToken: tok, TokenType: "Bearer", ExpiresIn: expiresIn}, nil
}

// tenantIssuable gates all token issuance on tenant status (BR-4).
func tenantIssuable(t *Tenant) error {
	switch t.Status {
	case TenantActive:
		return nil
	case TenantSuspended:
		return ETenantSuspended()
	default:
		return EPermissionDenied("tenant is not active")
	}
}

// ApplyAgentEvent syncs agent principals from agent.events.v1 (IDN-FR-040,
// BRD §6 consumes). Invoked by the Kafka consumer (stubbed — see README) and
// directly by tests. Kill-switch takes effect on the next issuance (AC-7).
func (s *TokenService) ApplyAgentEvent(ctx context.Context, ev AgentRegistryEvent) error {
	now := s.now()
	existing, err := s.Store.GetAgentPrincipal(ctx, ev.TenantID, ev.AgentID, ev.AgentVersion)
	if err != nil {
		if ev.EventType != "agent_version.published" {
			return ENotFound("agent principal")
		}
		id, _ := uuid.NewV7()
		existing = &AgentPrincipal{
			ID: id, TenantID: ev.TenantID, AgentID: ev.AgentID, AgentVersion: ev.AgentVersion,
			EvalGateOK: true, Status: AgentActive, CreatedAt: now,
		}
	}
	switch ev.EventType {
	case "agent_version.published":
		existing.Scopes = ev.Scopes
		existing.AutonomousAllowed = ev.AutonomousAllowed
		existing.Status = AgentActive
	case "agent_version.killed":
		existing.Status = AgentKilled
	case "agent_version.eval_gate_changed":
		existing.EvalGateOK = ev.EvalGateOK
	default:
		return EValidation("unknown agent event type: " + ev.EventType)
	}
	existing.UpdatedAt = now
	return s.Store.UpsertAgentPrincipal(ctx, existing,
		NewEvent(EvAgentPrincipalSynced, ev.TenantID, Actor{Type: "service", ID: "identity-service"},
			existing.URN(), now, map[string]any{"event_type": ev.EventType}))
}
