//go:build integration

// Integration tests for the rewired identity adapters against REAL local infra
// (deploy/docker-compose.dev.yml): the Vault transit signer, the Redis
// API-key denylist, and cross-service JWT verification — identity issues a
// Vault-signed token and libs/go-common's authjwt verifies it against
// identity's live JWKS. These auto-skip when the relevant infra is down.
package integration

import (
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/authjwt"

	"github.com/windrose-ai/identity-service/internal/adapters/denylist"
	"github.com/windrose-ai/identity-service/internal/adapters/vault"
	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/keys"
	"github.com/windrose-ai/identity-service/internal/store/memory"

	"github.com/windrose-ai/go-common/redisx"
)

func vaultAddr() string {
	if a := os.Getenv("VAULT_ADDR"); a != "" {
		return a
	}
	return "http://localhost:8200"
}

func vaultToken() string {
	if t := os.Getenv("VAULT_TOKEN"); t != "" {
		return t
	}
	return "windrose_dev_root"
}

func redisAddr() string {
	if a := os.Getenv("REDIS_ADDR"); a != "" {
		return a
	}
	return "localhost:6379"
}

func newVaultSigner(t *testing.T) *vault.TransitSigner {
	t.Helper()
	s, err := vault.New(vaultAddr(), vaultToken(), "")
	if err != nil {
		t.Skipf("vault unavailable at %s: %v", vaultAddr(), err)
	}
	return s
}

func TestVaultTransitSigner_GenerateAndSign(t *testing.T) {
	ctx := context.Background()
	s := newVaultSigner(t)

	kid, pubPEM, err := s.Generate(ctx)
	if err != nil {
		t.Fatalf("generate: %v", err)
	}
	if kid == "" || pubPEM == "" {
		t.Fatalf("empty kid/pem: %q %q", kid, pubPEM)
	}

	msg := []byte("header.payload")
	sig, err := s.Sign(ctx, kid, msg)
	if err != nil {
		t.Fatalf("sign: %v", err)
	}

	// Verify the signature Vault produced with the public key it returned —
	// proves RS256 (pkcs1v15/sha2-256) signing happened inside Vault.
	block, _ := pem.Decode([]byte(pubPEM))
	if block == nil {
		t.Fatal("bad public PEM")
	}
	pub, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		t.Fatalf("parse pub: %v", err)
	}
	sum := sha256.Sum256(msg)
	if err := rsa.VerifyPKCS1v15(pub.(*rsa.PublicKey), crypto.SHA256, sum[:], sig); err != nil {
		t.Fatalf("vault signature failed verification: %v", err)
	}
}

func TestRedisDenylist_RevokeRoundtrip(t *testing.T) {
	rc := redisx.New(redisAddr())
	defer rc.Close()
	if err := rc.Ping(context.Background()); err != nil {
		t.Skipf("redis unavailable at %s: %v", redisAddr(), err)
	}
	dl := &denylist.Redis{Cmd: rc, Prefix: "denylist:test:" + uuid.NewString()[:8] + ":", TTL: time.Minute}
	id := uuid.NewString()
	if dl.IsRevoked(id) {
		t.Fatal("unexpectedly revoked before Revoke")
	}
	dl.Revoke(id)
	if !dl.IsRevoked(id) {
		t.Fatal("not revoked after Revoke (Redis propagation)")
	}
}

func TestVaultSignedToken_VerifiesViaGoCommonJWKS(t *testing.T) {
	ctx := context.Background()
	signer := newVaultSigner(t)

	clock := time.Now
	store := memory.New()
	km := keys.NewKeyManager(store, signer, clock)
	if err := km.Bootstrap(ctx); err != nil {
		t.Fatalf("bootstrap: %v", err)
	}
	issuer := keys.NewIssuer(km, clock)

	tok, _, err := issuer.Issue(domain.Claims{
		Subject: "u-1", TenantID: uuid.New(), Typ: domain.TypUser,
		Scopes: []string{"identity.tenant.read"},
	})
	if err != nil {
		t.Fatalf("issue: %v", err)
	}

	// Serve identity's JWKS (Vault public keys) and verify the token with the
	// SHARED go-common verifier — the real cross-service authN path.
	jwksSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		doc, jerr := km.JWKS()
		if jerr != nil {
			http.Error(w, jerr.Error(), 500)
			return
		}
		_ = json.NewEncoder(w).Encode(doc)
	}))
	defer jwksSrv.Close()

	v := authjwt.NewJWKS(jwksSrv.URL, "https://identity.windrose.ai", "windrose")
	claims, err := v.Verify(ctx, tok)
	if err != nil {
		t.Fatalf("go-common verify of Vault-signed token failed: %v", err)
	}
	if claims.Sub != "u-1" || claims.Typ != domain.TypUser {
		t.Fatalf("claims mismatch: %+v", claims)
	}
}
