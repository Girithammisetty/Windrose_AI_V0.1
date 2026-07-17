package api

import (
	"crypto/rand"
	"crypto/rsa"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/tool-plane/internal/authz"
)

const (
	testIssuer   = "https://identity.windrose.test"
	testAudience = "windrose"
)

// authzHarness is a registry server with a static-key verifier and a pluggable
// admin authorizer. Store stays nil: every request here must be decided by the
// middleware BEFORE any handler touches persistence (fail-closed proof).
type authzHarness struct {
	router http.Handler
	key    *rsa.PrivateKey
}

func newAuthzHarness(t *testing.T, a authz.AdminAuthorizer) *authzHarness {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	srv := &RegistryServer{
		Verifier: authjwt.NewStatic(&key.PublicKey, testIssuer, testAudience),
		Authz:    a,
	}
	return &authzHarness{router: srv.Router(), key: key}
}

func (h *authzHarness) mint(t *testing.T, c authjwt.Claims) string {
	t.Helper()
	c.Issuer = testIssuer
	c.Audience = jwt.ClaimStrings{testAudience}
	c.ExpiresAt = jwt.NewNumericDate(time.Now().Add(5 * time.Minute))
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, &c)
	s, err := tok.SignedString(h.key)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func (h *authzHarness) do(t *testing.T, token, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	var rd *strings.Reader
	if body == "" {
		rd = strings.NewReader("")
	} else {
		rd = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, rd)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.router.ServeHTTP(rec, req)
	return rec
}

const tenantA = "11111111-1111-1111-1111-111111111111"
const tenantB = "22222222-2222-2222-2222-222222222222"

// TestDeniedRequestGets403 proves fail-closed authorization: a request whose
// authorizer denies the route's action is rejected with 403 PERMISSION_DENIED
// before the handler runs (Store is nil — any handler execution would panic).
func TestDeniedRequestGets403(t *testing.T) {
	h := newAuthzHarness(t, authz.AdminStatic{Denied: map[string]bool{
		authz.ActionToolRead:   true,
		authz.ActionKillCreate: true,
	}})
	userTok := h.mint(t, authjwt.Claims{Sub: "user:u1", TenantID: tenantA, Typ: authjwt.TypUser})

	if rec := h.do(t, userTok, http.MethodGet, "/api/v1/tools", ""); rec.Code != http.StatusForbidden {
		t.Fatalf("GET /tools with denying authorizer: want 403, got %d: %s", rec.Code, rec.Body.String())
	}
	body := `{"scope":"tool","tool_id":"case.assign","reason":"INC-1"}`
	if rec := h.do(t, userTok, http.MethodPost, "/api/v1/kill-switches", body); rec.Code != http.StatusForbidden {
		t.Fatalf("POST /kill-switches with denying authorizer: want 403, got %d: %s", rec.Code, rec.Body.String())
	}
}

// TestNilAuthorizerFailsClosed proves a mis-wired (nil) authorizer denies
// everything rather than reverting to the old JWT-only behaviour (BR-1).
func TestNilAuthorizerFailsClosed(t *testing.T) {
	h := newAuthzHarness(t, nil)
	userTok := h.mint(t, authjwt.Claims{Sub: "user:u1", TenantID: tenantA, Typ: authjwt.TypUser})
	if rec := h.do(t, userTok, http.MethodGet, "/api/v1/tools", ""); rec.Code != http.StatusForbidden {
		t.Fatalf("nil authorizer: want 403, got %d", rec.Code)
	}
}

// TestAgentWildcardScopeDoesNotBypass proves an agent token cannot claim the
// operator fast path: agents always go through the authorizer, even with the
// "*" wildcard scope in their token.
func TestAgentWildcardScopeDoesNotBypass(t *testing.T) {
	h := newAuthzHarness(t, authz.AdminStatic{Denied: map[string]bool{authz.ActionToolRead: true}})
	agentTok := h.mint(t, authjwt.Claims{
		Sub: "agent:case-triage", TenantID: tenantA, Typ: authjwt.TypAgentOBO,
		AgentID: "case-triage", OboSub: "user:u1", Scopes: []string{"*"},
	})
	if rec := h.do(t, agentTok, http.MethodGet, "/api/v1/tools", ""); rec.Code != http.StatusForbidden {
		t.Fatalf("agent wildcard scope must not bypass authz: want 403, got %d", rec.Code)
	}
}

// TestCrossTenantKillRejectedForNormalCaller pins the TPL cross-tenant kill fix:
// a tenant-scoped kill whose body tenant_id differs from the caller's verified
// token tenant is rejected for any non-platform-operator caller. The kill scope
// tenant can never be chosen by the request body.
func TestCrossTenantKillRejectedForNormalCaller(t *testing.T) {
	h := newAuthzHarness(t, authz.AdminAllowAll{})
	userTok := h.mint(t, authjwt.Claims{Sub: "user:u1", TenantID: tenantA, Typ: authjwt.TypUser})
	body := `{"scope":"tool_tenant","tool_id":"case.assign","tenant_id":"` + tenantB + `","reason":"INC-1"}`
	rec := h.do(t, userTok, http.MethodPost, "/api/v1/kill-switches", body)
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("cross-tenant kill by normal caller: want 422 VALIDATION_FAILED, got %d: %s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "platform-operator only") {
		t.Fatalf("expected cross-tenant rejection message, got %s", rec.Body.String())
	}
}
