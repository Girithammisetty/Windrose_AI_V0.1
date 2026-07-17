package keys

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// Issuer implements domain.TokenIssuer + domain.TokenVerifier over the
// KeyManager. Issuance builds the compact JWT by hand so the signature is
// produced through the Signer port (Vault-compatible); verification uses
// golang-jwt restricted to RS256 — alg=none is structurally impossible
// (IDN-FR-045, MASTER-FR-014, AC-13).
type Issuer struct {
	KM    *KeyManager
	Iss   string
	Aud   string
	TTL   time.Duration
	Clock func() time.Time
}

func NewIssuer(km *KeyManager, clock func() time.Time) *Issuer {
	return &Issuer{KM: km, Iss: "https://identity.windrose.ai", Aud: "windrose", TTL: domain.TokenTTL, Clock: clock}
}

type wireClaims struct {
	Sub            string   `json:"sub"`
	TenantID       string   `json:"tenant_id"`
	Typ            string   `json:"typ"`
	AgentID        string   `json:"agent_id,omitempty"`
	AgentVersion   string   `json:"agent_version,omitempty"`
	OBOSub         string   `json:"obo_sub,omitempty"`
	Scopes         []string `json:"scopes"`
	SessionID      string   `json:"session_id,omitempty"`
	WorkspaceID    string   `json:"workspace_id,omitempty"`
	Embed          bool     `json:"embed,omitempty"`
	Surface        []string `json:"surface,omitempty"`
	FrameAncestors []string `json:"frame_ancestors,omitempty"`
	Iss            string   `json:"iss"`
	Aud            string   `json:"aud"`
	Exp            int64    `json:"exp"`
	Iat            int64    `json:"iat"`
	Nbf            int64    `json:"nbf"`
	JTI            string   `json:"jti"`
}

// Issue signs claims with the active key via the Signer port.
func (i *Issuer) Issue(c domain.Claims) (string, int, error) {
	return i.IssueWithTTL(c, i.TTL)
}

// IssueWithTTL signs claims with an explicit lifetime (embed tokens are short).
func (i *Issuer) IssueWithTTL(c domain.Claims, ttl time.Duration) (string, int, error) {
	key, err := i.KM.SigningKey()
	if err != nil {
		return "", 0, err
	}
	now := i.Clock().UTC()
	jti, _ := uuid.NewV7()
	wc := wireClaims{
		Sub: c.Subject, TenantID: c.TenantID.String(), Typ: c.Typ,
		AgentID: c.AgentID, AgentVersion: c.AgentVersion, OBOSub: c.OBOSub,
		Scopes: c.Scopes, SessionID: c.SessionID,
		WorkspaceID: c.WorkspaceID, Embed: c.Embed, Surface: c.Surface,
		FrameAncestors: c.FrameAncestors,
		Iss:            i.Iss, Aud: i.Aud,
		Exp: now.Add(ttl).Unix(), Iat: now.Unix(), Nbf: now.Unix(), JTI: jti.String(),
	}
	if wc.Scopes == nil {
		wc.Scopes = []string{}
	}
	header := map[string]string{"alg": key.Alg, "typ": "JWT", "kid": key.KID}
	hb, _ := json.Marshal(header)
	cb, _ := json.Marshal(wc)
	signingString := base64.RawURLEncoding.EncodeToString(hb) + "." + base64.RawURLEncoding.EncodeToString(cb)
	sig, err := i.KM.Signer.Sign(context.Background(), key.KID, []byte(signingString))
	if err != nil {
		return "", 0, err
	}
	return signingString + "." + base64.RawURLEncoding.EncodeToString(sig), int(ttl.Seconds()), nil
}

// Verify parses and validates a platform JWT. Only RS256 is accepted;
// exp/iss/aud validated with 60s leeway (BR-8); retired kids fail (AC-8).
func (i *Issuer) Verify(tokenString string) (*domain.Claims, error) {
	parsed, err := jwt.Parse(tokenString,
		func(t *jwt.Token) (any, error) {
			kid, _ := t.Header["kid"].(string)
			if kid == "" {
				return nil, fmt.Errorf("missing kid")
			}
			return i.KM.VerificationKey(kid)
		},
		jwt.WithValidMethods([]string{"RS256"}), // alg=none / HS* rejected (AC-13)
		jwt.WithLeeway(domain.ClockSkew),
		jwt.WithIssuer(i.Iss),
		jwt.WithAudience(i.Aud),
		jwt.WithExpirationRequired(),
		jwt.WithTimeFunc(func() time.Time { return i.Clock().UTC() }),
	)
	if err != nil || !parsed.Valid {
		return nil, domain.EUnauthenticated("invalid token")
	}
	mc, ok := parsed.Claims.(jwt.MapClaims)
	if !ok {
		return nil, domain.EUnauthenticated("invalid token claims")
	}
	tenantStr, _ := mc["tenant_id"].(string)
	tenantID, err := uuid.Parse(tenantStr)
	if err != nil {
		return nil, domain.EUnauthenticated("invalid tenant_id claim")
	}
	out := &domain.Claims{TenantID: tenantID}
	out.Subject, _ = mc["sub"].(string)
	out.Typ, _ = mc["typ"].(string)
	out.AgentID, _ = mc["agent_id"].(string)
	out.AgentVersion, _ = mc["agent_version"].(string)
	out.OBOSub, _ = mc["obo_sub"].(string)
	out.SessionID, _ = mc["session_id"].(string)
	out.Issuer, _ = mc["iss"].(string)
	out.JTI, _ = mc["jti"].(string)
	if raw, ok := mc["scopes"].([]any); ok {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				out.Scopes = append(out.Scopes, s)
			}
		}
	}
	if out.Subject == "" || out.Typ == "" {
		return nil, domain.EUnauthenticated("missing required claims")
	}
	return out, nil
}
