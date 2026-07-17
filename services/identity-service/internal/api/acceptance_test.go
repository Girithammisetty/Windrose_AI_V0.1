// Acceptance tests for BRD 01 §10 (AC-1..AC-14), unit tier: httptest server
// over the in-memory store with fake adapters. Where an AC needs unavailable
// infra the test documents the delta inline.
package api_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// AC-1: valid tenant creation with publish=true -> active, all 7 steps
// succeeded, synthetic login (Verify probe) succeeded.
func TestAC01_ProvisioningHappyPath(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("acme")
	r := f.do(http.MethodGet, "/api/v1/tenants/"+tn.ID.String()+"/provisioning", f.superToken(), nil)
	if r.status != http.StatusOK {
		t.Fatalf("provisioning status: %d", r.status)
	}
	steps := r.body["steps"].([]any)
	if len(steps) != 7 {
		t.Fatalf("expected 7 steps, got %d", len(steps))
	}
	for _, s := range steps {
		st := s.(map[string]any)
		if st["status"] != "succeeded" {
			t.Errorf("step %v: status %v", st["step_name"], st["status"])
		}
	}
	if f.prober.Calls == 0 {
		t.Error("synthetic login (Verify probe) never ran")
	}
}

// AC-2: ProvisionInfra fails 5 times -> provision_failed, steps 1-2 remain
// recorded, no unmanaged infra (compensation log verified).
func TestAC02_ProvisionFailureAfterRetries(t *testing.T) {
	f := newFixture(t)
	f.tf.FailApplyAlways = errors.New("cloud unavailable")
	r := f.createTenant("failco", true)
	if r.status != http.StatusAccepted {
		t.Fatalf("create: %d %s", r.status, string(r.raw))
	}
	tn, _ := f.store.GetTenantByName(context.Background(), "failco")
	if tn.Status != domain.TenantProvisionFailed {
		t.Fatalf("status = %s, want provision_failed", tn.Status)
	}
	if f.tf.ApplyCalls != 5 {
		t.Fatalf("expected 5 retry attempts, got %d", f.tf.ApplyCalls)
	}
	steps, _ := f.store.ListProvisioningSteps(context.Background(), tn.ID, domain.WorkflowIDFor(tn.ID))
	if len(steps) != 3 {
		t.Fatalf("expected 3 step records (1-2 succeeded + 3 failed), got %d", len(steps))
	}
	for _, s := range steps[:2] {
		if s.Status != domain.StepSucceeded || s.CompensationName == "" {
			t.Errorf("step %s: want succeeded with recorded compensation path, got %+v", s.StepName, s)
		}
	}
	if steps[2].Status != domain.StepFailed || steps[2].Attempt != 5 {
		t.Errorf("step 3: %+v", steps[2])
	}
	if f.tf.HasInfra("failco") {
		t.Error("unmanaged infra exists after failed provisioning")
	}
}

// AC-3: retry resumes at step 3; steps 1-2 are not re-executed.
func TestAC03_RetryResumesFromFailedStep(t *testing.T) {
	f := newFixture(t)
	f.tf.ApplyErrs = []error{
		errors.New("e1"), errors.New("e2"), errors.New("e3"), errors.New("e4"), errors.New("e5"),
	}
	f.createTenant("retryco", true)
	tn, _ := f.store.GetTenantByName(context.Background(), "retryco")
	if tn.Status != domain.TenantProvisionFailed {
		t.Fatalf("precondition: status %s", tn.Status)
	}
	realmCalls := f.kc.Calls["CreateRealm"]
	r := f.do(http.MethodPost, "/api/v1/tenants/"+tn.ID.String()+"/provisioning/retry", f.superToken(), nil)
	if r.status != http.StatusAccepted {
		t.Fatalf("retry: %d %s", r.status, string(r.raw))
	}
	tn, _ = f.store.GetTenantByName(context.Background(), "retryco")
	if tn.Status != domain.TenantActive {
		t.Fatalf("after retry: status %s, want active", tn.Status)
	}
	if f.kc.Calls["CreateRealm"] != realmCalls {
		t.Errorf("step 2 re-executed on retry (idempotency markers violated)")
	}
	cells, _ := f.store.ListCells(context.Background())
	if cells[0].TenantCount != 1 {
		t.Errorf("step 1 re-executed: cell count %d, want 1", cells[0].TenantCount)
	}
	if f.tf.ApplyCalls != 6 { // 5 failures + 1 success
		t.Errorf("ApplyCalls = %d, want 6", f.tf.ApplyCalls)
	}
}

