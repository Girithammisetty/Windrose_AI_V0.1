// Package keys implements platform signing-key management (IDN-FR-050..052):
// a Signer port (local RSA dev impl here; Vault transit adapter in
// internal/adapters/vault), the KeyManager rotation/overlap logic, JWKS
// publication, and the JWT issuer/verifier used across the service.
package keys

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"sort"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// Signer signs JWT signing strings with a named key. Private material never
// crosses this interface (IDN-FR-050: prod keys live in Vault transit).
type Signer interface {
	// Generate creates a new RS256 keypair and returns its kid + public key PEM.
	Generate(ctx context.Context) (kid string, publicPEM string, err error)
	// Sign returns the RS256 signature over signingString.
	Sign(ctx context.Context, kid string, signingString []byte) ([]byte, error)
}

// LocalSigner is the dev implementation: RSA-2048 keypairs generated at boot,
// held only in process memory. NOT for production (see adapters/vault TODO).
type LocalSigner struct {
	mu   sync.RWMutex
	keys map[string]*rsa.PrivateKey
}

func NewLocalSigner() *LocalSigner { return &LocalSigner{keys: map[string]*rsa.PrivateKey{}} }

func (s *LocalSigner) Generate(ctx context.Context) (string, string, error) {
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return "", "", err
	}
	kid := "kid-" + uuid.NewString()
	der, err := x509.MarshalPKIXPublicKey(&priv.PublicKey)
	if err != nil {
		return "", "", err
	}
	pemStr := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
	s.mu.Lock()
	s.keys[kid] = priv
	s.mu.Unlock()
	return kid, pemStr, nil
}

func (s *LocalSigner) Sign(ctx context.Context, kid string, signingString []byte) ([]byte, error) {
	s.mu.RLock()
	priv, ok := s.keys[kid]
	s.mu.RUnlock()
	if !ok {
		return nil, fmt.Errorf("unknown kid %s (local signer keys do not survive restarts)", kid)
	}
	sum := sha256.Sum256(signingString)
	return rsa.SignPKCS1v15(rand.Reader, priv, crypto.SHA256, sum[:])
}

// KeyManager owns the signing-key registry and the rotation overlap rules
// (IDN-FR-052): a new key is published in JWKS >= OverlapLead before use;
// the previous key is retired only after max token TTL + clock skew past
// the new key's not_before.
type KeyManager struct {
	Store       domain.Store
	Signer      Signer
	Clock       func() time.Time
	OverlapLead time.Duration // default 10 min

	mu    sync.RWMutex
	cache []*domain.SigningKey
	// preferredKID breaks not_before ties in SigningKey toward the key this
	// process established as signable at Bootstrap (F-1). Without it, a
	// restart's recovery key and the stale (unmintable) key can share a
	// not_before under a coarse clock and SigningKey might pick the stale one.
	preferredKID string
}

func NewKeyManager(store domain.Store, signer Signer, clock func() time.Time) *KeyManager {
	return &KeyManager{Store: store, Signer: signer, Clock: clock, OverlapLead: 10 * time.Minute}
}

func (m *KeyManager) now() time.Time { return m.Clock().UTC() }

// Bootstrap ensures at least one immediately-usable key exists that THIS
// signer can actually sign with.
//
// F-1: with the LocalSigner (private keys held only in process memory, lost
// on restart), a persisted "usable" key found in the registry after a restart
// is unmintable — the fresh signer has no private key for that kid, so every
// issuance would fail with "unknown kid". We therefore probe the active key
// with a real signature; if the signer cannot produce one, we mint a new key
// with not_before=now (immediately usable) so token issuance always works
// after boot. (A Vault-backed signer keeps its keys across restarts and the
// probe passes, so no spurious rotation happens there.)
func (m *KeyManager) Bootstrap(ctx context.Context) error {
	if err := m.refresh(ctx); err != nil {
		return err
	}
	if key, err := m.SigningKey(); err == nil && m.signerCanSign(ctx, key.KID) {
		m.setPreferred(key.KID) // pin the signable key against tie-break ambiguity
		return nil
	}
	now := m.now()
	kid, pub, err := m.Signer.Generate(ctx)
	if err != nil {
		return err
	}
	if err := m.Store.SaveSigningKey(ctx, &domain.SigningKey{
		KID: kid, Alg: "RS256", PublicKeyPEM: pub, NotBefore: now, CreatedAt: now, UpdatedAt: now,
	}); err != nil {
		return err
	}
	m.setPreferred(kid)
	return m.refresh(ctx)
}

func (m *KeyManager) setPreferred(kid string) {
	m.mu.Lock()
	m.preferredKID = kid
	m.mu.Unlock()
}

// signerCanSign probes whether the configured signer holds the private key
// for kid (F-1 restart safety).
func (m *KeyManager) signerCanSign(ctx context.Context, kid string) bool {
	_, err := m.Signer.Sign(ctx, kid, []byte("bootstrap-probe"))
	return err == nil
}

