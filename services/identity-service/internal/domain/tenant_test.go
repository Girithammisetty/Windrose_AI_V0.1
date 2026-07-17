package domain

import (
	"strings"
	"testing"
	"time"
)

// TestTenantTransitionMatrix exercises every (from, to) pair of the state
// machine (IDN-FR-003): allowed pairs succeed, everything else is a 409.
func TestTenantTransitionMatrix(t *testing.T) {
	allowed := map[[2]TenantStatus]bool{
		{TenantDraft, TenantProvisioning}:           true,
		{TenantProvisioning, TenantActive}:          true,
		{TenantProvisioning, TenantProvisionFailed}: true,
		{TenantProvisionFailed, TenantProvisioning}: true,
		{TenantProvisionFailed, TenantDeleting}:     true,
		{TenantActive, TenantSuspended}:             true,
		{TenantSuspended, TenantActive}:             true,
		{TenantActive, TenantDeleting}:              true,
		{TenantSuspended, TenantDeleting}:           true,
		{TenantDeleting, TenantDeleted}:             true,
	}
	now := time.Now()
	for _, from := range AllTenantStatuses {
		for _, to := range AllTenantStatuses {
			tn := &Tenant{Status: from}
			err := tn.Transition(to, now)
			want := allowed[[2]TenantStatus{from, to}]
			if want && err != nil {
				t.Errorf("%s -> %s: expected allowed, got %v", from, to, err)
			}
			if !want {
				de, ok := AsError(err)
				if !ok || de.HTTP != 409 || de.Code != CodeConflict {
					t.Errorf("%s -> %s: expected 409 CONFLICT, got %v", from, to, err)
				}
				if tn.Status != from {
					t.Errorf("%s -> %s: status mutated on rejected transition", from, to)
				}
			}
		}
	}
	if len(allowed) != 10 {
		t.Fatalf("matrix drift: expected 10 allowed transitions, table has %d", len(allowed))
	}
}

func TestNormalizeTenantName(t *testing.T) {
	cases := []struct {
		in      string
		wantErr bool
		name    string
		schema  string
	}{
		{"Acme-Corp", false, "acme-corp", "acme_corp"},
		{"acme", false, "acme", "acme"},
		{"a1-b2-c3", false, "a1-b2-c3", "a1_b2_c3"},
		{"ab", true, "", ""},                    // too short
		{"1abc", true, "", ""},                  // must start with letter
		{"has_underscore", true, "", ""},        // invalid char
		{"admin", true, "", ""},                 // reserved
		{"api", true, "", ""},                   // reserved
		{"cell-eu-1", true, "", ""},             // reserved via extra list
		{strings.Repeat("a", 40), true, "", ""}, // too long
		{strings.Repeat("a", 39), false, strings.Repeat("a", 39), strings.Repeat("a", 39)},
	}
	for _, c := range cases {
		name, sub, ns, schema, err := NormalizeTenantName(c.in, []string{"cell-eu-1"})
		if c.wantErr {
			if err == nil {
				t.Errorf("%q: expected error", c.in)
			} else if de, _ := AsError(err); de.Code != CodeValidationFailed {
				t.Errorf("%q: expected VALIDATION_FAILED, got %s", c.in, de.Code)
			}
			continue
		}
		if err != nil {
			t.Errorf("%q: unexpected error %v", c.in, err)
			continue
		}
		if name != c.name || sub != c.name || ns != c.name || schema != c.schema {
			t.Errorf("%q: got (%s,%s,%s,%s) want name=%s schema=%s", c.in, name, sub, ns, schema, c.name, c.schema)
		}
	}
}

func TestModuleGraphResolve(t *testing.T) {
	g := DefaultModuleGraph()
	got, err := g.Resolve([]string{"infer"})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{"data": true, "config": true, "ui": true, "train": true, "infer": true}
	if len(got) != len(want) {
		t.Fatalf("resolve(infer) = %v, want keys %v", got, want)
	}
	for _, m := range got {
		if !want[m] {
			t.Errorf("unexpected module %s", m)
		}
	}
	if _, err := g.Resolve([]string{"bogus"}); err == nil {
		t.Error("expected error for unknown module")
	}
}

