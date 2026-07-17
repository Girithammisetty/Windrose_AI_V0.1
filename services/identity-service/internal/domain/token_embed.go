package domain

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"net/url"
	"time"

	"github.com/google/uuid"
)

// maxEmbedOrigins caps how many origins a tenant may register.
const maxEmbedOrigins = 20

// ValidateEmbedOrigins enforces that every allowed frame-ancestor is a concrete
// `scheme://host[:port]` origin (http/https) or the CSP keyword 'self'. These
// values flow verbatim into the embed token's frame_ancestors claim and then
// into a `Content-Security-Policy: frame-ancestors ...` header, so an
// unvalidated entry means clickjacking ('*' / a wildcard lets any site frame
// the surface) or CSP header injection (a value with whitespace or ';' could
// append another directive). Reject all of those at write time.
func ValidateEmbedOrigins(origins []string) error {
	if len(origins) == 0 {
		return EValidation("allowed_origins must list at least one origin")
	}
	if len(origins) > maxEmbedOrigins {
		return EValidation("too many allowed_origins")
	}
	for _, o := range origins {
		if o == "'self'" {
			continue
		}
		if o == "" || o == "*" {
			return EValidation("allowed_origins must be concrete origins, not '' or '*'")
		}
		// Any whitespace, ';' or ',' would let a value smuggle extra CSP tokens
		// or directives into the header.
		for _, r := range o {
			if r == ';' || r == ',' || r == ' ' || r == '\t' || r == '\n' || r == '\r' || r == '\'' || r == '*' {
				return EValidation("allowed_origins contains a disallowed character")
			}
		}
		u, err := url.Parse(o)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
			return EValidation("allowed_origins entries must be http(s)://host[:port]")
		}
		// A bare origin has no path/query/fragment.
		if u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
			return EValidation("allowed_origins entries must be an origin only (no path)")
		}
	}
	return nil
}

// TenantEmbedConfig is a tenant's embedded-UI configuration (IDN-FR-043): the
// hashed embed secret its backend presents to POST /token/embed, and the
// origins allowed to frame Windrose surfaces (bound into embed tokens as the
// frame_ancestors claim).
type TenantEmbedConfig struct {
	TenantID       uuid.UUID
	SecretHash     string
	AllowedOrigins []string
	UpdatedAt      time.Time
}

// HashEmbedSecret hashes a high-entropy embed secret for at-rest storage.
func HashEmbedSecret(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return hex.EncodeToString(sum[:])
}

// VerifyEmbedSecret constant-time compares a presented secret to a stored hash.
func VerifyEmbedSecret(presented, storedHash string) bool {
	if storedHash == "" || presented == "" {
		return false
	}
	got := HashEmbedSecret(presented)
	return subtle.ConstantTimeCompare([]byte(got), []byte(storedHash)) == 1
}

// KnownEmbedSurfaces is the closed set of embeddable UI surfaces.
var KnownEmbedSurfaces = map[string]bool{"dashboard": true, "cases": true, "copilot": true}

const (
	EmbedTokenMinTTL     = 60 * time.Second
	EmbedTokenMaxTTL     = time.Hour
	EmbedTokenDefaultTTL = 10 * time.Minute
)

// EmbedRequest is the POST /token/embed body. `Secret` is the tenant's embed
// secret (presented by its backend, never the browser). The user context
// (Sub/WorkspaceID) identifies whose scoped view to mint.
type EmbedRequest struct {
	TenantID    string   `json:"tenant_id"`
	Secret      string   `json:"secret"`
	Sub         string   `json:"sub"`
	WorkspaceID string   `json:"workspace_id"`
	Scopes      []string `json:"scopes"`
	Surface     []string `json:"surface"`
	TTLSeconds  int      `json:"ttl_seconds"`
}

// EmbedExchange implements POST /token/embed (IDN-FR-043): validate the
// tenant's embed secret, then mint a SHORT-LIVED, workspace-scoped user JWT
// carrying embed/surface claims + the tenant's allowed frame-ancestors. Least
// privilege: narrow scopes, one workspace, minutes-long TTL.
func (s *TokenService) EmbedExchange(ctx context.Context, req EmbedRequest, traceID string) (*TokenResponse, error) {
	if req.TenantID == "" || req.Secret == "" || req.Sub == "" || req.WorkspaceID == "" {
		return nil, EValidation("tenant_id, secret, sub and workspace_id are required")
	}
	tenantID, err := uuid.Parse(req.TenantID)
	if err != nil {
		return nil, EValidation("tenant_id must be a uuid")
	}
	surface := make([]string, 0, len(req.Surface))
	for _, sfc := range req.Surface {
		if KnownEmbedSurfaces[sfc] {
			surface = append(surface, sfc)
		}
	}
	if len(surface) == 0 {
		return nil, EValidation("surface must include one of dashboard, cases, copilot")
	}

	cfg, err := s.Store.GetTenantEmbedConfig(ctx, tenantID)
	if err != nil || cfg == nil || !VerifyEmbedSecret(req.Secret, cfg.SecretHash) {
		// Uniform failure (no oracle on whether the tenant has embed configured).
		return nil, EUnauthenticated("invalid embed credentials")
	}
	tenant, err := s.Store.GetTenant(ctx, tenantID)
	if err != nil {
		return nil, EUnauthenticated("invalid embed credentials")
	}
	if err := tenantIssuable(tenant); err != nil {
		return nil, err
	}

	ttl := EmbedTokenDefaultTTL
	if req.TTLSeconds > 0 {
		ttl = time.Duration(req.TTLSeconds) * time.Second
	}
	if ttl < EmbedTokenMinTTL {
		ttl = EmbedTokenMinTTL
	}
	if ttl > EmbedTokenMaxTTL {
		ttl = EmbedTokenMaxTTL
	}

	scopes := req.Scopes
	if len(scopes) == 0 {
		scopes = []string{"chart.dashboard.read"}
	}
	tok, expiresIn, err := s.Issuer.IssueWithTTL(Claims{
		Subject:        req.Sub,
		TenantID:       tenantID,
		Typ:            TypUser,
		Scopes:         scopes,
		WorkspaceID:    req.WorkspaceID,
		Embed:          true,
		Surface:        surface,
		FrameAncestors: cfg.AllowedOrigins,
	}, ttl)
	if err != nil {
		return nil, err
	}
	ev := NewEvent("identity.embed_token_issued", tenantID,
		Actor{Type: "user", ID: req.Sub}, PlatformURN("tenant", tenantID.String()), s.now(),
		map[string]any{"surface": surface, "workspace_id": req.WorkspaceID, "ttl_seconds": int(ttl.Seconds())})
	ev.TraceID = traceID
	_ = s.Store.AppendOutbox(ctx, ev)
	return &TokenResponse{AccessToken: tok, TokenType: "Bearer", ExpiresIn: expiresIn}, nil
}
