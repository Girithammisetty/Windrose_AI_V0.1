package authz_test

import (
	"context"
	"encoding/json"
	"net"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/go-common/redisx"

	"github.com/windrose-ai/identity-service/internal/authz"
	"github.com/windrose-ai/identity-service/internal/domain"
)

// TestOPAAuthorizerSuperAdminShortCircuit: a platform.admin token authorizes
// any identity action without touching OPA/Redis (IDN-FR-025). Points the
// authorizer at unreachable OPA/Redis to prove the short-circuit runs first.
func TestOPAAuthorizerSuperAdminShortCircuit(t *testing.T) {
	a := authz.NewOPAAuthorizer("http://127.0.0.1:1", "127.0.0.1:1")
	claims := &domain.Claims{
		Subject: "user-super", Typ: domain.TypUser, TenantID: uuid.New(),
		Scopes: []string{"platform.admin"},
	}
	if !a.Allow(context.Background(), claims, "identity.user.admin", "") {
		t.Fatal("platform super-admin must be allowed via short-circuit")
	}
}

// TestOPAAuthorizerFailsClosed: a non-super-admin decision with unreachable
// OPA/Redis must DENY (fail closed, MASTER-FR-012), never fail open.
func TestOPAAuthorizerFailsClosed(t *testing.T) {
	a := authz.NewOPAAuthorizer("http://127.0.0.1:1", "127.0.0.1:1")
	claims := &domain.Claims{Subject: "user-x", Typ: domain.TypUser, TenantID: uuid.New()}
	if a.Allow(context.Background(), claims, "identity.user.admin", "") {
		t.Fatal("must fail closed when OPA/Redis are unreachable")
	}
}

// TestOPAAuthorizerRealDecision is the end-to-end authz proof: seed the rbac
// projection (catalog + a tenant-admin flag) into the REAL Redis and evaluate
// against the REAL OPA sidecar. A tenant Admin (projection admin flag) is
// allowed identity.user.admin WITHOUT the scope in its token; a plain user with
// no projection is denied. Skips when the local OPA/Redis are not reachable.
func TestOPAAuthorizerRealDecision(t *testing.T) {
	const (
		opaURL    = "http://localhost:8281"
		redisAddr = "localhost:6379"
	)
	if !reachable("localhost:8281") || !reachable(redisAddr) {
		t.Skip("OPA :8281 or Redis :6379 not reachable — skipping real-decision authz test")
	}
	ctx := context.Background()
	r := redisx.New(redisAddr)

	tenant := uuid.New().String()
	admin := "user-admin-" + uuid.NewString()
	plain := "user-plain-" + uuid.NewString()
	const action = "identity.user.admin"

	// Catalog: action known + tenant-scoped (workspace_scoped=false), matching
	// how identity registers its guarded actions with rbac. The catalog key is
	// a shared global on the dev-stack Redis — merge additively, never replace.
	if err := opaclient.SeedCatalogActions(ctx, r, map[string]bool{action: false}); err != nil {
		t.Fatalf("seed catalog: %v", err)
	}
	// Admin flag for the admin user (BR-7 projection admin short-circuit).
	flags, _ := json.Marshal(map[string]any{"admin": true, "ws_admin": []string{}})
	adminFlagsKey := "perm:" + tenant + ":" + admin + ":flags"
	mustSet(t, ctx, r, adminFlagsKey, string(flags))
	t.Cleanup(func() {
		_ = r.Del(ctx, adminFlagsKey)
		// Leave perm:catalog:actions as-is if it predated us is impossible here
		// (unique action string), so removing our catalog entry is unnecessary;
		// the action key is namespaced by the unique action name only globally,
		// so we intentionally do NOT delete the shared catalog key.
	})

	a := authz.NewOPAAuthorizer(opaURL, redisAddr)

	adminClaims := &domain.Claims{
		Subject: admin, Typ: domain.TypUser, TenantID: uuid.MustParse(tenant),
	}
	if !a.Allow(ctx, adminClaims, action, "") {
		t.Errorf("tenant admin (projection admin flag) must be allowed %s without the scope", action)
	}

	plainClaims := &domain.Claims{
		Subject: plain, Typ: domain.TypUser, TenantID: uuid.MustParse(tenant),
	}
	if a.Allow(ctx, plainClaims, action, "") {
		t.Errorf("plain user with no projection must be denied %s", action)
	}
}

func reachable(addr string) bool {
	c, err := net.DialTimeout("tcp", addr, 500*time.Millisecond)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

func mustSet(t *testing.T, ctx context.Context, r *redisx.Client, key, val string) {
	t.Helper()
	if err := r.Set(ctx, key, val, 0); err != nil {
		t.Fatalf("redis set %s: %v", key, err)
	}
}
