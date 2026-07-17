package reports

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"testing"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// testRSAKeyPEM generates a throwaway RSA key for unit tests only — it is not
// the platform's real signing key (that is REGISTER_SIGNING_KEY_PEM, injected
// at runtime); this just proves MintOBO's own encoding/signing logic.
func testRSAKeyPEM(t *testing.T) string {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate test key: %v", err)
	}
	der := x509.MarshalPKCS1PrivateKey(key)
	block := &pem.Block{Type: "RSA PRIVATE KEY", Bytes: der}
	return string(pem.EncodeToMemory(block))
}

func TestTokenMinter_MintOBO_ProducesVerifiableClaims(t *testing.T) {
	pemKey := testRSAKeyPEM(t)
	m := NewTokenMinter(pemKey, "kid-1", "https://identity.windrose.ai", "windrose")
	tenant := uuid.New()
	workspace := uuid.New()

	tok, err := m.MintOBO(tenant, workspace, "manager@demo.windrose")
	if err != nil {
		t.Fatalf("MintOBO: %v", err)
	}

	block, _ := pem.Decode([]byte(pemKey))
	priv, err := x509.ParsePKCS1PrivateKey(block.Bytes)
	if err != nil {
		t.Fatalf("parse test key: %v", err)
	}
	claims := jwt.MapClaims{}
	parsed, err := jwt.ParseWithClaims(tok, claims, func(*jwt.Token) (any, error) { return &priv.PublicKey, nil })
	if err != nil || !parsed.Valid {
		t.Fatalf("minted token does not verify against its own key: %v", err)
	}
	if claims["typ"] != "agent_obo" {
		t.Errorf("typ = %v, want agent_obo", claims["typ"])
	}
	if claims["obo_sub"] != "manager@demo.windrose" {
		t.Errorf("obo_sub = %v, want manager@demo.windrose", claims["obo_sub"])
	}
	if claims["tenant_id"] != tenant.String() {
		t.Errorf("tenant_id = %v, want %s", claims["tenant_id"], tenant.String())
	}
	if claims["workspace_id"] != workspace.String() {
		t.Errorf("workspace_id = %v, want %s", claims["workspace_id"], workspace.String())
	}
}

func TestTokenMinter_MintOBO_FailsWithoutKey(t *testing.T) {
	m := &TokenMinter{}
	if _, err := m.MintOBO(uuid.New(), uuid.New(), "u"); err == nil {
		t.Fatal("expected an error when no signing key is configured, not a silent fake token")
	}
}