// AC-4: duplicate tenant name differing only by case -> 422, no records.
func TestAC04_DuplicateNameCaseInsensitive(t *testing.T) {
	f := newFixture(t)
	if r := f.createTenant("acme-corp", false); r.status != http.StatusCreated {
		t.Fatalf("first create: %d", r.status)
	}
	r := f.do(http.MethodPost, "/api/v1/tenants", f.superToken(), map[string]any{
		"name": "ACME-CORP", "owner_email": "o@x.com", "tier": "pool", "cloud": "aws",
	})
	if r.status != 422 || r.errCode(t) != domain.CodeValidationFailed {
		t.Fatalf("want 422 VALIDATION_FAILED, got %d %s", r.status, string(r.raw))
	}
	list := f.do(http.MethodGet, "/api/v1/tenants", f.superToken(), nil)
	if n := len(list.body["data"].([]any)); n != 1 {
		t.Fatalf("expected exactly 1 tenant record, got %d", n)
	}
}

// AC-5: expired invitation -> 410 with resend hint; re-invite invalidates
// the old token.
func TestAC05_ExpiredInvitationAndResend(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("inviteco")
	admin := f.adminToken(tn.ID)
	r := f.do(http.MethodPost, "/api/v1/users/invite", admin, map[string]any{"email": "new@inviteco.com"})
	if r.status != http.StatusCreated {
		t.Fatalf("invite: %d %s", r.status, string(r.raw))
	}
	userID := r.body["id"].(string)
	oldTok := f.lastActivationToken("new@inviteco.com")

	f.clock.Advance(domain.InvitationTTL + time.Hour)
	r = f.do(http.MethodPost, "/api/v1/invitations/"+oldTok+"/accept", "", nil)
	if r.status != http.StatusGone {
		t.Fatalf("expired accept: want 410, got %d %s", r.status, string(r.raw))
	}
	det := r.body["error"].(map[string]any)["details"].(map[string]any)
	if det["hint"] == nil {
		t.Error("410 response missing resend hint")
	}

	// Mint a fresh admin token — the earlier one has passed its 5-min TTL
	// after the clock advance.
	r = f.do(http.MethodPost, "/api/v1/users/"+userID+"/invite/resend", f.adminToken(tn.ID), nil)
	if r.status != http.StatusOK {
		t.Fatalf("resend: %d %s", r.status, string(r.raw))
	}
	newTok := f.lastActivationToken("new@inviteco.com")
	if newTok == oldTok {
		t.Fatal("resend did not issue a new token")
	}
	// The old token is invalidated (404, not 410 — it no longer exists).
	if r = f.do(http.MethodPost, "/api/v1/invitations/"+oldTok+"/accept", "", nil); r.status != http.StatusNotFound {
		t.Fatalf("old token after resend: want 404, got %d", r.status)
	}
	if r = f.do(http.MethodPost, "/api/v1/invitations/"+newTok+"/accept", "", nil); r.status != http.StatusOK {
		t.Fatalf("new token accept: want 200, got %d %s", r.status, string(r.raw))
	}
}

// AC-6: deactivated user -> OBO exchange refused with 403 PERMISSION_DENIED
// (immediately — well inside the 5-min bound).
func TestAC06_DeactivatedUserOBORefused(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("oboco")
	u := f.activeUser(tn, "worker@oboco.com")
	f.enableAgent(tn, "analytics", "v14", false)
	userTok := f.userToken(u)

	if r := f.oboExchange(userTok, "analytics", "v14"); r.status != http.StatusOK {
		t.Fatalf("precondition OBO: %d %s", r.status, string(r.raw))
	}
	r := f.do(http.MethodPost, "/api/v1/users/"+u.ID.String()+"/deactivate", f.adminToken(tn.ID), nil)
	if r.status != http.StatusOK {
		t.Fatalf("deactivate: %d %s", r.status, string(r.raw))
	}
	r = f.oboExchange(userTok, "analytics", "v14")
	if r.status != http.StatusForbidden || r.errCode(t) != domain.CodePermissionDenied {
		t.Fatalf("want 403 PERMISSION_DENIED, got %d %s", r.status, string(r.raw))
	}
}

