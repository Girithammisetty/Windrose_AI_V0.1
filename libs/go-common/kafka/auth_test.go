package kafka

import "testing"

// Phase 3 "swappable dependency providers" for the event bus: a nil SASLConfig
// (every existing caller today) must produce exactly kafka-go's default,
// unauthenticated transport/dialer — zero behavior change — while a configured
// mechanism must actually build a real sasl.Mechanism.

func TestSASLConfig_NilMeansNoMechanism(t *testing.T) {
	var cfg *SASLConfig
	mech, err := cfg.mechanism()
	if err != nil || mech != nil {
		t.Fatalf("nil config: mech=%v err=%v, want (nil, nil)", mech, err)
	}
}

func TestSASLConfig_EmptyMechanismMeansNoMechanism(t *testing.T) {
	cfg := &SASLConfig{}
	mech, err := cfg.mechanism()
	if err != nil || mech != nil {
		t.Fatalf("empty mechanism: mech=%v err=%v, want (nil, nil)", mech, err)
	}
}

func TestSASLConfig_Plain(t *testing.T) {
	cfg := &SASLConfig{Mechanism: "PLAIN", Username: "u", Password: "p"}
	mech, err := cfg.mechanism()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if mech == nil || mech.Name() != "PLAIN" {
		t.Fatalf("expected PLAIN mechanism, got %v", mech)
	}
}

func TestSASLConfig_ScramSHA256AndSHA512(t *testing.T) {
	for _, m := range []string{"scram-sha-256", "SCRAM-SHA-512"} {
		cfg := &SASLConfig{Mechanism: m, Username: "u", Password: "p"}
		mech, err := cfg.mechanism()
		if err != nil {
			t.Fatalf("%s: unexpected error: %v", m, err)
		}
		if mech == nil {
			t.Fatalf("%s: expected a mechanism, got nil", m)
		}
	}
}

func TestSASLConfig_UnknownMechanismErrors(t *testing.T) {
	cfg := &SASLConfig{Mechanism: "oauthbearer", Username: "u", Password: "p"}
	_, err := cfg.mechanism()
	if err == nil {
		t.Fatal("expected an error for an unsupported mechanism, got nil")
	}
}

func TestBuildTransport_NilWhenUnconfigured(t *testing.T) {
	if tr := buildTransport(nil, false); tr != nil {
		t.Fatalf("expected nil transport for no SASL + no TLS, got %+v", tr)
	}
}

func TestBuildTransport_NonNilWhenTLSOnly(t *testing.T) {
	tr := buildTransport(nil, true)
	if tr == nil {
		t.Fatal("expected a non-nil transport when TLS is enabled")
	}
	if tr.TLS == nil {
		t.Error("expected TLS config to be set")
	}
	if tr.SASL != nil {
		t.Error("expected no SASL mechanism")
	}
}

func TestBuildTransport_NonNilWhenSASLConfigured(t *testing.T) {
	tr := buildTransport(&SASLConfig{Mechanism: "plain", Username: "u", Password: "p"}, false)
	if tr == nil {
		t.Fatal("expected a non-nil transport when SASL is configured")
	}
	if tr.SASL == nil {
		t.Error("expected a SASL mechanism")
	}
	if tr.TLS != nil {
		t.Error("expected no TLS config (not requested)")
	}
}

func TestBuildTransport_DegradesToNoAuthOnBadMechanism(t *testing.T) {
	// A misconfigured mechanism must not panic the constructor — it degrades to
	// no-auth (loud in logs), which the broker will then reject immediately.
	tr := buildTransport(&SASLConfig{Mechanism: "not-a-real-mechanism"}, false)
	if tr != nil {
		t.Fatalf("expected nil transport (no TLS requested, bad mechanism dropped), got %+v", tr)
	}
}

func TestBuildDialer_NilWhenUnconfigured(t *testing.T) {
	if d := buildDialer(nil, false); d != nil {
		t.Fatalf("expected nil dialer for no SASL + no TLS, got %+v", d)
	}
}

func TestBuildDialer_NonNilWhenConfigured(t *testing.T) {
	d := buildDialer(&SASLConfig{Mechanism: "scram-sha-256", Username: "u", Password: "p"}, true)
	if d == nil {
		t.Fatal("expected a non-nil dialer")
	}
	if d.SASLMechanism == nil {
		t.Error("expected a SASL mechanism on the dialer")
	}
	if d.TLS == nil {
		t.Error("expected TLS config on the dialer")
	}
}

func TestSASLFromEnv_UnsetMeansNil(t *testing.T) {
	lookup := func(string) string { return "" }
	if cfg := SASLFromEnv(lookup); cfg != nil {
		t.Fatalf("expected nil when KAFKA_SASL_MECHANISM is unset, got %+v", cfg)
	}
}

func TestSASLFromEnv_ReadsAllThreeVars(t *testing.T) {
	env := map[string]string{
		"KAFKA_SASL_MECHANISM": "scram-sha-512",
		"KAFKA_SASL_USERNAME":  "app",
		"KAFKA_SASL_PASSWORD":  "s3cr3t",
	}
	cfg := SASLFromEnv(func(k string) string { return env[k] })
	if cfg == nil {
		t.Fatal("expected a non-nil config")
	}
	if cfg.Mechanism != "scram-sha-512" || cfg.Username != "app" || cfg.Password != "s3cr3t" {
		t.Errorf("unexpected config: %+v", cfg)
	}
}

func TestTLSFromEnv(t *testing.T) {
	cases := map[string]bool{"": false, "0": false, "false": false, "1": true, "true": true, "YES": true}
	for v, want := range cases {
		got := TLSFromEnv(func(string) string { return v })
		if got != want {
			t.Errorf("TLSFromEnv(%q) = %v, want %v", v, got, want)
		}
	}
}
