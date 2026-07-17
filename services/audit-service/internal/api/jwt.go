// Package api is audit-service's HTTP layer (BRD 18 §5): chi router, RS256 JWT
// verification, tenant-context from the verified token only, OPA-backed
// authorization, the master error/pagination envelopes and the admin handlers.
package api

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

// Claims are the platform JWT claims (MASTER-FR-011).
type Claims struct {
	Sub          string   `json:"sub"`
	TenantID     string   `json:"tenant_id"`
	Typ          string   `json:"typ"`
	AgentID      string   `json:"agent_id,omitempty"`
	AgentVersion string   `json:"agent_version,omitempty"`
	OboSub       string   `json:"obo_sub,omitempty"`
	Scopes       []string `json:"scopes,omitempty"`
	jwt.RegisteredClaims
}

// Tenant parses the tenant claim.
func (c *Claims) Tenant() (uuid.UUID, error) { return uuid.Parse(c.TenantID) }

// HasScope reports whether the token carries scope s.
func (c *Claims) HasScope(s string) bool {
	for _, sc := range c.Scopes {
		if sc == s || sc == "*" {
			return true
		}
	}
	return false
}

// Verifier validates RS256 JWTs (MASTER-FR-010) via JWKS (cached, ≤5min refresh)
// or a static test key; exp/iss/aud validated; alg=none rejected (MASTER-FR-014).
type Verifier struct {
	Issuer   string
	Audience string

	staticKey *rsa.PublicKey
	jwksURL   string

	mu        sync.RWMutex
	keys      map[string]*rsa.PublicKey
	fetchedAt time.Time
	client    *http.Client
}

// NewVerifierStatic verifies against one fixed RSA public key (dev/test).
func NewVerifierStatic(key *rsa.PublicKey, issuer, audience string) *Verifier {
	return &Verifier{staticKey: key, Issuer: issuer, Audience: audience}
}

// NewVerifierJWKS verifies against a JWKS endpoint (production).
func NewVerifierJWKS(jwksURL, issuer, audience string) *Verifier {
	return &Verifier{jwksURL: jwksURL, Issuer: issuer, Audience: audience, client: &http.Client{Timeout: 5 * time.Second}}
}

// Verify parses and validates a compact JWT, returning its claims.
func (v *Verifier) Verify(ctx context.Context, tokenString string) (*Claims, error) {
	claims := &Claims{}
	opts := []jwt.ParserOption{
		jwt.WithValidMethods([]string{"RS256"}),
		jwt.WithExpirationRequired(),
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
		claims.Typ = "user"
	}
	return claims, nil
}

func (v *Verifier) keyFor(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	v.mu.RLock()
	key, ok := v.keys[kid]
	fresh := time.Since(v.fetchedAt) < 5*time.Minute
	v.mu.RUnlock()
	if ok && fresh {
		return key, nil
	}
	if err := v.refreshJWKS(ctx); err != nil {
		if ok {
			return key, nil
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

func (v *Verifier) refreshJWKS(ctx context.Context) error {
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