// AC-7: agent kill-switch event -> OBO refused with 403 AGENT_DISABLED
// (immediately on next issuance — inside the 5-s bound).
func TestAC07_KillSwitchDisablesOBO(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("killco")
	u := f.activeUser(tn, "worker@killco.com")
	f.enableAgent(tn, "analytics", "v14", false)
	userTok := f.userToken(u)
	if r := f.oboExchange(userTok, "analytics", "v14"); r.status != http.StatusOK {
		t.Fatalf("precondition OBO: %d", r.status)
	}
	if err := f.tokens.ApplyAgentEvent(context.Background(), domain.AgentRegistryEvent{
		EventType: "agent_version.killed", TenantID: tn.ID, AgentID: "analytics", AgentVersion: "v14",
	}); err != nil {
		t.Fatal(err)
	}
	r := f.oboExchange(userTok, "analytics", "v14")
	if r.status != http.StatusForbidden || r.errCode(t) != domain.CodeAgentDisabled {
		t.Fatalf("want 403 AGENT_DISABLED, got %d %s", r.status, string(r.raw))
	}
	// Eval-gate failure refuses the same way (IDN-FR-043).
	f.enableAgent(tn, "analytics", "v15", false)
	if err := f.tokens.ApplyAgentEvent(context.Background(), domain.AgentRegistryEvent{
		EventType: "agent_version.eval_gate_changed", TenantID: tn.ID, AgentID: "analytics", AgentVersion: "v15", EvalGateOK: false,
	}); err != nil {
		t.Fatal(err)
	}
	if r := f.oboExchange(userTok, "analytics", "v15"); r.errCode(t) != domain.CodeAgentDisabled {
		t.Fatalf("eval gate: want AGENT_DISABLED, got %s", string(r.raw))
	}
}

// AC-8: signing-key rotation — old-key tokens verify during the overlap
// window; after retirement the old kid fails verification and leaves JWKS.
func TestAC08_KeyRotationOverlap(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("rotco")
	admin := f.adminToken(tn.ID) // signed with the pre-rotation key
	if r := f.do(http.MethodGet, "/api/v1/users", admin, nil); r.status != http.StatusOK {
		t.Fatalf("precondition list users: %d", r.status)
	}
	oldKey, _ := f.km.SigningKey()

	r := f.do(http.MethodPost, "/api/v1/keys/rotate", f.superToken(), nil)
	if r.status != http.StatusOK {
		t.Fatalf("rotate: %d %s", r.status, string(r.raw))
	}
	newKid := r.body["kid"].(string)

	// Overlap window: old-key token still works.
	if r := f.do(http.MethodGet, "/api/v1/users", admin, nil); r.status != http.StatusOK {
		t.Fatalf("old-key token rejected during overlap: %d", r.status)
	}
	// JWKS carries both keys (new one published >=10 min before use).
	jr := f.do(http.MethodGet, "/.well-known/jwks.json", "", nil)
	if jr.headers.Get("Cache-Control") != "max-age=300" {
		t.Errorf("JWKS Cache-Control = %q", jr.headers.Get("Cache-Control"))
	}
	if n := len(jr.body["keys"].([]any)); n != 2 {
		t.Fatalf("JWKS keys during overlap = %d, want 2", n)
	}

	// After retirement (not_before + TTL + skew), old-key tokens fail with 401.
	f.clock.Advance(11*time.Minute + domain.TokenTTL + domain.ClockSkew + time.Minute)
	if r := f.do(http.MethodGet, "/api/v1/users", admin, nil); r.status != http.StatusUnauthorized {
		t.Fatalf("old-key token after retirement: want 401, got %d", r.status)
	}
	if _, err := f.km.VerificationKey(oldKey.KID); err == nil {
		t.Error("retired kid still resolvable for verification")
	}
	jr = f.do(http.MethodGet, "/.well-known/jwks.json", "", nil)
	ks := jr.body["keys"].([]any)
	if len(ks) != 1 || ks[0].(map[string]any)["kid"] != newKid {
		t.Errorf("JWKS after retirement should carry only %s", newKid)
	}
	// Fresh tokens signed by the new key still work.
	if r := f.do(http.MethodGet, "/api/v1/users", f.adminToken(tn.ID), nil); r.status != http.StatusOK {
		t.Fatalf("new-key token rejected: %d", r.status)
	}
}

// AC-9: mode=destroy with failing Terraform destroy -> tenant stays
// `deleting`, never reported `deleted` (BR-6).
func TestAC09_DestroyNeverCompletesWithoutTerraform(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("delco")
	f.tf.FailDestroyAlways = errors.New("destroy stuck")
	r := f.do(http.MethodDelete, "/api/v1/tenants/"+tn.ID.String()+"?mode=destroy&force=true", f.superToken(), nil)
	if r.status != http.StatusOK {
		t.Fatalf("delete: %d %s", r.status, string(r.raw))
	}
	got, _ := f.store.GetTenant(context.Background(), tn.ID)
	if got.Status != domain.TenantDeleting || got.DeletedAt != nil {
		t.Fatalf("AC-9 violated: status=%s deleted_at=%v", got.Status, got.DeletedAt)
	}
	// Once Terraform destroy succeeds, the retry completes the deletion.
	f.tf.FailDestroyAlways = nil
	if err := f.engine.Deprovision(context.Background(), tn.ID); err != nil {
		t.Fatalf("retry destroy: %v", err)
	}
	got, _ = f.store.GetTenant(context.Background(), tn.ID)
	if got.Status != domain.TenantDeleted {
		t.Fatalf("after successful destroy: %s", got.Status)
	}
}

