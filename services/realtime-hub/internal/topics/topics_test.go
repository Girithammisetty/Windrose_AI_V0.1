package topics

import "testing"

func TestParse_SchemesAndGrammar(t *testing.T) {
	cases := []struct {
		raw     string
		wantErr bool
		scheme  Scheme
		ident   string
	}{
		{"run-status:wr:t-42:pipeline:run/pr-881", false, SchemeRunStatus, "wr:t-42:pipeline:run/pr-881"},
		{"chat:sess-999", false, SchemeChat, "sess-999"},
		{"notifications:u-7", false, SchemeNotifications, "u-7"},
		{"proposal:pp-3", false, SchemeProposal, "pp-3"},
		{"bogus:x", true, "", ""},          // unknown scheme -> INVALID_TOPIC
		{"run-status:pr-881", true, "", ""}, // run-status ident must be a URN
		{"chat:", true, "", ""},            // empty ident
		{"", true, "", ""},
		{"noscheme", true, "", ""},
	}
	for _, c := range cases {
		got, err := Parse(c.raw)
		if c.wantErr {
			if err == nil {
				t.Errorf("Parse(%q) expected error", c.raw)
			}
			continue
		}
		if err != nil {
			t.Errorf("Parse(%q) unexpected error: %v", c.raw, err)
			continue
		}
		if got.Scheme != c.scheme || got.Ident != c.ident {
			t.Errorf("Parse(%q) = %v/%q want %v/%q", c.raw, got.Scheme, got.Ident, c.scheme, c.ident)
		}
	}
}

func TestQoS_ChatDisconnectsOthersGap(t *testing.T) {
	chat, _ := Parse("chat:s1")
	if chat.QoS().Overflow != OverflowDisconnect {
		t.Fatal("chat must disconnect on overflow (RTH-FR-034)")
	}
	rs, _ := Parse("run-status:wr:t:svc:res/1")
	if rs.QoS().Overflow != OverflowGap {
		t.Fatal("run-status must gap on overflow (RTH-FR-034)")
	}
}

func TestKey_TenantScoped(t *testing.T) {
	// BR-3: subscription key always prefixes the JWT tenant, never the client's.
	if got := Key("t-42", "notifications:u-7"); got != "t-42/notifications:u-7" {
		t.Fatalf("Key = %q", got)
	}
}

func TestURNTenant(t *testing.T) {
	if got := URNTenant("wr:t-42:pipeline:run/pr-1"); got != "t-42" {
		t.Fatalf("URNTenant = %q", got)
	}
	if got := URNTenant("not-a-urn"); got != "" {
		t.Fatalf("URNTenant(non-urn) = %q", got)
	}
}
