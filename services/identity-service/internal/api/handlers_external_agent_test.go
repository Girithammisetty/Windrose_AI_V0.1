package api_test

import (
	"net/http"
	"strings"
	"testing"
)

// BRD 60 WS2: self-service external-agent credentials. An admin mints a
// per-agent key; a customer's own agent exchanges it (unauth, the key IS the
// credential) for a short-lived agent_autonomous token. These tests exercise
// the full mint → exchange → revoke lifecycle plus the scope + tenant walls.

// mintExternalAgentKey creates a credential and returns the shown-once plaintext.
func mintExternalAgentKey(t *testing.T, f *fixture, adminTok string, agentID string, scopes []string) string {
	t.Helper()
	r := f.do(http.MethodPost, "/api/v1/tenants/self/external-agents", adminTok,
		map[string]any{"agent_id": agentID, "agent_version": 1, "scopes": scopes, "label": "acme bot"})
	if r.status != http.StatusCreated {
		t.Fatalf("mint key: want 201, got %d %s", r.status, string(r.raw))
	}
	if r.body["shown_once"] != true {
		t.Fatalf("mint response missing shown_once: %v", r.body)
	}
	pt, _ := r.body["plaintext"].(string)
	if !strings.HasPrefix(pt, "wr_xa_") {
		t.Fatalf("plaintext key should be a wr_xa_ credential, got %q", pt)
	}
	return pt
}

// TestExternalAgentKey_MintRequiresAdminScope: a zero-scope member cannot mint
// an external-agent credential; a tenant admin can, and the plaintext is shown
// exactly once.
func TestExternalAgentKey_MintRequiresAdminScope(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("xa-scope")
	u := f.activeUser(tn, "member@xa-scope.com")

	r := f.do(http.MethodPost, "/api/v1/tenants/self/external-agents", f.userToken(u),
		map[string]any{"agent_id": "acme-bot", "agent_version": 1})
	if r.status != http.StatusForbidden {
		t.Fatalf("zero-scope mint: want 403, got %d %s", r.status, string(r.raw))
	}

	pt := mintExternalAgentKey(t, f, f.adminToken(tn.ID), "acme-bot", []string{"case.case.read"})
	if pt == "" {
		t.Fatal("empty plaintext key")
	}
}

// TestExternalAgentKey_ExchangeMintsAgentToken: presenting a valid key (no
// bearer) yields a short-lived Bearer access token the customer's agent uses.
func TestExternalAgentKey_ExchangeMintsAgentToken(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("xa-exchange")
	pt := mintExternalAgentKey(t, f, f.adminToken(tn.ID), "acme-bot", []string{"case.case.read"})

	// Exchange is unauthenticated: the key itself carries the auth.
	r := f.do(http.MethodPost, "/api/v1/token/agent/external", "", map[string]any{"api_key": pt})
	if r.status != http.StatusOK {
		t.Fatalf("exchange: want 200, got %d %s", r.status, string(r.raw))
	}
	if tok, _ := r.body["access_token"].(string); tok == "" {
		t.Fatalf("exchange returned no access_token: %v", r.body)
	}
	if r.body["token_type"] != "Bearer" {
		t.Fatalf("want Bearer token_type, got %v", r.body["token_type"])
	}
}

// TestExternalAgentKey_MalformedAndUnknownRejected: a garbage key and a
// well-formed-but-unknown key both fail closed with 401 (never 500 / never a
// token).
func TestExternalAgentKey_MalformedAndUnknownRejected(t *testing.T) {
	f := newFixture(t)
	_ = f.activeTenant("xa-reject")
	for _, k := range []string{"", "not-a-key", "wr_sa_deadbeef.secret", "wr_xa_not-a-uuid.secret"} {
		r := f.do(http.MethodPost, "/api/v1/token/agent/external", "", map[string]any{"api_key": k})
		if r.status != http.StatusUnauthorized {
			t.Fatalf("key %q: want 401, got %d %s", k, r.status, string(r.raw))
		}
	}
}

// TestExternalAgentKey_RevokeThenExchangeFails: once an admin revokes the key,
// exchanging it fails closed — the customer's agent can no longer mint tokens.
func TestExternalAgentKey_RevokeThenExchangeFails(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("xa-revoke")
	adminTok := f.adminToken(tn.ID)
	pt := mintExternalAgentKey(t, f, adminTok, "acme-bot", []string{"case.case.read"})

	// It works before revocation.
	if r := f.do(http.MethodPost, "/api/v1/token/agent/external", "", map[string]any{"api_key": pt}); r.status != http.StatusOK {
		t.Fatalf("pre-revoke exchange: want 200, got %d %s", r.status, string(r.raw))
	}

	// Find the key id from the admin listing, then revoke it.
	list := f.do(http.MethodGet, "/api/v1/tenants/self/external-agents", adminTok, nil)
	if list.status != http.StatusOK {
		t.Fatalf("list: want 200, got %d %s", list.status, string(list.raw))
	}
	keys, _ := list.body["keys"].([]any)
	if len(keys) != 1 {
		t.Fatalf("want 1 key, got %d (%v)", len(keys), list.body)
	}
	km, _ := keys[0].(map[string]any)
	if _, leaked := km["secret_hash"]; leaked {
		t.Fatal("secret_hash must never be serialized in the listing")
	}
	id, _ := km["id"].(string)

	if r := f.do(http.MethodDelete, "/api/v1/tenants/self/external-agents/"+id, adminTok, nil); r.status != http.StatusNoContent {
		t.Fatalf("revoke: want 204, got %d %s", r.status, string(r.raw))
	}

	// After revocation the exact same key fails closed.
	if r := f.do(http.MethodPost, "/api/v1/token/agent/external", "", map[string]any{"api_key": pt}); r.status != http.StatusUnauthorized {
		t.Fatalf("post-revoke exchange: want 401, got %d %s", r.status, string(r.raw))
	}
}

// TestExternalAgentKey_CrossTenantRevokeIsolation: an admin of tenant B cannot
// revoke tenant A's credential (tenant-scoped), and A's key keeps working.
func TestExternalAgentKey_CrossTenantRevokeIsolation(t *testing.T) {
	f := newFixture(t)
	tnA := f.activeTenant("xa-iso-a")
	tnB := f.activeTenant("xa-iso-b")
	ptA := mintExternalAgentKey(t, f, f.adminToken(tnA.ID), "acme-bot", []string{"case.case.read"})

	// Discover A's key id (as A's admin), then try to revoke it as B's admin.
	list := f.do(http.MethodGet, "/api/v1/tenants/self/external-agents", f.adminToken(tnA.ID), nil)
	keys, _ := list.body["keys"].([]any)
	km, _ := keys[0].(map[string]any)
	id, _ := km["id"].(string)

	if r := f.do(http.MethodDelete, "/api/v1/tenants/self/external-agents/"+id, f.adminToken(tnB.ID), nil); r.status != http.StatusNotFound {
		t.Fatalf("cross-tenant revoke: want 404, got %d %s", r.status, string(r.raw))
	}

	// A's key still exchanges successfully — B's attempt did not touch it.
	if r := f.do(http.MethodPost, "/api/v1/token/agent/external", "", map[string]any{"api_key": ptA}); r.status != http.StatusOK {
		t.Fatalf("A's key after cross-tenant revoke attempt: want 200, got %d %s", r.status, string(r.raw))
	}
}