// AC-10: suspended tenant -> user/API-key token issuance refused with
// 403 TENANT_SUSPENDED and an audit event exists.
func TestAC10_SuspendedTenantBlocksIssuance(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("suspco")
	u := f.activeUser(tn, "worker@suspco.com")
	f.enableAgent(tn, "analytics", "v14", false)
	userTok := f.userToken(u)
	// Create an API key while active.
	r := f.do(http.MethodPost, "/api/v1/service-accounts", f.adminToken(tn.ID), map[string]any{
		"name": "ci", "scopes": []string{"dataset.dataset.read"},
	})
	if r.status != http.StatusCreated {
		t.Fatalf("create SA: %d %s", r.status, string(r.raw))
	}
	apiKey := r.body["api_key"].(string)

	if r := f.do(http.MethodPost, "/api/v1/tenants/"+tn.ID.String()+"/suspend", f.superToken(), nil); r.status != http.StatusOK {
		t.Fatalf("suspend: %d", r.status)
	}
	// OBO refused.
	r = f.oboExchange(userTok, "analytics", "v14")
	if r.status != http.StatusForbidden || r.errCode(t) != domain.CodeTenantSuspended {
		t.Fatalf("OBO: want 403 TENANT_SUSPENDED, got %d %s", r.status, string(r.raw))
	}
	// API-key exchange refused + audited.
	r = f.do(http.MethodPost, "/api/v1/token/apikey", "", map[string]any{"api_key": apiKey})
	if r.status != http.StatusForbidden || r.errCode(t) != domain.CodeTenantSuspended {
		t.Fatalf("apikey: want 403 TENANT_SUSPENDED, got %d %s", r.status, string(r.raw))
	}
	if len(f.store.EventsOfType("security.suspended_tenant_denied")) == 0 {
		t.Error("no audit event for suspended-tenant denial")
	}
	// Reactivation restores issuance (BR-5: verify probe, no re-provisioning).
	provisions := f.tf.ApplyCalls
	if r := f.do(http.MethodPost, "/api/v1/tenants/"+tn.ID.String()+"/reactivate", f.superToken(), nil); r.status != http.StatusOK {
		t.Fatalf("reactivate: %d", r.status)
	}
	if f.tf.ApplyCalls != provisions {
		t.Error("reactivation re-ran provisioning (BR-5)")
	}
	if r := f.oboExchange(userTok, "analytics", "v14"); r.status != http.StatusOK {
		t.Fatalf("OBO after reactivate: %d", r.status)
	}
}

// AC-11: revoked API key rejected at the edge immediately (denylist; the
// <=5s bound is Redis propagation in production — in-memory is instant).
func TestAC11_RevokedAPIKeyRejected(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("revco")
	admin := f.adminToken(tn.ID)
	r := f.do(http.MethodPost, "/api/v1/service-accounts", admin, map[string]any{
		"name": "ci", "scopes": []string{"dataset.dataset.read"},
	})
	apiKey := r.body["api_key"].(string)
	saID := r.body["service_account"].(map[string]any)["id"].(string)
	if r := f.do(http.MethodPost, "/api/v1/token/apikey", "", map[string]any{"api_key": apiKey}); r.status != http.StatusOK {
		t.Fatalf("exchange before revoke: %d %s", r.status, string(r.raw))
	}
	if r := f.do(http.MethodDelete, "/api/v1/service-accounts/"+saID, admin, nil); r.status != http.StatusNoContent {
		t.Fatalf("revoke: %d", r.status)
	}
	if !f.deny.IsRevoked(saID) {
		t.Error("denylist not updated on revocation")
	}
	r = f.do(http.MethodPost, "/api/v1/token/apikey", "", map[string]any{"api_key": apiKey})
	if r.status != http.StatusUnauthorized {
		t.Fatalf("exchange after revoke: want 401, got %d", r.status)
	}
}

// AC-12: tenant A's admin calling GET /tenants/{B} -> 404 +
// security.cross_tenant_denied audit event (MASTER-FR-003).
func TestAC12_CrossTenantTenantReadIs404(t *testing.T) {
	f := newFixture(t)
	a := f.activeTenant("tenant-a")
	b := f.activeTenant("tenant-b")
	r := f.do(http.MethodGet, "/api/v1/tenants/"+b.ID.String(), f.adminToken(a.ID), nil)
	if r.status != http.StatusNotFound || r.errCode(t) != domain.CodeNotFound {
		t.Fatalf("want 404 NOT_FOUND, got %d %s", r.status, string(r.raw))
	}
	evs := f.store.EventsOfType(domain.EvCrossTenantDenied)
	if len(evs) == 0 {
		t.Fatal("no security.cross_tenant_denied audit event")
	}
	if evs[0].TenantID != a.ID {
		t.Errorf("audit event tenant = %s, want caller tenant %s", evs[0].TenantID, a.ID)
	}
	// Own tenant still readable.
	if r := f.do(http.MethodGet, "/api/v1/tenants/"+a.ID.String(), f.adminToken(a.ID), nil); r.status != http.StatusOK {
		t.Fatalf("own tenant read: %d", r.status)
	}
}

