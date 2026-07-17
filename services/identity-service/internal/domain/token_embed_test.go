package domain

import "testing"

func TestValidateEmbedOrigins(t *testing.T) {
	ok := [][]string{
		{"https://acme.example.com"},
		{"https://portal.acme.test", "http://localhost:8899"},
		{"'self'", "https://app.acme.com:8443"},
	}
	for _, o := range ok {
		if err := ValidateEmbedOrigins(o); err != nil {
			t.Fatalf("expected %v valid, got %v", o, err)
		}
	}

	bad := map[string][]string{
		"empty":            {},
		"star":             {"*"},
		"wildcard-sub":     {"https://*.acme.com"},
		"csp-injection":    {"https://evil.com; script-src 'unsafe-inline'"},
		"comma":            {"https://a.com,https://b.com"},
		"space":            {"https://a.com https://b.com"},
		"bare-host":        {"acme.com"},
		"non-http-scheme":  {"javascript:alert(1)"},
		"has-path":         {"https://acme.com/embed"},
		"blank-entry":      {""},
	}
	for name, o := range bad {
		if err := ValidateEmbedOrigins(o); err == nil {
			t.Fatalf("%s: expected rejection of %v", name, o)
		}
	}

	// count cap
	many := make([]string, 0, maxEmbedOrigins+1)
	for i := 0; i < maxEmbedOrigins+1; i++ {
		many = append(many, "https://x.test")
	}
	if err := ValidateEmbedOrigins(many); err == nil {
		t.Fatalf("expected rejection past the origin cap")
	}
}
