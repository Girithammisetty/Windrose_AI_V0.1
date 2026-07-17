package api_test

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/api"
	"github.com/windrose-ai/identity-service/internal/domain"
)

// F-2: X-Spiffe-Id is honored only when TrustSpiffeHeader is enabled. With it
// disabled (production default) a valid-looking header must NOT mint an
// autonomous agent token.
func TestF2_SpiffeHeaderUntrustedByDefault(t *testing.T) {
	f := newFixtureOpt(t, false) // header trust OFF
	tn := f.activeTenant("f2co")
	f.enableAgent(tn, "watcher", "v1", true)
	body := map[string]any{"agent_id": "watcher", "version": "v1", "tenant_id": tn.ID.String()}
	r := f.do(http.MethodPost, "/api/v1/token/agent", "", body, [2]string{"X-Spiffe-Id", testSpiffeAgentRuntime})
	if r.status != http.StatusForbidden {
		t.Fatalf("spoofed header with trust disabled: want 403, got %d %s", r.status, string(r.raw))
	}
	// Sanity: with trust enabled the same request succeeds.
	f2 := newFixtureOpt(t, true)
	tn2 := f2.activeTenant("f2co")
	f2.enableAgent(tn2, "watcher", "v1", true)
	r = f2.do(http.MethodPost, "/api/v1/token/agent", "",
		map[string]any{"agent_id": "watcher", "version": "v1", "tenant_id": tn2.ID.String()},
		[2]string{"X-Spiffe-Id", testSpiffeAgentRuntime})
	if r.status != http.StatusOK {
		t.Fatalf("trusted header: want 200, got %d %s", r.status, string(r.raw))
	}
}

// F-3: GET /tenants/{id} requires an admin scope — a zero-scope token cannot
// read registry internals even for its own tenant.
func TestF3_GetTenantRequiresAdminScope(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("f3co")
	zeroScope := f.mint(domain.Claims{Subject: "u", TenantID: tn.ID, Typ: domain.TypUser, Scopes: []string{}})
	r := f.do(http.MethodGet, "/api/v1/tenants/"+tn.ID.String(), zeroScope, nil)
	if r.status != http.StatusForbidden {
		t.Fatalf("zero-scope GET own tenant: want 403, got %d %s", r.status, string(r.raw))
	}
	// An admin-scoped token still reads its own tenant.
	if r := f.do(http.MethodGet, "/api/v1/tenants/"+tn.ID.String(), f.adminToken(tn.ID), nil); r.status != http.StatusOK {
		t.Fatalf("admin GET own tenant: want 200, got %d", r.status)
	}
}

// F-4: idempotency records are scoped per acting subject, so two super-admins
// sharing tenant_id=Nil do not cross-replay.
func TestF4_IdempotencyScopedPerSubject(t *testing.T) {
	f := newFixture(t)
	staffA := f.mint(domain.Claims{Subject: "staff-A", TenantID: uuid.Nil, Typ: domain.TypUser, Scopes: []string{"platform.admin"}})
	staffB := f.mint(domain.Claims{Subject: "staff-B", TenantID: uuid.Nil, Typ: domain.TypUser, Scopes: []string{"platform.admin"}})
	body := map[string]any{"name": "f4co", "owner_email": "o@f4.com", "tier": "pool", "cloud": "aws"}
	key := [2]string{"Idempotency-Key", "shared-key"}

	rA := f.do(http.MethodPost, "/api/v1/tenants", staffA, body, key)
	if rA.status != http.StatusCreated {
		t.Fatalf("staff A create: %d %s", rA.status, string(rA.raw))
	}
	// Staff B, same key + body: must NOT replay A's response. It executes and
	// hits the duplicate-name validation instead.
	rB := f.do(http.MethodPost, "/api/v1/tenants", staffB, body, key)
	if rB.headers.Get("Idempotency-Replayed") == "true" {
		t.Fatal("F-4: staff B received staff A's replayed response")
	}
	if rB.status != 422 {
		t.Fatalf("staff B create dup name: want 422, got %d %s", rB.status, string(rB.raw))
	}
	// Staff A repeating the key replays its own original 201.
	rA2 := f.do(http.MethodPost, "/api/v1/tenants", staffA, body, key)
	if rA2.status != http.StatusCreated || rA2.headers.Get("Idempotency-Replayed") != "true" {
		t.Fatalf("staff A replay: status=%d replayed=%q", rA2.status, rA2.headers.Get("Idempotency-Replayed"))
	}
}