// AC-13: alg=none (and other unsigned/downgraded) tokens -> 401 everywhere.
func TestAC13_AlgNoneRejected(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("nonesec")
	// {"alg":"none","typ":"JWT"} . {"sub":"u","typ":"user","tenant_id":...} . <empty>
	none := "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJ1IiwidHlwIjoidXNlciJ9."
	for _, path := range []string{"/api/v1/users", "/api/v1/tenants", "/api/v1/credentials"} {
		if r := f.do(http.MethodGet, path, none, nil); r.status != http.StatusUnauthorized {
			t.Errorf("%s with alg=none: want 401, got %d", path, r.status)
		}
	}
	// alg=none as OBO subject token -> 401 (never 5xx, never issued).
	r := f.oboExchange(none, "analytics", "v14")
	if r.status != http.StatusUnauthorized {
		t.Errorf("OBO with alg=none subject: want 401, got %d", r.status)
	}
	_ = tn
}

// AC-14: 61st OBO exchange within a minute for one (user, agent) -> 429
// RATE_LIMITED with Retry-After.
func TestAC14_OBORateLimit(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("rateco")
	u := f.activeUser(tn, "worker@rateco.com")
	f.enableAgent(tn, "analytics", "v14", false)
	userTok := f.userToken(u)
	for i := 0; i < domain.OBORateLimit; i++ {
		if r := f.oboExchange(userTok, "analytics", "v14"); r.status != http.StatusOK {
			t.Fatalf("exchange %d: %d %s", i+1, r.status, string(r.raw))
		}
	}
	r := f.oboExchange(userTok, "analytics", "v14")
	if r.status != http.StatusTooManyRequests || r.errCode(t) != domain.CodeRateLimited {
		t.Fatalf("61st: want 429 RATE_LIMITED, got %d %s", r.status, string(r.raw))
	}
	if r.headers.Get("Retry-After") == "" {
		t.Error("429 missing Retry-After header")
	}
	// A different agent for the same user is not limited.
	f.enableAgent(tn, "other", "v1", false)
	if r := f.oboExchange(userTok, "other", "v1"); r.status != http.StatusOK {
		t.Fatalf("other agent limited: %d", r.status)
	}
	// After the window, the pair is allowed again.
	f.clock.Advance(61 * time.Second)
	if r := f.oboExchange(userTok, "analytics", "v14"); r.status != http.StatusOK {
		t.Fatalf("post-window exchange: %d", r.status)
	}
}

// TestOBOTokenClaims validates the issued OBO JWT claim set (IDN-FR-041,
// MASTER-FR-011).
func TestOBOTokenClaims(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("claimco")
	u := f.activeUser(tn, "worker@claimco.com")
	f.enableAgent(tn, "analytics", "v14", false)
	r := f.oboExchange(f.userToken(u), "analytics", "v14")
	if r.status != http.StatusOK {
		t.Fatalf("obo: %d %s", r.status, string(r.raw))
	}
	if int(r.body["expires_in"].(float64)) != 300 {
		t.Errorf("expires_in = %v, want 300", r.body["expires_in"])
	}
	claims, err := f.issuer.Verify(r.body["access_token"].(string))
	if err != nil {
		t.Fatal(err)
	}
	if claims.Typ != domain.TypAgentOBO ||
		claims.Subject != "agent:analytics@v14" ||
		claims.OBOSub != u.ID.String() ||
		claims.TenantID != tn.ID ||
		claims.AgentID != "analytics" || claims.AgentVersion != "v14" ||
		claims.SessionID != "s-1" ||
		len(claims.Scopes) != 1 || claims.Scopes[0] != "dataset.dataset.read" {
		t.Errorf("OBO claims wrong: %+v", claims)
	}
	// token.obo_issued audit event with dual attribution (MASTER-FR-041).
	evs := f.store.EventsOfType(domain.EvTokenOBOIssued)
	if len(evs) != 1 || evs[0].ViaAgent == nil || evs[0].ViaAgent.AgentID != "analytics" {
		t.Errorf("token.obo_issued event wrong: %+v", evs)
	}
	// BR-10: OBO tokens cannot be re-exchanged.
	rr := f.oboExchange(r.body["access_token"].(string), "analytics", "v14")
	if rr.status != http.StatusForbidden {
		t.Errorf("OBO-of-OBO: want 403, got %d", rr.status)
	}
}

