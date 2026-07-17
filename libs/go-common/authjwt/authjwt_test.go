package authjwt

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

func signToken(t *testing.T, key *rsa.PrivateKey, kid string, mut func(jwt.MapClaims)) string {
	t.Helper()
	now := time.Now()
	claims := jwt.MapClaims{
		"sub": "u-1", "tenant_id": uuid.New().String(), "typ": "user",
		"iss": "https://identity.windrose.ai", "aud": "windrose",
		"exp": now.Add(5 * time.Minute).Unix(), "iat": now.Unix(), "nbf": now.Unix(),
		"scopes": []string{"dataset.dataset.read"},
	}
	if mut != nil {
		mut(claims)
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	tok.Header["kid"] = kid
	s, err := tok.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func jwksServer(t *testing.T, kid string, pub *rsa.PublicKey) *httptest.Server {
	t.Helper()
	doc := map[string]any{"keys": []map[string]string{{
		"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
		"n": base64.RawURLEncoding.EncodeToString(pub.N.Bytes()),
		"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(pub.E)).Bytes()),
	}}}
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(doc)
	}))
}

func TestVerifyViaJWKS(t *testing.T) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	kid := "kid-1"
	srv := jwksServer(t, kid, &key.PublicKey)
	defer srv.Close()

	v := NewJWKS(srv.URL, "https://identity.windrose.ai", "windrose")
	tok := signToken(t, key, kid, nil)
	claims, err := v.Verify(context.Background(), tok)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if claims.Sub != "u-1" || claims.Typ != "user" || !claims.HasScope("dataset.dataset.read") {
		t.Fatalf("claims=%+v", claims)
	}
}

func TestRejectAlgNone(t *testing.T) {
	// alg=none token; must be rejected by WithValidMethods (MASTER-FR-014).
	tok := jwt.NewWithClaims(jwt.SigningMethodNone, jwt.MapClaims{
		"sub": "u", "tenant_id": uuid.New().String(), "exp": time.Now().Add(time.Hour).Unix(),
	})
	s, _ := tok.SignedString(jwt.UnsafeAllowNoneSignatureType)
	v := NewStatic(nil, "", "")
	if _, err := v.Verify(context.Background(), s); err == nil {
		t.Fatal("alg=none accepted — must be rejected")
	}
}

func TestRejectWrongIssuer(t *testing.T) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	v := NewStatic(&key.PublicKey, "https://identity.windrose.ai", "windrose")
	tok := signToken(t, key, "kid-1", func(c jwt.MapClaims) { c["iss"] = "https://evil" })
	if _, err := v.Verify(context.Background(), tok); err == nil {
		t.Fatal("wrong issuer accepted")
	}
}

func TestMiddlewarePopulatesClaims(t *testing.T) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	v := NewStatic(&key.PublicKey, "https://identity.windrose.ai", "windrose")
	var got *Claims
	h := v.Middleware(nil)(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		got, _ = FromContext(r.Context())
	}))
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("Authorization", "Bearer "+signToken(t, key, "kid-1", nil))
	h.ServeHTTP(httptest.NewRecorder(), req)
	if got == nil || got.Sub != "u-1" {
		t.Fatalf("claims not populated: %+v", got)
	}

	rec := httptest.NewRecorder()
	v.Middleware(nil)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {})).
		ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("missing token should 401, got %d", rec.Code)
	}
}