// F-5: an agent token carrying an admin-looking scope string cannot drive
// identity administration (typ allowlist blocks it before ScopeAuthorizer).
func TestF5_AgentTypeRejectedOnAdminEndpoints(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("f5co")
	for _, typ := range []string{domain.TypAgentOBO, domain.TypAgentAutonomous} {
		agentTok := f.mint(domain.Claims{
			Subject: "agent:x@v1", TenantID: tn.ID, Typ: typ,
			AgentID: "x", AgentVersion: "v1",
			Scopes: []string{api.ActUserAdmin, api.ActSvcAcctAdmin, "platform.admin"},
		})
		cases := []struct {
			method, path string
			body         any
		}{
			{http.MethodPost, "/api/v1/users/invite", map[string]any{"email": "x@y.com"}},
			{http.MethodGet, "/api/v1/users", nil},
			{http.MethodPost, "/api/v1/service-accounts", map[string]any{"name": "k", "scopes": []string{"a.b.c"}}},
			{http.MethodPost, "/api/v1/tenants", map[string]any{"name": "zz", "owner_email": "z@z.com", "tier": "pool", "cloud": "aws"}},
			{http.MethodGet, "/api/v1/tenants/" + tn.ID.String(), nil},
		}
		for _, c := range cases {
			r := f.do(c.method, c.path, agentTok, c.body)
			if r.status != http.StatusForbidden {
				t.Errorf("typ=%s %s %s: want 403, got %d", typ, c.method, c.path, r.status)
			}
		}
	}
	// A service token (typ=service) with the scope IS allowed — the allowlist
	// blocks only agent principals.
	svc := f.mint(domain.Claims{Subject: "sa:1", TenantID: tn.ID, Typ: domain.TypService, Scopes: []string{api.ActUserAdmin}})
	if r := f.do(http.MethodGet, "/api/v1/users", svc, nil); r.status != http.StatusOK {
		t.Fatalf("service token with user-admin scope: want 200, got %d", r.status)
	}
}

// --- coverage nudges (F-6) on real business logic ---

// TestPatchTenant covers TenantService.Patch through the HTTP layer.
func TestPatchTenant(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("patchco")
	r := f.do(http.MethodPatch, "/api/v1/tenants/"+tn.ID.String(), f.superToken(), map[string]any{
		"display_name": "Patched Co", "auto_upgrade": true,
		"quotas": map[string]any{"cpu": 8, "memory": "32Gi", "processing_cpu": 8, "processing_memory": "32Gi"},
	})
	if r.status != http.StatusOK {
		t.Fatalf("patch: %d %s", r.status, string(r.raw))
	}
	if r.body["display_name"] != "Patched Co" || r.body["auto_upgrade"] != true {
		t.Fatalf("patch not applied: %v", r.body)
	}
	if int(r.body["quotas"].(map[string]any)["cpu"].(float64)) != 8 {
		t.Fatalf("quota not applied: %v", r.body["quotas"])
	}
	// Invalid quota -> 422.
	if r := f.do(http.MethodPatch, "/api/v1/tenants/"+tn.ID.String(), f.superToken(), map[string]any{
		"quotas": map[string]any{"cpu": 0, "memory": "1Gi", "processing_cpu": 1, "processing_memory": "1Gi"},
	}); r.status != 422 {
		t.Fatalf("invalid quota patch: want 422, got %d", r.status)
	}
}

