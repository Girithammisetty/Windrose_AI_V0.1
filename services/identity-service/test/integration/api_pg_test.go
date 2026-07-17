//go:build integration

package integration

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/windrose-ai/identity-service/internal/adapters/denylist"
	"github.com/windrose-ai/identity-service/internal/adapters/keycloak"
	"github.com/windrose-ai/identity-service/internal/adapters/terraform"
	"github.com/windrose-ai/identity-service/internal/api"
	"github.com/windrose-ai/identity-service/internal/authz"
	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/keys"
	pgstore "github.com/windrose-ai/identity-service/internal/store/postgres"
)

// TestAPITenantIsolationOnPostgres runs the isolation suite end-to-end:
// HTTP -> handlers -> pgx -> RLS (MASTER-FR-003/004 on the real stack).
func TestAPITenantIsolationOnPostgres(t *testing.T) {
	requirePG(t)
	ctx := context.Background()
	store := pgstore.New(appPool)

	km := keys.NewKeyManager(store, keys.NewLocalSigner(), time.Now)
	if err := km.Bootstrap(ctx); err != nil {
		t.Fatal(err)
	}
	issuer := keys.NewIssuer(km, time.Now)
	users := &domain.UserService{Store: store, Keycloak: keycloak.NewFake(), LastAdmin: domain.AllowAllLastAdminChecker{}, Clock: time.Now}
	sas := &domain.ServiceAccountService{Store: store, Denylist: denylist.NewMemory(), Clock: time.Now}
	tokens := &domain.TokenService{
		Store: store, Issuer: issuer, Verifier: issuer, Denylist: denylist.NewMemory(),
		Limiter: domain.NewSlidingWindowLimiter(domain.OBORateLimit, domain.OBORateWindow), Clock: time.Now,
	}
	deps := domain.StepDeps{Store: store, Keycloak: keycloak.NewFake(), Terraform: terraform.NewFake(), DB: terraform.NewFakeDB(), Prober: &terraform.FakeProber{}}
	cfg := domain.DefaultEngineConfig()
	cfg.Backoff = func(int) time.Duration { return 0 }
	engine := domain.NewEngine(store, cfg, deps.ProvisionSteps, deps.DestroySteps, nil)
	tenants := &domain.TenantService{Store: store, Engine: engine, Graph: domain.DefaultModuleGraph(), Prober: deps.Prober, Clock: time.Now}
	srv := &api.Server{
		Store: store, Tenants: tenants, Users: users, SAs: sas, Tokens: tokens,
		KM: km, Verifier: issuer, Authz: authz.ScopeAuthorizer{},
		TrustedSpiffeIDs: map[string]bool{}, Clock: time.Now,
	}
	ts := httptest.NewServer(srv.Router())
	defer ts.Close()

	a := newTenantRow(t, store, domain.TenantActive)
	b := newTenantRow(t, store, domain.TenantActive)
	bUser := newUserRow(t, store, b.ID, "victim@"+b.Name+".com")

	mint := func(c domain.Claims) string {
		tok, _, err := issuer.Issue(c)
		if err != nil {
			t.Fatal(err)
		}
		return tok
	}
	aTok := mint(domain.Claims{
		Subject: "admin-a", TenantID: a.ID, Typ: domain.TypUser,
		Scopes: []string{api.ActUserAdmin, api.ActSvcAcctAdmin, api.ActCredentialRead},
	})

	call := func(method, path, token string, body any) (int, map[string]any) {
		t.Helper()
		var rd io.Reader
		if body != nil {
			bb, _ := json.Marshal(body)
			rd = bytes.NewReader(bb)
		}
		req, _ := http.NewRequest(method, ts.URL+path, rd)
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		res, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		raw, _ := io.ReadAll(res.Body)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		return res.StatusCode, m
	}

	// Cross-tenant reads/mutations through the full stack -> 404.
	for _, c := range []struct{ method, path string }{
		{http.MethodGet, "/api/v1/users/" + bUser.ID.String()},
		{http.MethodPost, "/api/v1/users/" + bUser.ID.String() + "/deactivate"},
		{http.MethodDelete, "/api/v1/users/" + bUser.ID.String()},
		{http.MethodGet, "/api/v1/tenants/" + b.ID.String()},
	} {
		status, _ := call(c.method, c.path, aTok, nil)
		if status != http.StatusNotFound {
			t.Errorf("%s %s: want 404, got %d", c.method, c.path, status)
		}
	}

	// Own-tenant listing sees only own rows.
	newUserRow(t, store, a.ID, "own@"+a.Name+".com")
	status, body := call(http.MethodGet, "/api/v1/users", aTok, nil)
	if status != http.StatusOK {
		t.Fatalf("list users: %d", status)
	}
	for _, item := range body["data"].([]any) {
		if item.(map[string]any)["tenant_id"] != a.ID.String() {
			t.Fatalf("foreign tenant row leaked into listing: %v", item)
		}
	}

	// Cross-tenant denial is audited (MASTER-FR-003).
	var n int
	if err := adminPool.QueryRow(ctx,
		"SELECT count(*) FROM outbox WHERE event_type='security.cross_tenant_denied' AND tenant_id=$1", a.ID).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n == 0 {
		t.Error("no cross_tenant_denied audit event persisted")
	}
}
