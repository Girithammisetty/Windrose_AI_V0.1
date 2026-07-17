package templates

import "testing"

// TestAC08_WhitelistValidation proves a template referencing a variable outside
// the event's whitelist is rejected, naming the offending variable (AC-8).
func TestAC08_WhitelistValidation(t *testing.T) {
	whitelist := map[string]string{"CaseNumber": "int", "DeepLink": "url"}

	ok, err := ValidateWhitelist(whitelist, "Case #{{.CaseNumber}} — {{.DeepLink}}")
	if err != nil {
		t.Fatalf("valid template errored: %v", err)
	}
	if len(ok) != 0 {
		t.Fatalf("expected no offenders, got %v", ok)
	}

	offenders, err := ValidateWhitelist(whitelist, "Hi {{.SecretPII}} on case {{.CaseNumber}}")
	if err != nil {
		t.Fatalf("parse error: %v", err)
	}
	if len(offenders) != 1 || offenders[0] != "SecretPII" {
		t.Fatalf("expected [SecretPII], got %v", offenders)
	}
}

func TestRender(t *testing.T) {
	r, err := Render("Case #{{.CaseNumber}}", "<p>{{.CaseNumber}}</p>", "Case {{.CaseNumber}} {{.DeepLink}}",
		map[string]any{"CaseNumber": 42, "DeepLink": "https://x/y"})
	if err != nil {
		t.Fatalf("render: %v", err)
	}
	if r.Subject != "Case #42" || r.HTML != "<p>42</p>" {
		t.Fatalf("unexpected render: %+v", r)
	}
}

// A missing variable must error (so the caller falls back, never leaking {{.}}).
func TestRenderMissingKeyErrors(t *testing.T) {
	if _, err := Render("{{.Absent}}", "", "", map[string]any{}); err == nil {
		t.Fatal("missing key should error")
	}
}