// TestScheduledDeletion covers TenantService.Delete grace scheduling +
// RunScheduledDeletions sweep (IDN-FR-008b).
func TestScheduledDeletion(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("schedco")
	// mode=destroy without force -> scheduled with a 7-day grace, still active-ish.
	r := f.do(http.MethodDelete, "/api/v1/tenants/"+tn.ID.String()+"?mode=destroy", f.superToken(), nil)
	if r.status != http.StatusOK {
		t.Fatalf("delete: %d %s", r.status, string(r.raw))
	}
	got, _ := f.store.GetTenant(context.Background(), tn.ID)
	if got.Status != domain.TenantDeleting || got.DeletionScheduledAt == nil {
		t.Fatalf("expected deleting+scheduled, got status=%s scheduled=%v", got.Status, got.DeletionScheduledAt)
	}
	// Before the grace elapses, the sweep is a no-op.
	if err := f.tenants().RunScheduledDeletions(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got, _ := f.store.GetTenant(context.Background(), tn.ID); got.Status != domain.TenantDeleting {
		t.Fatalf("premature deletion: %s", got.Status)
	}
	// After the grace period, the sweep completes destruction.
	f.clock.Advance(domain.DeletionGracePeriod + time.Hour)
	if err := f.tenants().RunScheduledDeletions(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got, _ := f.store.GetTenant(context.Background(), tn.ID); got.Status != domain.TenantDeleted {
		t.Fatalf("post-grace sweep: want deleted, got %s", got.Status)
	}
}

// TestUserPatchAndSoftDelete covers UserService.Patch + SoftDelete via HTTP.
func TestUserPatchAndSoftDelete(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("userco")
	u := f.activeUser(tn, "member@userco.com")
	admin := f.adminToken(tn.ID)

	r := f.do(http.MethodPatch, "/api/v1/users/"+u.ID.String(), admin, map[string]any{"full_name": "Renamed"})
	if r.status != http.StatusOK || r.body["full_name"] != "Renamed" {
		t.Fatalf("patch user: %d %s", r.status, string(r.raw))
	}
	if r := f.do(http.MethodDelete, "/api/v1/users/"+u.ID.String(), admin, nil); r.status != http.StatusNoContent {
		t.Fatalf("delete user: want 204, got %d", r.status)
	}
	got, _ := f.store.GetUser(context.Background(), tn.ID, u.ID)
	if got.DeletedAt == nil || got.Status != domain.UserDeactivated {
		t.Fatalf("soft-delete not applied: %+v", got)
	}
	if len(f.store.EventsOfType(domain.EvUserDeleted)) == 0 {
		t.Error("no user.deleted event emitted")
	}
}

// TestRetryProvisioningGuard covers the error path: retry is only valid from
// provision_failed; on an active tenant it conflicts (409).
func TestRetryProvisioningGuard(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("retryguard")
	if r := f.do(http.MethodPost, "/api/v1/tenants/"+tn.ID.String()+"/provisioning/retry", f.superToken(), nil); r.status != http.StatusConflict {
		t.Fatalf("retry on active tenant: want 409, got %d %s", r.status, string(r.raw))
	}
	// Reactivate guard: reactivating an active tenant is also a 409.
	if r := f.do(http.MethodPost, "/api/v1/tenants/"+tn.ID.String()+"/reactivate", f.superToken(), nil); r.status != http.StatusConflict {
		t.Fatalf("reactivate active tenant: want 409, got %d", r.status)
	}
	// Provisioning status for an unknown tenant is a 404.
	if r := f.do(http.MethodGet, "/api/v1/tenants/"+uuid.NewString()+"/provisioning", f.superToken(), nil); r.status != http.StatusNotFound {
		t.Fatalf("status of unknown tenant: want 404, got %d", r.status)
	}
}

// TestKeyManagerRetireDueKeys covers RetireDueKeys cache refresh.
func TestKeyManagerRetireDueKeys(t *testing.T) {
	f := newFixture(t)
	if err := f.km.RetireDueKeys(context.Background()); err != nil {
		t.Fatalf("RetireDueKeys: %v", err)
	}
	if _, err := f.km.SigningKey(); err != nil {
		t.Fatalf("signing key missing after refresh: %v", err)
	}
}

// TestStubEndpoints covers the documented 501 stubs (IDN-FR-009/024).
func TestStubEndpoints(t *testing.T) {
	f := newFixture(t)
	if r := f.do(http.MethodGet, "/api/v1/platform-versions", f.superToken(), nil); r.status != http.StatusNotImplemented || r.errCode(t) != domain.CodeNotImplemented {
		t.Fatalf("platform-versions: want 501 NOT_IMPLEMENTED, got %d %s", r.status, string(r.raw))
	}
	if r := f.do(http.MethodGet, "/api/v1/scim/v2/Users", f.adminToken(uuid.Nil), nil); r.status != http.StatusNotImplemented {
		t.Fatalf("scim: want 501, got %d", r.status)
	}
}

// TestOpsEndpoints covers /readyz (both branches) and /metrics.
func TestOpsEndpoints(t *testing.T) {
	f := newFixture(t)
	if r := f.do(http.MethodGet, "/readyz", "", nil); r.status != http.StatusOK {
		t.Fatalf("readyz (no Ready hook): %d", r.status)
	}
	f.srv.Ready = func() error { return context.DeadlineExceeded }
	if r := f.do(http.MethodGet, "/readyz", "", nil); r.status != http.StatusServiceUnavailable {
		t.Fatalf("readyz unready: want 503, got %d", r.status)
	}
	f.srv.Ready = nil
	if r := f.do(http.MethodGet, "/metrics", "", nil); r.status != http.StatusOK {
		t.Fatalf("metrics: %d", r.status)
	}
	if r := f.do(http.MethodGet, "/healthz", "", nil); r.status != http.StatusOK {
		t.Fatalf("healthz: %d", r.status)
	}
}

// FIX A: GET /users honors filter[id]=<comma-separated uuids> — the batch
// hydration path bff-graphql's userById loader depends on (BFF-FR-030/031).
func TestListUsersFilterByID(t *testing.T) {
	f := newFixture(t)
	tn := f.activeTenant("filterco")
	u1 := f.activeUser(tn, "a@filterco.com")
	u2 := f.activeUser(tn, "b@filterco.com")
	u3 := f.activeUser(tn, "c@filterco.com")

	list := func(q string) resp {
		return f.do(http.MethodGet, "/api/v1/users"+q, f.adminToken(tn.ID), nil)
	}
	ids := func(r resp) map[string]bool {
		t.Helper()
		data, ok := r.body["data"].([]any)
		if !ok {
			t.Fatalf("no data array: %s", string(r.raw))
		}
		out := map[string]bool{}
		for _, it := range data {
			out[it.(map[string]any)["id"].(string)] = true
		}
		return out
	}

	// No filter: all three (plus the tenant owner) come back.
	if r := list(""); r.status != http.StatusOK || len(ids(r)) < 3 {
		t.Fatalf("unfiltered list: %d %s", r.status, string(r.raw))
	}

	// filter[id]=u1,u3 returns exactly those two.
	r := list("?filter[id]=" + u1.ID.String() + "," + u3.ID.String())
	if r.status != http.StatusOK {
		t.Fatalf("filtered list: %d %s", r.status, string(r.raw))
	}
	got := ids(r)
	if len(got) != 2 || !got[u1.ID.String()] || !got[u3.ID.String()] || got[u2.ID.String()] {
		t.Fatalf("filter[id] returned wrong set: %v", got)
	}

	// Unknown id: isolated miss, not an error (loader null-per-key, BR-5).
	r = list("?filter[id]=" + uuid.NewString())
	if r.status != http.StatusOK || len(ids(r)) != 0 {
		t.Fatalf("unknown id: want 200 + empty data, got %d %s", r.status, string(r.raw))
	}

	// Cross-tenant id: never leaks (MASTER-FR-003).
	tn2 := f.activeTenant("filterco2")
	if r := f.do(http.MethodGet, "/api/v1/users?filter[id]="+u1.ID.String(), f.adminToken(tn2.ID), nil); r.status != http.StatusOK || len(ids(r)) != 0 {
		t.Fatalf("cross-tenant filter: want 200 + empty, got %d %s", r.status, string(r.raw))
	}

	// Malformed id: 422 VALIDATION_FAILED (identity's EValidation mapping).
	if r := list("?filter[id]=not-a-uuid"); r.status != http.StatusUnprocessableEntity || r.errCode(t) != "VALIDATION_FAILED" {
		t.Fatalf("malformed filter[id]: want 422 VALIDATION_FAILED, got %d %s", r.status, string(r.raw))
	}
}
