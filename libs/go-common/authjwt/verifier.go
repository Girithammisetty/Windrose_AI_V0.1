// Package authjwt is the shared RS256 JWT verifier + HTTP middleware
// (MASTER-FR-010/011/014). It fetches the issuer's JWKS over HTTP, caches keys
// with a ≤5-min refresh, verifies RS256 signatures, rejects alg=none and any
// non-RS256 algorithm outright, validates exp/iss/aud, and populates the
// platform claim set. It speaks to a real JWKS endpoint (identity-service).
package authjwt

import (
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// Claim types (MASTER-FR-011).
const (
	TypUser            = "user"
	TypService         = "service"
	TypAgentOBO        = "agent_obo"
	TypAgentAutonomous = "agent_autonomous"
)

// Claims is the platform JWT claim set (MASTER-FR-011).
type Claims struct {
	Sub          string   `json:"sub"`
	TenantID     string   `json:"tenant_id"`
	Typ          string   `json:"typ"`
	AgentID      string   `json:"agent_id,omitempty"`
	AgentVersion string   `json:"agent_version,omitempty"`
	OboSub       string   `json:"obo_sub,omitempty"`
	Scopes       []string `json:"scopes,omitempty"`
	SessionID    string   `json:"session_id,omitempty"`
	jwt.RegisteredClaims
}

// Tenant parses the tenant_id claim.
func (c *Claims) Tenant() (uuid.UUID, error) { return uuid.Parse(c.TenantID) }

// EffectiveUser is whose permissions apply (OBO → original user, MASTER-FR-015).
func (c *Claims) EffectiveUser() string {
	if c.Typ == TypAgentOBO && c.OboSub != "" {
		return c.OboSub
	}
	return c.Sub
}

// HasScope reports whether the token carries scope ("*" is the wildcard).
func (c *Claims) HasScope(scope string) bool {
	for _, s := range c.Scopes {
		if s == scope || s == "*" {
			return true
		}
	}
	return false
}

// Verifier validates RS256 JWTs against a cached JWKS (MASTER-FR-010).
type Verifier struct {
	Issuer      string
	Audience    string
	RefreshMin  time.Duration // JWKS max staleness (default 5 min)
	Leeway      time.Duration // exp/nbf leeway (default 60s clock skew)
	staticKey   *rsa.PublicKey
	jwksURL     string
	client      *http.Client
	mu          sync.RWMutex
	keys        map[string]*rsa.PublicKey
	fetchedAt   time.Time
}

// NewJWKS verifies against a live JWKS endpoint (production path).
func NewJWKS(jwksURL, issuer, audience string) *Verifier {
	return &Verifier{
		Issuer: issuer, Audience: audience, jwksURL: jwksURL,
		RefreshMin: 5 * time.Minute, Leeway: 60 * time.Second,
		client: &http.Client{Timeout: 5 * time.Second},
	}
}

// NewStatic verifies against one fixed RSA public key (dev/unit tests).
func NewStatic(key *rsa.PublicKey, issuer, audience string) *Verifier {
	return &Verifier{
		Issuer: issuer, Audience: audience, staticKey: key,
		RefreshMin: 5 * time.Minute, Leeway: 60 * time.Second,
	}
}

// Verify parses and validates a compact JWT and returns its claims. Only RS256
// is accepted (alg=none / HS* rejected, MASTER-FR-014); exp is required;
// iss/aud validated when configured.
func (v *Verifier) Verify(ctx context.Context, tokenString string) (*Claims, error) {
	claims := &Claims{}
	opts := []jwt.ParserOption{
		jwt.WithValidMethods([]string{"RS256"}),
		jwt.WithExpirationRequired(),
		jwt.WithLeeway(v.Leeway),
	}
	if v.Issuer != "" {
		opts = append(opts, jwt.WithIssuer(v.Issuer))
	}
	if v.Audience != "" {
		opts = append(opts, jwt.WithAudience(v.Audience))
	}
	_, err := jwt.ParseWithClaims(tokenString, claims, func(t *jwt.Token) (any, error) {
		if v.staticKey != nil {
			return v.staticKey, nil
		}
		kid, _ := t.Header["kid"].(string)
		return v.keyFor(ctx, kid)
	}, opts...)
	if err != nil {
		return nil, err
	}
	if claims.Sub == "" || claims.TenantID == "" {
		return nil, errors.New("token missing sub or tenant_id")
	}
	if claims.Typ == "" {
		claims.Typ = TypUser
	}
	return claims, nil
}

func (v *Verifier) keyFor(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	v.mu.RLock()
	key, ok := v.keys[kid]
	fresh := time.Since(v.fetchedAt) < v.RefreshMin
	v.mu.RUnlock()
	if ok && fresh {
		return key, nil
	}
	if err := v.refresh(ctx); err != nil {
		if ok {
			return key, nil // stale key beats an outage
		}
		return nil, err
	}
	v.mu.RLock()
	defer v.mu.RUnlock()
	key, ok = v.keys[kid]
	if !ok {
		return nil, fmt.Errorf("unknown signing key %q", kid)
	}
	return key, nil
}

func (v *Verifier) refresh(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, v.jwksURL, nil)
	if err != nil {
		return err
	}
	resp, err := v.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("jwks fetch: status %d", resp.StatusCode)
	}
	var doc struct {
		Keys []struct {
			Kid string `json:"kid"`
			Kty string `json:"kty"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&doc); err != nil {
		return err
	}
	keys := map[string]*rsa.PublicKey{}
	for _, k := range doc.Keys {
		if k.Kty != "RSA" {
			continue
		}
		nb, err := base64.RawURLEncoding.DecodeString(k.N)
		if err != nil {
			continue
		}
		eb, err := base64.RawURLEncoding.DecodeString(k.E)
		if err != nil {
			continue
		}
		keys[k.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(nb), E: int(new(big.Int).SetBytes(eb).Int64())}
	}
	v.mu.Lock()
	v.keys = keys
	v.fetchedAt = time.Now()
	v.mu.Unlock()
	return nil
}
