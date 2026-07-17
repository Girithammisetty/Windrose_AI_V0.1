package reports

import (
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// TokenMinter mints short-lived platform JWTs so the async report-send
// activity can call chart-service's real, authorization-guarded data-fetch
// endpoints outside of any HTTP request (Temporal has no caller bearer token
// to forward, unlike the synchronous chart-service->semantic/query calls).
//
// It signs with the SAME shared platform signing key notification-service
// already receives as REGISTER_SIGNING_KEY_PEM/REGISTER_SIGNING_KID to
// register its action manifest with rbac-service (internal/register) — the
// real private key backing the live JWKS every service already verifies
// against in this deployment, not a bypass.
//
// The minted token is typ=agent_obo: the scheduled send runs on behalf of the
// subscription's creator (obo_sub), so chart-service's OPA check authorizes
// the run against that human's own chart.dashboard.read grant — the same
// dual-attribution (agent acting OBO a user) the platform already uses for
// agent-runtime's copilots (MASTER-FR-041), just for a durable scheduled
// action instead of an interactive one. In production this would go through
// a dedicated internal token-issuance path (e.g. an agent-principal grant via
// identity-service's POST /token/agent); the dev/e2e harness already hands
// every service this one shared signing key for that purpose, so reusing it
// here needs no new infrastructure.
type TokenMinter struct {
	KeyPEM   string
	KID      string
	Issuer   string
	Audience string

	// AgentID/AgentVersion identify the automation for audit trails.
	AgentID      string
	AgentVersion string
}

// NewTokenMinter builds a minter from the same env already wired for RBAC
// action registration (see cmd/server/main.go register.Config).
func NewTokenMinter(keyPEM, kid, issuer, audience string) *TokenMinter {
	return &TokenMinter{
		KeyPEM: keyPEM, KID: kid, Issuer: issuer, Audience: audience,
		AgentID: "notification-report-scheduler", AgentVersion: "v1",
	}
}

// MintOBO mints a 5-minute typ=agent_obo token for tenant, acting on behalf
// of userID (the subscription's created_by), scoped to workspaceID (the
// target dashboard's own workspace — semantic-service's workspace-scoped
// checks read `workspace_id` straight off the JWT claim, not off a resolved
// resource like chart-service does, so it must be set here explicitly).
func (m *TokenMinter) MintOBO(tenantID, workspaceID uuid.UUID, userID string) (string, error) {
	if m.KeyPEM == "" {
		return "", errors.New("report token minting not configured: REGISTER_SIGNING_KEY_PEM unset")
	}
	key, err := jwt.ParseRSAPrivateKeyFromPEM([]byte(m.KeyPEM))
	if err != nil {
		return "", fmt.Errorf("parse signing key: %w", err)
	}
	now := time.Now()
	claims := jwt.MapClaims{
		"sub":           "agent:" + m.AgentID + "@" + m.AgentVersion,
		"tenant_id":     tenantID.String(),
		"workspace_id":  workspaceID.String(),
		"typ":           "agent_obo",
		"agent_id":      m.AgentID,
		"agent_version": m.AgentVersion,
		"obo_sub":       userID,
		// windrose_authz.rego's agent_obo path requires scope_ok: the
		// INTERSECTION of the agent's own scopes and the impersonated user's
		// real grants (BR-6) — an agent_obo token with no scopes can never
		// pass, regardless of what the user holds. This automation calls
		// chart-service's dashboard/chart read + data-fetch endpoints (which
		// guard on chart.dashboard.read / chart.chart.read), and chart-service
		// in turn forwards this SAME bearer token to semantic-service (compile)
		// and query-service (execute) to actually resolve chart data — so the
		// token needs exactly the scopes that whole real resolve chain checks,
		// nothing broader.
		"scopes": []string{
			"chart.dashboard.read", "chart.chart.read",
			"semantic.compile.execute", "query.execution.execute", "query.execution.read",
		},
		"iss":    m.Issuer,
		"aud":    m.Audience,
		"iat":    now.Unix(),
		"exp":    now.Add(5 * time.Minute).Unix(),
		"jti":    "report-run-" + uuid.NewString(),
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	if m.KID != "" {
		tok.Header["kid"] = m.KID
	}
	return tok.SignedString(key)
}