// Rotate creates a new key usable after OverlapLead and schedules retirement
// of the current key at newKey.notBefore + TokenTTL + ClockSkew (IDN-FR-052).
func (m *KeyManager) Rotate(ctx context.Context, actor domain.Actor) (string, error) {
	now := m.now()
	kid, pub, err := m.Signer.Generate(ctx)
	if err != nil {
		return "", err
	}
	notBefore := now.Add(m.OverlapLead)
	newKey := &domain.SigningKey{
		KID: kid, Alg: "RS256", PublicKeyPEM: pub, NotBefore: notBefore, CreatedAt: now, UpdatedAt: now,
	}
	if err := m.Store.SaveSigningKey(ctx, newKey,
		domain.NewEvent(domain.EvSigningKeyRotated, uuid.Nil, actor,
			domain.PlatformURN("signing_key", kid), now, map[string]any{"not_before": notBefore})); err != nil {
		return "", err
	}
	// Schedule retirement of all older non-retired keys.
	retireAt := notBefore.Add(domain.TokenTTL + domain.ClockSkew)
	ks, err := m.Store.ListSigningKeys(ctx)
	if err != nil {
		return "", err
	}
	for _, k := range ks {
		if k.KID != kid && k.RetiredAt == nil {
			k.RetiredAt = &retireAt
			k.UpdatedAt = now
			if err := m.Store.UpdateSigningKey(ctx, k); err != nil {
				return "", err
			}
		}
	}
	return kid, m.refresh(ctx)
}

func (m *KeyManager) refresh(ctx context.Context) error {
	ks, err := m.Store.ListSigningKeys(ctx)
	if err != nil {
		return err
	}
	sort.Slice(ks, func(i, j int) bool { return ks[i].NotBefore.Before(ks[j].NotBefore) })
	m.mu.Lock()
	m.cache = ks
	m.mu.Unlock()
	return nil
}

// SigningKey returns the newest key already usable at `now`.
func (m *KeyManager) SigningKey() (*domain.SigningKey, error) {
	now := m.now()
	m.mu.RLock()
	defer m.mu.RUnlock()
	var best *domain.SigningKey
	for _, k := range m.cache {
		if k.NotBefore.After(now) {
			continue
		}
		if k.RetiredAt != nil && !now.Before(*k.RetiredAt) {
			continue
		}
		switch {
		case best == nil:
			best = k
		case k.NotBefore.After(best.NotBefore):
			best = k
		case k.NotBefore.Equal(best.NotBefore) && k.KID == m.preferredKID:
			// F-1 tie-break: prefer the key this process can actually sign with.
			best = k
		}
	}
	if best == nil {
		return nil, errors.New("no active signing key")
	}
	return best, nil
}

// VerificationKey resolves a kid for verification. Retired keys fail (AC-8).
func (m *KeyManager) VerificationKey(kid string) (*rsa.PublicKey, error) {
	now := m.now()
	m.mu.RLock()
	defer m.mu.RUnlock()
	for _, k := range m.cache {
		if k.KID != kid {
			continue
		}
		if k.RetiredAt != nil && !now.Before(*k.RetiredAt) {
			return nil, errors.New("signing key retired")
		}
		return parsePublicPEM(k.PublicKeyPEM)
	}
	return nil, errors.New("unknown kid")
}

// JWK / JWKS per RFC 7517 (IDN-FR-051). Future keys (inside the overlap
// lead) are included so verifiers have them >=10 min before first use.
type JWK struct {
	Kty string `json:"kty"`
	Use string `json:"use"`
	Alg string `json:"alg"`
	Kid string `json:"kid"`
	N   string `json:"n"`
	E   string `json:"e"`
}

type JWKS struct {
	Keys []JWK `json:"keys"`
}

func (m *KeyManager) JWKS() (*JWKS, error) {
	now := m.now()
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := &JWKS{Keys: []JWK{}}
	for _, k := range m.cache {
		if k.RetiredAt != nil && !now.Before(*k.RetiredAt) {
			continue
		}
		pub, err := parsePublicPEM(k.PublicKeyPEM)
		if err != nil {
			return nil, err
		}
		out.Keys = append(out.Keys, JWK{
			Kty: "RSA", Use: "sig", Alg: k.Alg, Kid: k.KID,
			N: base64.RawURLEncoding.EncodeToString(pub.N.Bytes()),
			E: base64.RawURLEncoding.EncodeToString(big.NewInt(int64(pub.E)).Bytes()),
		})
	}
	return out, nil
}

// RetireDueKeys is called by the scheduler to refresh the cache so retired
// keys stop verifying without a restart.
func (m *KeyManager) RetireDueKeys(ctx context.Context) error { return m.refresh(ctx) }

func parsePublicPEM(p string) (*rsa.PublicKey, error) {
	block, _ := pem.Decode([]byte(p))
	if block == nil {
		return nil, errors.New("invalid public key PEM")
	}
	pub, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, err
	}
	rsaPub, ok := pub.(*rsa.PublicKey)
	if !ok {
		return nil, errors.New("not an RSA public key")
	}
	return rsaPub, nil
}
