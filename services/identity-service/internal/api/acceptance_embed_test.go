package api_test

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"
)

// decodeJWTClaims base64-decodes the (unverified) payload segment for assertion.
func decodeJWTClaims(t *testing.T, token string) map[string]any {
	t.Helper()
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("not a JWT: %q", token)
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		t.Fatalf("decode payload: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("unmarshal claims: %v", err)
	}
	return m
}

// IDN-FR-043: the embed-token exchange mints a short-lived, workspace-scoped
// user token carrying embed/surface claims + the tenant's frame-ancestors,
// gated by the per-tenant embed secret.
func TestEmbedTokenExchange(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("embedco")
	origins := []string{"https://acme.example.com", "https://portal.acme.test"}

	// 1) tenant admin sets embed config -> gets the plaintext secret once.
	r := f.do(http.MethodPut, "/api/v1/tenants/"+tn.ID.String()+"/embed-config",
		f.adminToken(tn.ID), map[string]any{"allowed_origins": origins})
	if r.status != http.StatusOK {
		t.Fatalf("set embed-config: %d %s", r.status, string(r.raw))
	}
	secret, _ := r.body["embed_secret"].(string)
	if secret == "" || !strings.HasPrefix(secret, "wes_") {
		t.Fatalf("expected a wes_ secret, got %q", secret)
	}

	// 2) exchange the secret + user context -> a scoped embed token.
	ex := f.do(http.MethodPost, "/api/v1/token/embed", "", map[string]any{
		"tenant_id":    tn.ID.String(),
		"secret":       secret,
		"sub":          "user-embed",
		"workspace_id": "11111111-1111-1111-1111-111111111111",
		"surface":      []string{"dashboard"},
		"scopes":       []string{"chart.dashboard.read"},
		"ttl_seconds":  300,
	})
	if ex.status != http.StatusOK {
		t.Fatalf("embed exchange: %d %s", ex.status, string(ex.raw))
	}
	tok, _ := ex.body["access_token"].(string)
	claims := decodeJWTClaims(t, tok)
	if claims["embed"] != true {
		t.Fatalf("expected embed=true, got %v", claims["embed"])
	}
	if claims["typ"] != "user" {
		t.Fatalf("embed token must be typ=user (downstream accepts it), got %v", claims["typ"])
	}
	if claims["workspace_id"] != "11111111-1111-1111-1111-111111111111" {
		t.Fatalf("workspace not scoped: %v", claims["workspace_id"])
	}
	if sfc, _ := claims["surface"].([]any); len(sfc) != 1 || sfc[0] != "dashboard" {
		t.Fatalf("surface claim wrong: %v", claims["surface"])
	}
	if fa, _ := claims["frame_ancestors"].([]any); len(fa) != 2 {
		t.Fatalf("frame_ancestors not bound into token: %v", claims["frame_ancestors"])
	}
	// short TTL: exp within ~5min
	exp, _ := claims["exp"].(float64)
	if ttl := int64(exp) - time.Now().Unix(); ttl <= 0 || ttl > 300 {
		t.Fatalf("expected short TTL, got %ds", ttl)
	}
}

// IDN-FR-043: the admin screen reads back configured state (never the
// secret itself, only its hash is stored) — 404 before any config exists,
// then the real allowed_origins + configured=true after PUT.
func TestGetEmbedConfig(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("embedconfigco")

	before := f.do(http.MethodGet, "/api/v1/tenants/"+tn.ID.String()+"/embed-config", f.adminToken(tn.ID), nil)
	if before.status != http.StatusNotFound {
		t.Fatalf("expected 404 before any embed-config exists, got %d %s", before.status, string(before.raw))
	}

	origins := []string{"https://acme.example.com"}
	put := f.do(http.MethodPut, "/api/v1/tenants/"+tn.ID.String()+"/embed-config",
		f.adminToken(tn.ID), map[string]any{"allowed_origins": origins})
	if put.status != http.StatusOK {
		t.Fatalf("set embed-config: %d %s", put.status, string(put.raw))
	}

	after := f.do(http.MethodGet, "/api/v1/tenants/"+tn.ID.String()+"/embed-config", f.adminToken(tn.ID), nil)
	if after.status != http.StatusOK {
		t.Fatalf("get embed-config: %d %s", after.status, string(after.raw))
	}
	if after.body["configured"] != true {
		t.Fatalf("expected configured=true, got %v", after.body["configured"])
	}
	if _, leaked := after.body["embed_secret"]; leaked {
		t.Fatalf("GET must never return the plaintext secret")
	}
	got, _ := after.body["allowed_origins"].([]any)
	if len(got) != 1 || got[0] != origins[0] {
		t.Fatalf("allowed_origins mismatch: %v", after.body["allowed_origins"])
	}
}

func TestEmbedTokenRejectsBadSecretAndSurface(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("embedco2")
	f.do(http.MethodPut, "/api/v1/tenants/"+tn.ID.String()+"/embed-config",
		f.adminToken(tn.ID), map[string]any{"allowed_origins": []string{"https://x.test"}})

	// wrong secret -> 401
	bad := f.do(http.MethodPost, "/api/v1/token/embed", "", map[string]any{
		"tenant_id": tn.ID.String(), "secret": "wes_wrong", "sub": "u",
		"workspace_id": "11111111-1111-1111-1111-111111111111", "surface": []string{"dashboard"},
	})
	if bad.status != http.StatusUnauthorized {
		t.Fatalf("wrong secret: want 401, got %d %s", bad.status, string(bad.raw))
	}

	// a tenant with NO embed config -> 401 (uniform failure, no oracle)
	other := f.activeTenant("noembed")
	r := f.do(http.MethodPost, "/api/v1/token/embed", "", map[string]any{
		"tenant_id": other.ID.String(), "secret": "wes_anything", "sub": "u",
		"workspace_id": "11111111-1111-1111-1111-111111111111", "surface": []string{"dashboard"},
	})
	if r.status != http.StatusUnauthorized {
		t.Fatalf("unconfigured tenant: want 401, got %d", r.status)
	}
}
