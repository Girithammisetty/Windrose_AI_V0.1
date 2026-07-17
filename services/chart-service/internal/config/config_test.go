package config

import (
	"strings"
	"testing"
)

// TestAC00_BootDefaultAdaptersAreReal boots the wiring under the DEFAULT
// environment and asserts every adapter is a REAL type — no in-memory / stub
// adapter is reachable from cmd/server. This is the boot-introspection guard
// the two systemic rules require.
func TestAC00_BootDefaultAdaptersAreReal(t *testing.T) {
	cfg := Load()
	core := BuildCore(cfg)
	rep := core.Describe()

	checks := map[string]string{
		"authz":    rep.Authz,    // authz.OPA
		"cache":    rep.Cache,    // cache.Redis
		"semantic": rep.Semantic, // resolve.HTTPSemantic
		"query":    rep.Query,    // resolve.HTTPQuery
		"producer": rep.Producer, // kafka.Producer
	}
	wantSubstr := map[string]string{
		"authz": "authz.OPA", "cache": "cache.Redis",
		"semantic": "resolve.HTTPSemantic", "query": "resolve.HTTPQuery", "producer": "kafka.Producer",
	}
	for name, got := range checks {
		if !strings.Contains(got, wantSubstr[name]) {
			t.Errorf("%s adapter = %q, want real %q", name, got, wantSubstr[name])
		}
		if strings.Contains(strings.ToLower(got), "stub") || strings.Contains(strings.ToLower(got), "fake") ||
			strings.Contains(strings.ToLower(got), "mem") || strings.Contains(strings.ToLower(got), "allowall") {
			t.Errorf("%s adapter looks like a double: %q", name, got)
		}
	}
	if !strings.Contains(rep.Verifier, "JWKS") {
		t.Errorf("verifier should be the real JWKS verifier, got %q", rep.Verifier)
	}
}

// TestDefaultRuntimeDSNIsNonOwner asserts the shipped runtime DSN connects as
// the non-owner chart_app role (RLS is authoritative), NOT the migration owner.
func TestDefaultRuntimeDSNIsNonOwner(t *testing.T) {
	cfg := Load()
	if !strings.Contains(cfg.DatabaseURL, "chart_app") {
		t.Errorf("runtime DATABASE_URL must use the non-owner chart_app role, got %q", cfg.DatabaseURL)
	}
	if strings.Contains(cfg.DatabaseURL, "windrose:") || strings.Contains(cfg.DatabaseURL, "postgres:postgres") {
		t.Errorf("runtime DATABASE_URL must not be a superuser/owner role, got %q", cfg.DatabaseURL)
	}
	if !strings.Contains(cfg.MigrateDatabaseURL, "windrose") {
		t.Errorf("migrate DSN should be the owner role, got %q", cfg.MigrateDatabaseURL)
	}
}