// TestAutonomousToken covers IDN-FR-042: SPIFFE-gated, autonomous_allowed.
func TestAutonomousToken(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("autoco")
	f.enableAgent(tn, "watcher", "v1", true)
	body := map[string]any{"agent_id": "watcher", "version": "v1", "tenant_id": tn.ID.String()}

	// No SPIFFE identity -> 403.
	if r := f.do(http.MethodPost, "/api/v1/token/agent", "", body); r.status != http.StatusForbidden {
		t.Fatalf("no spiffe: want 403, got %d", r.status)
	}
	// Untrusted workload -> 403.
	if r := f.do(http.MethodPost, "/api/v1/token/agent", "", body, [2]string{"X-Spiffe-Id", "spiffe://evil"}); r.status != http.StatusForbidden {
		t.Fatalf("untrusted spiffe: want 403, got %d", r.status)
	}
	// agent-runtime -> 200 typ=agent_autonomous.
	r := f.do(http.MethodPost, "/api/v1/token/agent", "", body, [2]string{"X-Spiffe-Id", testSpiffeAgentRuntime})
	if r.status != http.StatusOK {
		t.Fatalf("agent token: %d %s", r.status, string(r.raw))
	}
	claims, err := f.issuer.Verify(r.body["access_token"].(string))
	if err != nil || claims.Typ != domain.TypAgentAutonomous {
		t.Fatalf("claims: %+v err=%v", claims, err)
	}
	// autonomous_allowed=false -> AGENT_DISABLED.
	f.enableAgent(tn, "helper", "v1", false)
	r = f.do(http.MethodPost, "/api/v1/token/agent", "",
		map[string]any{"agent_id": "helper", "version": "v1", "tenant_id": tn.ID.String()},
		[2]string{"X-Spiffe-Id", testSpiffeAgentRuntime})
	if r.status != http.StatusForbidden || r.errCode(t) != domain.CodeAgentDisabled {
		t.Fatalf("non-autonomous agent: want 403 AGENT_DISABLED, got %d %s", r.status, string(r.raw))
	}
}

// TestTenantIsolationSuite is the unit-tier isolation suite (MASTER-FR-004,
// CONVENTIONS §testing): every tenant-scoped endpoint hit with tenant A's
// token against tenant B's resources returns 404.
func TestTenantIsolationSuite(t *testing.T) {
	f := newFixture(t)
	a := f.activeTenant("iso-a")
	b := f.activeTenant("iso-b")
	bUser := f.activeUser(b, "victim@iso-b.com")
	r := f.do(http.MethodPost, "/api/v1/service-accounts", f.adminToken(b.ID), map[string]any{
		"name": "b-key", "scopes": []string{"dataset.dataset.read"},
	})
	bSA := r.body["service_account"].(map[string]any)["id"].(string)
	aTok := f.adminToken(a.ID)

	cases := []struct {
		method, path string
		body         any
	}{
		{http.MethodGet, "/api/v1/users/" + bUser.ID.String(), nil},
		{http.MethodPatch, "/api/v1/users/" + bUser.ID.String(), map[string]any{"full_name": "hax"}},
		{http.MethodPost, "/api/v1/users/" + bUser.ID.String() + "/deactivate", nil},
		{http.MethodPost, "/api/v1/users/" + bUser.ID.String() + "/invite/resend", nil},
		{http.MethodDelete, "/api/v1/users/" + bUser.ID.String(), nil},
		{http.MethodPost, "/api/v1/service-accounts/" + bSA + "/rotate", nil},
		{http.MethodDelete, "/api/v1/service-accounts/" + bSA, nil},
		{http.MethodGet, "/api/v1/tenants/" + b.ID.String(), nil},
	}
	for _, c := range cases {
		r := f.do(c.method, c.path, aTok, c.body)
		if r.status != http.StatusNotFound {
			t.Errorf("%s %s with tenant A token: want 404, got %d (%s)", c.method, c.path, r.status, string(r.raw))
		}
	}
	// B's resources are untouched.
	if u, _ := f.store.GetUser(context.Background(), b.ID, bUser.ID); u.Status != domain.UserActive {
		t.Error("tenant B user mutated by cross-tenant request")
	}
}