func TestValidateEmail(t *testing.T) {
	if _, err := ValidateEmail("Owner@Example.com"); err != nil {
		t.Errorf("valid email rejected: %v", err)
	}
	got, _ := ValidateEmail("Owner@Example.com")
	if got != "owner@example.com" {
		t.Errorf("email not lowercased: %s", got)
	}
	for _, bad := range []string{"", "not-an-email", "a b@c.com", "Owner <o@e.com>"} {
		if _, err := ValidateEmail(bad); err == nil {
			t.Errorf("%q: expected validation error", bad)
		}
	}
}

func TestAPIKeyRoundtrip(t *testing.T) {
	secret, err := NewAPIKeySecret()
	if err != nil {
		t.Fatal(err)
	}
	hash, err := HashSecret(secret)
	if err != nil {
		t.Fatal(err)
	}
	if !VerifySecret(secret, hash) {
		t.Fatal("argon2id verify failed for correct secret")
	}
	if VerifySecret("wrong", hash) {
		t.Fatal("argon2id verify passed for wrong secret")
	}
	sa := &ServiceAccount{SecretHash: hash}
	if !sa.VerifyPresentedSecret(secret, time.Now()) {
		t.Fatal("VerifyPresentedSecret failed")
	}
	// Rotation overlap: old secret works until the overlap deadline.
	newSecret, _ := NewAPIKeySecret()
	newHash, _ := HashSecret(newSecret)
	now := time.Now()
	exp := now.Add(RotationOverlap)
	old := sa.SecretHash
	sa.OldSecretHash = &old
	sa.OldSecretExpiresAt = &exp
	sa.SecretHash = newHash
	if !sa.VerifyPresentedSecret(secret, now.Add(time.Minute)) {
		t.Fatal("old secret should verify inside overlap window")
	}
	if sa.VerifyPresentedSecret(secret, now.Add(RotationOverlap+time.Second)) {
		t.Fatal("old secret should fail after overlap window")
	}
}

func TestRateLimiterSlidingWindow(t *testing.T) {
	l := NewSlidingWindowLimiter(3, time.Minute)
	now := time.Now()
	for i := 0; i < 3; i++ {
		if ok, _ := l.Allow("k", now.Add(time.Duration(i)*time.Second)); !ok {
			t.Fatalf("call %d should be allowed", i)
		}
	}
	ok, retry := l.Allow("k", now.Add(3*time.Second))
	if ok {
		t.Fatal("4th call should be limited")
	}
	if retry < 1 {
		t.Fatalf("retry-after should be >=1, got %d", retry)
	}
	// After the window slides, calls are allowed again.
	if ok, _ := l.Allow("k", now.Add(61*time.Second)); !ok {
		t.Fatal("call after window should be allowed")
	}
	// Other keys are unaffected.
	if ok, _ := l.Allow("other", now); !ok {
		t.Fatal("other key should be allowed")
	}
}

func TestStepFailedError(t *testing.T) {
	inner := EValidation("boom")
	sf := &StepFailedError{StepIndex: 2, StepName: "ProvisionInfra", Err: inner}
	if got := sf.Error(); !strings.Contains(got, "ProvisionInfra") {
		t.Fatalf("Error() = %q", got)
	}
	if sf.Unwrap() != inner {
		t.Fatal("Unwrap did not return the wrapped error")
	}
	if de, ok := AsError(sf.Unwrap()); !ok || de.Code != CodeValidationFailed {
		t.Fatal("wrapped domain error not recoverable via AsError")
	}
}

func TestInvitationLifecycle(t *testing.T) {
	tok, hash, err := NewInvitationToken()
	if err != nil {
		t.Fatal(err)
	}
	if HashInvitationToken(tok) != hash {
		t.Fatal("hash mismatch")
	}
	now := time.Now()
	inv := &Invitation{ExpiresAt: now.Add(InvitationTTL)}
	if err := inv.Usable(now); err != nil {
		t.Fatalf("fresh invitation should be usable: %v", err)
	}
	if err := inv.Usable(now.Add(InvitationTTL + time.Hour)); err == nil {
		t.Fatal("expired invitation should not be usable")
	} else if de, _ := AsError(err); de.HTTP != 410 {
		t.Fatalf("expired invitation: want 410, got %d", de.HTTP)
	}
	inv.InvalidatedAt = &now
	if err := inv.Usable(now); err == nil {
		t.Fatal("invalidated invitation should not be usable")
	}
}
