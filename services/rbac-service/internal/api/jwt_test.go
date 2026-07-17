package api

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/rbac-service/internal/domain"
)

func testKey(t *testing.T) *rsa.PrivateKey {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	require.NoError(t, err)
	return key
}

func baseClaims() jwt.MapClaims {
	return jwt.MapClaims{
		"sub":       "u-1",
		"tenant_id": "3d9b6dcb-3e50-4a49-9a3c-3a2d3ff2a001",
		"typ":       "user",
		"iss":       "windrose-test",
		"aud":       "windrose",
		"exp":       time.Now().Add(time.Minute).Unix(),
	}
}

func TestVerifier_ValidToken(t *testing.T) {
	key := testKey(t)
	v := NewVerifierStatic(&key.PublicKey, "windrose-test", "windrose")
	claims := baseClaims()
	claims["scopes"] = []string{"super_admin"}
	tok, err := jwt.NewWithClaims(jwt.SigningMethodRS256, claims).SignedString(key)
	require.NoError(t, err)

	got, err := v.Verify(context.Background(), tok)
	require.NoError(t, err)
	assert.Equal(t, "u-1", got.Sub)
	assert.Equal(t, domain.TypUser, got.Typ)
	assert.True(t, got.HasScope(ScopeSuperAdmin))
	tenant, err := got.Tenant()
	require.NoError(t, err)
	assert.Equal(t, "3d9b6dcb-3e50-4a49-9a3c-3a2d3ff2a001", tenant.String())
}

// MASTER-FR-014: alg=none is forbidden; only RS256 is accepted.
func TestVerifier_RejectsAlgNoneAndHMAC(t *testing.T) {
	key := testKey(t)
	v := NewVerifierStatic(&key.PublicKey, "", "")

	noneTok, err := jwt.NewWithClaims(jwt.SigningMethodNone, baseClaims()).SignedString(jwt.UnsafeAllowNoneSignatureType)
	require.NoError(t, err)
	_, err = v.Verify(context.Background(), noneTok)
	assert.Error(t, err, "alg=none must be rejected")

	hmacTok, err := jwt.NewWithClaims(jwt.SigningMethodHS256, baseClaims()).SignedString([]byte("secret"))
	require.NoError(t, err)
	_, err = v.Verify(context.Background(), hmacTok)
	assert.Error(t, err, "HS256 must be rejected")
}

func TestVerifier_RejectsExpiredWrongIssuerWrongKey(t *testing.T) {
	key := testKey(t)
	v := NewVerifierStatic(&key.PublicKey, "windrose-test", "windrose")

	expired := baseClaims()
	expired["exp"] = time.Now().Add(-time.Minute).Unix()
	tok, _ := jwt.NewWithClaims(jwt.SigningMethodRS256, expired).SignedString(key)
	_, err := v.Verify(context.Background(), tok)
	assert.Error(t, err, "expired token")

	wrongIss := baseClaims()
	wrongIss["iss"] = "evil"
	tok, _ = jwt.NewWithClaims(jwt.SigningMethodRS256, wrongIss).SignedString(key)
	_, err = v.Verify(context.Background(), tok)
	assert.Error(t, err, "wrong issuer")

	otherKey := testKey(t)
	tok, _ = jwt.NewWithClaims(jwt.SigningMethodRS256, baseClaims()).SignedString(otherKey)
	_, err = v.Verify(context.Background(), tok)
	assert.Error(t, err, "wrong signing key")

	noTenant := baseClaims()
	delete(noTenant, "tenant_id")
	tok, _ = jwt.NewWithClaims(jwt.SigningMethodRS256, noTenant).SignedString(key)
	_, err = v.Verify(context.Background(), tok)
	assert.Error(t, err, "missing tenant claim")
}

func TestClaims_EffectiveUserOBO(t *testing.T) {
	c := &Claims{Sub: "agent-1", Typ: domain.TypAgentOBO, OboSub: "u-9"}
	assert.Equal(t, "u-9", c.EffectiveUser(), "OBO resolves to the original user")
	c2 := &Claims{Sub: "u-1", Typ: domain.TypUser}
	assert.Equal(t, "u-1", c2.EffectiveUser())
}