// TestAuthzMatrix: endpoints reject tokens lacking the required scope with
// 403, and unauthenticated calls with 401 (MASTER-FR-071 authz matrix).
func TestAuthzMatrix(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("authzco")
	noScopes := f.mint(domain.Claims{Subject: "u", TenantID: tn.ID, Typ: domain.TypUser, Scopes: []string{}})
	cases := []struct {
		method, path string
		body         any
	}{
		{http.MethodPost, "/api/v1/users/invite", map[string]any{"email": "x@y.com"}},
		{http.MethodGet, "/api/v1/users", nil},
		{http.MethodPost, "/api/v1/service-accounts", map[string]any{"name": "k", "scopes": []string{"a.b.c"}}},
		{http.MethodGet, "/api/v1/credentials", nil},
		{http.MethodPost, "/api/v1/tenants", map[string]any{"name": "zzz", "owner_email": "z@z.com", "tier": "pool", "cloud": "aws"}},
		{http.MethodGet, "/api/v1/tenants", nil},
		{http.MethodPost, "/api/v1/keys/rotate", nil},
	}
	for _, c := range cases {
		if r := f.do(c.method, c.path, noScopes, c.body); r.status != http.StatusForbidden {
			t.Errorf("%s %s scopeless: want 403, got %d", c.method, c.path, r.status)
		}
		if r := f.do(c.method, c.path, "", c.body); r.status != http.StatusUnauthorized {
			t.Errorf("%s %s unauthenticated: want 401, got %d", c.method, c.path, r.status)
		}
	}
}

// TestIdempotencyReplay covers MASTER-FR-025.
func TestIdempotencyReplay(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("idemco")
	admin := f.adminToken(tn.ID)
	body := map[string]any{"name": "ci", "scopes": []string{"dataset.dataset.read"}}
	key := [2]string{"Idempotency-Key", "idem-123"}

	r1 := f.do(http.MethodPost, "/api/v1/service-accounts", admin, body, key)
	if r1.status != http.StatusCreated {
		t.Fatalf("first: %d %s", r1.status, string(r1.raw))
	}
	r2 := f.do(http.MethodPost, "/api/v1/service-accounts", admin, body, key)
	if r2.status != http.StatusCreated || r2.headers.Get("Idempotency-Replayed") != "true" {
		t.Fatalf("replay: status=%d replayed=%q", r2.status, r2.headers.Get("Idempotency-Replayed"))
	}
	if string(r1.raw) != string(r2.raw) {
		t.Error("replayed body differs from original (api_key must match, shown-once preserved)")
	}
	// Same key, different body -> 409.
	r3 := f.do(http.MethodPost, "/api/v1/service-accounts", admin,
		map[string]any{"name": "other", "scopes": []string{"dataset.dataset.read"}}, key)
	if r3.status != http.StatusConflict {
		t.Fatalf("key reuse with different body: want 409, got %d", r3.status)
	}
	// Only one SA was created.
	n, _ := f.store.CountServiceAccounts(context.Background(), tn.ID)
	if n != 1 {
		t.Fatalf("expected 1 service account, got %d", n)
	}
}

// TestCursorPagination covers MASTER-FR-022.
func TestCursorPagination(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("pageco")
	admin := f.adminToken(tn.ID)
	for i := 0; i < 5; i++ {
		r := f.do(http.MethodPost, "/api/v1/service-accounts", admin, map[string]any{
			"name": fmt.Sprintf("sa-%d", i), "scopes": []string{"dataset.dataset.read"},
		})
		if r.status != http.StatusCreated {
			t.Fatalf("create %d: %d", i, r.status)
		}
	}
	r := f.do(http.MethodGet, "/api/v1/service-accounts?limit=2", admin, nil)
	if n := len(r.body["data"].([]any)); n != 2 {
		t.Fatalf("page 1 size = %d", n)
	}
	page := r.body["page"].(map[string]any)
	if page["has_more"] != true || page["next_cursor"] == nil {
		t.Fatalf("page 1 envelope: %v", page)
	}
	seen := 2
	cursor := page["next_cursor"].(string)
	for cursor != "" {
		r = f.do(http.MethodGet, "/api/v1/service-accounts?limit=2&cursor="+cursor, admin, nil)
		seen += len(r.body["data"].([]any))
		page = r.body["page"].(map[string]any)
		if page["has_more"] == false {
			break
		}
		cursor = page["next_cursor"].(string)
	}
	if seen != 5 {
		t.Fatalf("paged through %d items, want 5", seen)
	}
	// Invalid cursor -> 422.
	if r := f.do(http.MethodGet, "/api/v1/service-accounts?cursor=%25%25", admin, nil); r.status != 422 {
		t.Errorf("invalid cursor: want 422, got %d", r.status)
	}
}

// TestServiceAccountLimit: max 20 per tenant (IDN-FR-031).
func TestServiceAccountLimit(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("limitco")
	admin := f.adminToken(tn.ID)
	for i := 0; i < domain.MaxServiceAccountsPerTenant; i++ {
		r := f.do(http.MethodPost, "/api/v1/service-accounts", admin, map[string]any{
			"name": fmt.Sprintf("sa-%d", i), "scopes": []string{"dataset.dataset.read"},
		})
		if r.status != http.StatusCreated {
			t.Fatalf("create %d: %d", i, r.status)
		}
	}
	r := f.do(http.MethodPost, "/api/v1/service-accounts", admin, map[string]any{
		"name": "one-too-many", "scopes": []string{"dataset.dataset.read"},
	})
	if r.status != 422 {
		t.Fatalf("21st SA: want 422, got %d", r.status)
	}
}

