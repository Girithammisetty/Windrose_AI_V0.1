//go:build integration

// Integration tests for the Redis-miss fallback (RBC-FR-045) against the REAL
// running dev stack: real rbac-service (deploy/e2e/config.env RBAC_URL, real
// Postgres-backed ground truth), real Redis, real OPA sidecar. No mocks, no
// stubs — this proves the actual fix (a Redis-key wipe no longer denies a
// real, previously-working request) against the same infrastructure a
// production deploy runs.
//
// Requires the local dev stack up (deploy/local/up.sh or deploy/e2e/boot_services.sh)
// and a real seeded persona (deploy/local/run/personas.json — admin@demo.windrose).
package opaclient

import (
	"context"
	"os"
	"testing"

	"github.com/windrose-ai/go-common/redisx"
)

func rbacURL() string {
	if u := os.Getenv("RBAC_URL"); u != "" {
		return u
	}
	return "http://localhost:8302"
}

func redisAddr() string {
	if a := os.Getenv("REDIS_ADDR"); a != "" {
		return a
	}
	return "localhost:6379"
}

// realAdminSubject is a genuinely seeded persona (deploy/local/run/personas.json,
// deploy/local/seed_platform.py) — a real row in the real Postgres of the
// running dev stack, tenant.admin scope, "admin" rbac role.
const (
	realAdminSub    = "019f6de5-34da-7301-a45b-a525052cd74d"
	realAdminTenant = "019f62c1-080d-71bd-b404-1d4e15b02dcb"
)

func realFallbackConfig(t *testing.T) FallbackConfig {
	t.Helper()
	pem, err := os.ReadFile(mustHarnessKeyPath(t))
	if err != nil {
		t.Skipf("harness signing key unavailable: %v", err)
	}
	return FallbackConfig{
		RBACURL:       rbacURL(),
		SigningKeyPEM: string(pem),
		SigningKID:    "e2e-harness-key-1",
		Issuer:        "https://identity.windrose.ai",
		Audience:      "windrose",
	}
}

func mustHarnessKeyPath(t *testing.T) string {
	t.Helper()
	// deploy/e2e/keys/idp_private.pem relative to this package's repo root.
	// Walk up from the test's own working directory (libs/go-common/opaclient).
	candidates := []string{
		"../../../deploy/e2e/keys/idp_private.pem",
		os.Getenv("REGISTER_SIGNING_KEY_PATH"),
	}
	for _, c := range candidates {
		if c == "" {
			continue
		}
		if _, err := os.Stat(c); err == nil {
			return c
		}
	}
	t.Skip("deploy/e2e/keys/idp_private.pem not found")
	return ""
}

// TestFallbackCheck_RealRBACService proves the fallback's HTTP+JWT wiring
// against the REAL running rbac-service: a real admin persona's tenant-scoped
// action check must come back allowed via admin bypass (services/rbac-service
// internal/authz Decide()), computed from real Postgres, not a canned value.
func TestFallbackCheck_RealRBACService(t *testing.T) {
	cfg := realFallbackConfig(t)
	c := &Client{}
	if err := c.EnableMissFallback(cfg); err != nil {
		t.Fatalf("EnableMissFallback: %v", err)
	}

	in := Input{
		Subject: Subject{ID: realAdminSub, Typ: "user"},
		Action:  "rbac.group.list",
		Tenant:  realAdminTenant,
	}
	dec, err := c.fb.check(context.Background(), in)
	if err != nil {
		t.Fatalf("real rbac-service unreachable at %s: %v (is the dev stack up?)", cfg.RBACURL, err)
	}
	if !dec.Allow {
		t.Fatalf("expected allow=true for a real tenant.admin persona (reason=%q) — either the fixture "+
			"persona changed or the fallback's request shape doesn't match rbac-service's checkRequest", dec.Reason)
	}
}

// TestCheckWithRedis_SelfHealsOnRealRedisMiss is the end-to-end proof: wipe
// this user's REAL Redis projection keys (simulating exactly what a Redis
// restart/flush does), confirm the plain Redis-only path denies (today's
// baseline bug), then confirm CheckWithRedis WITH the fallback enabled
// returns the correct real decision — and that it re-warmed Redis, so the
// immediately-following call (fallback aside) is fast-path correct again.
func TestCheckWithRedis_SelfHealsOnRealRedisMiss(t *testing.T) {
	ctx := context.Background()
	r := redisx.NewFromEnv(redisAddr(), os.Getenv)
	loader := NewLoader(r)

	in := Input{
		Subject: Subject{ID: realAdminSub, Typ: "user"},
		Action:  "rbac.group.list",
		Tenant:  realAdminTenant,
	}

	// Wipe this user's real projection keys — the exact condition a Redis
	// restart/failover leaves behind (deploy/local/reconcile.sh exists solely
	// to repair this by hand today).
	for _, k := range []string{
		"perm:" + realAdminTenant + ":" + realAdminSub + ":flags",
		"perm:" + realAdminTenant + ":" + realAdminSub + ":actions",
	} {
		if err := r.Del(ctx, k); err != nil {
			t.Fatalf("del %s: %v", k, err)
		}
	}

	opa := New(opaURL())

	// Baseline: without the fallback, a real miss really does deny (proves
	// the test actually created the failure condition it claims to fix).
	baseline, err := opa.CheckWithRedis(ctx, loader, in)
	if err != nil {
		t.Fatalf("baseline check: %v", err)
	}
	if !baseline.Miss || baseline.Allow {
		t.Fatalf("expected a genuine miss+deny baseline after wiping Redis keys, got allow=%v miss=%v",
			baseline.Allow, baseline.Miss)
	}

	// With the fallback enabled, the SAME wiped state must resolve correctly.
	if err := opa.EnableMissFallback(realFallbackConfig(t)); err != nil {
		t.Fatalf("EnableMissFallback: %v", err)
	}
	healed, err := opa.CheckWithRedis(ctx, loader, in)
	if err != nil {
		t.Fatalf("healed check: %v", err)
	}
	if !healed.Allow {
		t.Fatalf("expected the miss-fallback to resolve to allow=true (real tenant.admin persona), got reason=%q",
			healed.Reason)
	}

	// Prove the self-heal side effect: Redis is warm again, so the PLAIN
	// (no-fallback) path now also returns the correct answer without a miss.
	rewarmed, err := opa.CheckWithRedis(ctx, loader, in)
	if err != nil {
		t.Fatalf("re-warmed check: %v", err)
	}
	if rewarmed.Miss {
		t.Fatalf("expected Redis to be re-warmed by the fallback's side effect, still reporting a miss")
	}
	if !rewarmed.Allow {
		t.Fatalf("expected the re-warmed fast path to allow (matching the fallback decision), got reason=%q",
			rewarmed.Reason)
	}
}
