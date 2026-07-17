package authz

import (
	"context"
	"testing"

	"github.com/windrose-ai/realtime-hub/internal/topics"
)

// Static is a unit-test double: it allows every topic except those whose Raw
// string is listed in Deny, and optionally enforces the same structural
// notifications/chat rules the real authorizer applies. Test-only: it lives in
// _test.go so it can never be compiled into or reached from cmd/server
// (CONVENTIONS "no stubs in the runtime").
type Static struct {
	Deny     map[string]bool   // topic.Raw -> deny
	Sessions map[string]string // session id -> owner sub (for chat checks)
}

// Authorize implements Authorizer for tests.
func (s Static) Authorize(_ context.Context, req Request) Decision {
	if s.Deny[req.Topic.Raw] {
		return Decision{Allow: false, Reason: "deny_default"}
	}
	switch req.Topic.Scheme {
	case topics.SchemeNotifications:
		if req.Subject.isService() || req.Subject.EffectiveUser() == req.Topic.Ident {
			return Decision{Allow: true, Reason: "allowed"}
		}
		return Decision{Allow: false, Reason: "not_owner"}
	case topics.SchemeChat:
		if req.Subject.isService() {
			return Decision{Allow: true, Reason: "allowed"}
		}
		if owner, ok := s.Sessions[req.Topic.Ident]; ok && owner == req.Subject.EffectiveUser() {
			return Decision{Allow: true, Reason: "allowed"}
		}
		return Decision{Allow: false, Reason: "not_session_owner"}
	default:
		return Decision{Allow: true, Reason: "allowed"}
	}
}

func mustTopic(t *testing.T, raw string) topics.Topic {
	t.Helper()
	tp, err := topics.Parse(raw)
	if err != nil {
		t.Fatalf("parse %q: %v", raw, err)
	}
	return tp
}

// TestStatic_StructuralRules is the unit-tier authz matrix: it mirrors the
// structural notifications/chat rules (RTH-FR-003 / BR-4) the real OPA
// authorizer applies, plus a deny list for OPA-decided topics.
func TestStatic_StructuralRules(t *testing.T) {
	az := Static{
		Deny:     map[string]bool{"run-status:wr:t:svc:res/denied": true},
		Sessions: map[string]string{"sess-mine": "u1"},
	}
	ctx := context.Background()
	sub := Subject{ID: "u1", Typ: "user"}

	cases := []struct {
		name  string
		req   Request
		allow bool
	}{
		{"notifications_own", Request{Subject: sub, Tenant: "t", Topic: mustTopic(t, "notifications:u1")}, true},
		{"notifications_other_denied", Request{Subject: sub, Tenant: "t", Topic: mustTopic(t, "notifications:u2")}, false},
		{"chat_owned", Request{Subject: sub, Tenant: "t", Topic: mustTopic(t, "chat:sess-mine")}, true},
		{"chat_not_owner_denied", Request{Subject: sub, Tenant: "t", Topic: mustTopic(t, "chat:sess-other")}, false},
		{"run_status_allowed", Request{Subject: sub, Tenant: "t", Topic: mustTopic(t, "run-status:wr:t:svc:res/ok")}, true},
		{"run_status_denylisted", Request{Subject: sub, Tenant: "t", Topic: mustTopic(t, "run-status:wr:t:svc:res/denied")}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := az.Authorize(ctx, c.req).Allow; got != c.allow {
				t.Fatalf("allow=%v want %v", got, c.allow)
			}
		})
	}
}

// TestStatic_ServiceCanReadAnyUser verifies a service principal bypasses the
// owner-only notifications rule (RTH-FR-003).
func TestStatic_ServiceCanReadAnyUser(t *testing.T) {
	az := Static{}
	svc := Subject{ID: "notification-service", Typ: "service"}
	if !az.Authorize(context.Background(), Request{Subject: svc, Tenant: "t", Topic: mustTopic(t, "notifications:u9")}).Allow {
		t.Fatal("service principal must read any user's notifications")
	}
}

// TestSubject_EffectiveUser covers OBO resolution (MASTER-FR-015).
func TestSubject_EffectiveUser(t *testing.T) {
	obo := Subject{ID: "agent-1", Typ: "agent_obo", OboSub: "u-real"}
	if obo.EffectiveUser() != "u-real" {
		t.Fatalf("OBO effective user = %q", obo.EffectiveUser())
	}
}