// TestSARotationOverlap: rotated key -> new secret works, old works during
// overlap then dies (IDN-FR-033).
func TestSARotationOverlap(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("saovl")
	admin := f.adminToken(tn.ID)
	r := f.do(http.MethodPost, "/api/v1/service-accounts", admin, map[string]any{
		"name": "ci", "scopes": []string{"dataset.dataset.read"},
	})
	oldKey := r.body["api_key"].(string)
	saID := r.body["service_account"].(map[string]any)["id"].(string)
	r = f.do(http.MethodPost, "/api/v1/service-accounts/"+saID+"/rotate", admin, nil)
	if r.status != http.StatusOK {
		t.Fatalf("rotate: %d %s", r.status, string(r.raw))
	}
	newKey := r.body["api_key"].(string)
	if newKey == oldKey {
		t.Fatal("rotation returned the same key")
	}
	if r := f.do(http.MethodPost, "/api/v1/token/apikey", "", map[string]any{"api_key": newKey}); r.status != http.StatusOK {
		t.Fatalf("new key: %d", r.status)
	}
	if r := f.do(http.MethodPost, "/api/v1/token/apikey", "", map[string]any{"api_key": oldKey}); r.status != http.StatusOK {
		t.Fatalf("old key inside overlap: %d", r.status)
	}
	f.clock.Advance(domain.RotationOverlap + time.Minute)
	if r := f.do(http.MethodPost, "/api/v1/token/apikey", "", map[string]any{"api_key": oldKey}); r.status != http.StatusUnauthorized {
		t.Fatalf("old key after overlap: want 401, got %d", r.status)
	}
}

// TestCredentialsInventory covers US-8.
func TestCredentialsInventory(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("credco")
	f.activeUser(tn, "worker@credco.com")
	f.enableAgent(tn, "analytics", "v14", false)
	admin := f.adminToken(tn.ID)
	f.do(http.MethodPost, "/api/v1/service-accounts", admin, map[string]any{
		"name": "ci", "scopes": []string{"dataset.dataset.read"},
	})
	r := f.do(http.MethodGet, "/api/v1/credentials", admin, nil)
	if r.status != http.StatusOK {
		t.Fatalf("credentials: %d", r.status)
	}
	kinds := map[string]int{}
	for _, e := range r.body["data"].([]any) {
		kinds[e.(map[string]any)["kind"].(string)]++
	}
	// owner (seeded) + worker = 2 users, 1 SA, 1 agent principal.
	if kinds["user"] != 2 || kinds["service_account"] != 1 || kinds["agent_principal"] != 1 {
		t.Errorf("inventory kinds = %v", kinds)
	}
}

// TestBR2ConcurrentPublishRejected: duplicate provisioning start -> 409.
func TestBR2ConcurrentPublishRejected(t *testing.T) {
	f := newFixture(t)
	r := f.createTenant("brco", false)
	id := r.body["id"].(string)
	if r := f.do(http.MethodPost, "/api/v1/tenants/"+id+"/publish", f.superToken(), nil); r.status != http.StatusAccepted {
		t.Fatalf("publish: %d", r.status)
	}
	// Tenant is now active (sync engine); a second publish must 409.
	if r := f.do(http.MethodPost, "/api/v1/tenants/"+id+"/publish", f.superToken(), nil); r.status != http.StatusConflict {
		t.Fatalf("second publish: want 409, got %d", r.status)
	}
	// Suspend from draft is also guarded.
	r2 := f.createTenant("brco2", false)
	if r := f.do(http.MethodPost, "/api/v1/tenants/"+r2.body["id"].(string)+"/suspend", f.superToken(), nil); r.status != http.StatusConflict {
		t.Fatalf("suspend draft: want 409, got %d", r.status)
	}
}

// TestTraceIDPropagation: every response carries X-Trace-Id (MASTER-FR-028).
func TestTraceIDPropagation(t *testing.T) {
	f := newFixture(t)
	r := f.do(http.MethodGet, "/healthz", "", nil)
	if r.headers.Get("X-Trace-Id") == "" {
		t.Error("missing X-Trace-Id")
	}
	r = f.do(http.MethodGet, "/api/v1/users", "", nil, [2]string{"X-Trace-Id", "trace-abc"})
	if r.headers.Get("X-Trace-Id") != "trace-abc" {
		t.Errorf("trace id not propagated: %q", r.headers.Get("X-Trace-Id"))
	}
	e := r.body["error"].(map[string]any)
	if e["trace_id"] != "trace-abc" {
		t.Errorf("error envelope trace_id = %v, want trace-abc", e["trace_id"])
	}
}
