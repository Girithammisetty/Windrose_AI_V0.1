package domain

import (
	"time"

	"github.com/google/uuid"
)

// Token types per MASTER-FR-011.
const (
	TypUser            = "user"
	TypService         = "service"
	TypAgentOBO        = "agent_obo"
	TypAgentAutonomous = "agent_autonomous"
)

// TokenTTL is the platform JWT TTL (MASTER-FR-010: 5 min).
const TokenTTL = 5 * time.Minute

// ClockSkew tolerance on all token validation (BR-8).
const ClockSkew = 60 * time.Second

// Claims is the platform JWT claim set (MASTER-FR-011 + IDN-FR-041).
type Claims struct {
	Subject      string    `json:"sub"`
	TenantID     uuid.UUID `json:"tenant_id"`
	Typ          string    `json:"typ"` // user|service|agent_obo|agent_autonomous
	AgentID      string    `json:"agent_id,omitempty"`
	AgentVersion string    `json:"agent_version,omitempty"`
	OBOSub       string    `json:"obo_sub,omitempty"` // original user for agent_obo
	Scopes       []string  `json:"scopes"`
	// PlatformAdmin (IDN: first-class cross-tenant operator) marks a human
	// platform administrator. It is a clean UI/BFF signal that travels alongside
	// the injected platform scopes; backend predicates still key off the scopes.
	PlatformAdmin bool   `json:"platform_admin,omitempty"`
	SessionID     string `json:"session_id,omitempty"`
	// Embedded-UI (IDN-FR-043): workspace-scoped embed tokens. `Embed` marks
	// the token as an embed token; `Surface` is the UI-surface allowlist; the
	// UI enforces both. `FrameAncestors` is the tenant's allowed embedding
	// origins, bound into the (signed) token so the UI can set a per-tenant
	// `frame-ancestors` CSP without a per-request lookup. `WorkspaceID` scopes
	// the token to one workspace.
	WorkspaceID    string   `json:"workspace_id,omitempty"`
	Embed          bool     `json:"embed,omitempty"`
	Surface        []string `json:"surface,omitempty"`
	FrameAncestors []string `json:"frame_ancestors,omitempty"`
	// Standard claims (filled by the issuer).
	Issuer    string    `json:"iss,omitempty"`
	Audience  string    `json:"aud,omitempty"`
	ExpiresAt time.Time `json:"-"`
	IssuedAt  time.Time `json:"-"`
	JTI       string    `json:"jti,omitempty"`
}

// HasScope reports whether the claim set carries the given action scope
// (MASTER-FR-016 action naming) or a covering wildcard.
func (c *Claims) HasScope(action string) bool {
	for _, s := range c.Scopes {
		if s == action || s == "platform.admin" {
			return true
		}
	}
	return false
}

// IsSuperAdmin: platform-staff tokens carry the platform.admin scope and no
// tenant binding requirement (IDN-FR-025 platform realm).
func (c *Claims) IsSuperAdmin() bool { return c.HasScope("platform.admin") }

// TokenIssuer abstracts JWT creation so domain logic stays crypto-free.
// Implemented by internal/keys (local RSA signer / Vault adapter).
type TokenIssuer interface {
	// Issue signs claims with the active key; returns the compact JWT and TTL seconds.
	Issue(claims Claims) (token string, expiresIn int, err error)
	// IssueWithTTL signs claims with an explicit lifetime (embed tokens are short).
	IssueWithTTL(claims Claims, ttl time.Duration) (token string, expiresIn int, err error)
}

// TokenVerifier verifies inbound platform JWTs. Implementations MUST accept
// only RS256/ES256 and reject alg=none (IDN-FR-045, AC-13).
type TokenVerifier interface {
	Verify(token string) (*Claims, error)
}

// OBORequest is the POST /token/obo body (IDN-FR-041).
type OBORequest struct {
	SubjectToken string `json:"subject_token"`
	AgentID      string `json:"agent_id"`
	AgentVersion string `json:"agent_version"`
	SessionID    string `json:"session_id"`
}

// AutonomousTokenRequest is the POST /token/agent body (IDN-FR-042).
type AutonomousTokenRequest struct {
	AgentID      string    `json:"agent_id"`
	AgentVersion string    `json:"version"`
	TenantID     uuid.UUID `json:"tenant_id"`
}

// TokenResponse is the issuance response shape.
type TokenResponse struct {
	AccessToken string `json:"access_token"`
	TokenType   string `json:"token_type"`
	ExpiresIn   int    `json:"expires_in"`
}
