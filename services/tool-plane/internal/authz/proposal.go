package authz

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
)

// ProposalGrantClaims is the cryptographically SIGNED proposal-execution grant
// issued by agent-runtime when a human approves a proposal (BRD TPL-FR-035, BR-3).
// tool-plane VERIFIES this grant before letting a write-tier call skip the
// PROPOSAL_REQUIRED gate. The grant binds the approved proposal to a specific
// tenant, tool, tier, and args digest, and is short-lived (exp). NOTHING from the
// untrusted MCP `_meta` is ever trusted for authorization — only these verified
// claims are.
//
// Grant format (RS256 JWS), issued by agent-runtime, verified by tool-plane:
//
//	{
//	  "iss": "<agent-runtime issuer>",          // must equal the configured issuer
//	  "sub": "<decider user id>",               // human who approved (decided_by)
//	  "exp": <unix>,                            // short-lived (≤ a few minutes)
//	  "proposal_id": "<approved proposal id>",
//	  "tenant_id":   "<tenant>",
//	  "tool_id":     "<tool_id>",
//	  "tier":        "write-proposal|write-direct|admin",
//	  "args_digest": "<sha256(canonical json args)>"
//	}
type ProposalGrantClaims struct {
	ProposalID string `json:"proposal_id"`
	TenantID   string `json:"tenant_id"`
	ToolID     string `json:"tool_id"`
	Tier       string `json:"tier"`
	ArgsDigest string `json:"args_digest"`
	jwt.RegisteredClaims
}

// ErrProposalInvalid is returned when a grant fails verification or binding.
var ErrProposalInvalid = errors.New("invalid proposal-execution grant")

// ProposalVerifier verifies RS256-signed proposal grants against the issuer's
// JWKS (production) or a static key (tests). Only RS256 is accepted; alg=none and
// non-RS256 are rejected outright (MASTER-FR-014). It mirrors go-common/authjwt's
// JWKS handling — the same mechanism every platform service uses.
type ProposalVerifier struct {
	Issuer    string // expected iss = agent-runtime
	Leeway    time.Duration
	staticKey *rsa.PublicKey
	jwksURL   string
	client    *http.Client
	mu        sync.RWMutex
	keys      map[string]*rsa.PublicKey
	fetchedAt time.Time
}

// NewProposalVerifierStatic verifies grants against one fixed RSA public key.
func NewProposalVerifierStatic(key *rsa.PublicKey, issuer string) *ProposalVerifier {
	return &ProposalVerifier{staticKey: key, Issuer: issuer, Leeway: 60 * time.Second}
}

// NewProposalVerifierJWKS verifies grants against the issuer's JWKS endpoint.
func NewProposalVerifierJWKS(jwksURL, issuer string) *ProposalVerifier {
	return &ProposalVerifier{jwksURL: jwksURL, Issuer: issuer, Leeway: 60 * time.Second,
		client: &http.Client{Timeout: 5 * time.Second}}
}

// GrantChecker is the port the enforce pipeline depends on: verify a signed grant
// and confirm it binds this exact (tenant, tool, tier, args_digest). Returns the
// trusted ProposalExecution on success, or an error (→ PROPOSAL_REQUIRED deny).
type GrantChecker interface {
	VerifyGrant(ctx context.Context, grant, tenant, toolID, tier, argsDigest string) (*ProposalExecution, error)
}

// VerifyGrant verifies the signed grant and its binding to this call. Any
// failure (bad signature, wrong issuer, expired, or a tenant/tool/tier/digest
// mismatch) returns ErrProposalInvalid so the pipeline falls back to
// PROPOSAL_REQUIRED — a forged/unsigned grant can never execute a write.
func (v *ProposalVerifier) VerifyGrant(ctx context.Context, grant, tenant, toolID, tier, argsDigest string) (*ProposalExecution, error) {
	if grant == "" {
		return nil, ErrProposalInvalid
	}
	claims := &ProposalGrantClaims{}
	opts := []jwt.ParserOption{
		jwt.WithValidMethods([]string{"RS256"}), // alg=none / HS* rejected
		jwt.WithExpirationRequired(),
		jwt.WithLeeway(v.Leeway),
	}
	if v.Issuer != "" {
		opts = append(opts, jwt.WithIssuer(v.Issuer))
	}
	_, err := jwt.ParseWithClaims(grant, claims, func(t *jwt.Token) (any, error) {
		if v.staticKey != nil {
			return v.staticKey, nil
		}
		kid, _ := t.Header["kid"].(string)
		return v.keyFor(ctx, kid)
	}, opts...)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrProposalInvalid, err)
	}
	// Binding checks: the verified grant must be for THIS call.
	if claims.ProposalID == "" {
		return nil, fmt.Errorf("%w: missing proposal_id", ErrProposalInvalid)
	}
	if claims.TenantID != tenant {
		return nil, fmt.Errorf("%w: tenant mismatch", ErrProposalInvalid)
	}
	if claims.ToolID != toolID {
		return nil, fmt.Errorf("%w: tool mismatch", ErrProposalInvalid)
	}
	if claims.Tier != "" && claims.Tier != tier {
		return nil, fmt.Errorf("%w: tier mismatch", ErrProposalInvalid)
	}
	if claims.ArgsDigest != argsDigest {
		return nil, fmt.Errorf("%w: args digest mismatch", ErrProposalInvalid)
	}
	return &ProposalExecution{
		ProposalID: claims.ProposalID,
		DecidedBy:  claims.Subject,
		ArgsDigest: claims.ArgsDigest,
	}, nil
}

func (v *ProposalVerifier) keyFor(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	v.mu.RLock()
	key, ok := v.keys[kid]
	fresh := time.Since(v.fetchedAt) < 5*time.Minute
	v.mu.RUnlock()
	if ok && fresh {
		return key, nil
	}
	if err := v.refresh(ctx); err != nil {
		if ok {
			return key, nil
		}
		return nil, err
	}
	v.mu.RLock()
	defer v.mu.RUnlock()
	key, ok = v.keys[kid]
	if !ok {
		return nil, fmt.Errorf("unknown grant signing key %q", kid)
	}
	return key, nil
}

func (v *ProposalVerifier) refresh(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, v.jwksURL, nil)
	if err != nil {
		return err
	}
	resp, err := v.client.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("grant jwks fetch: status %d", resp.StatusCode)
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
