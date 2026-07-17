package integration

import (
	"context"
	"encoding/json"
	"net"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/notification-service/internal/authz"
	"github.com/windrose-ai/notification-service/internal/register"
)

// rbac's canonical verb set + action grammar, replicated (rbac's ParseAction is
// in an internal package of another module and cannot be imported). This test
// exercises the SAME validation rbac's RegisterActions applies, so a
// non-canonical verb here fails exactly as it would in a real deployment.
var rbacCanonicalVerbs = map[string]bool{
	"read": true, "list": true, "create": true, "update": true, "delete": true,
	"execute": true, "assign": true, "approve": true, "admin": true, "export": true, "share": true,
}
var rbacActionRe = regexp.MustCompile(`^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`)

func rbacParseAction(a string) error {
	if !rbacActionRe.MatchString(a) {
		return errStr("bad action name")
	}
	if !rbacCanonicalVerbs[a[strings.LastIndex(a, ".")+1:]] {
		return errStr("non-canonical verb")
	}
	return nil
}

type errStr string

func (e errStr) Error() string { return string(e) }

// TestAC_OPAAuthzRealRegisterPath drives notification-service's ACTUAL guarded
// actions through the real register→catalog→OPA path (MASTER-FR-012): the
// manifest is validated exactly as rbac's RegisterActions would (real
// ParseAction), the resulting catalog + a granted admin's projection are seeded
// in Redis, and the real OPA sidecar decides. Every guarded action must be
// ALLOWED for the granted admin; a non-canonical action (which rbac would have
// rejected, leaving it out of the catalog) must be DENIED. Skips when OPA is
// unreachable.
func TestAC_OPAAuthzRealRegisterPath(t *testing.T) {
	h := requireHarness(t)
	opaURL := os.Getenv("OPA_URL")
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	if c, err := net.DialTimeout("tcp", "localhost:8281", 2*time.Second); err != nil {
		t.Skipf("OPA sidecar not reachable on :8281: %v", err)
	} else {
		_ = c.Close()
	}
	ctx := context.Background()
	tenant := uuid.New().String()
	admin := "admin-" + uuid.NewString()[:8]

	// Simulate rbac RegisterActions: validate every manifest action with the
	// real ParseAction rules; any failure would zero the catalog. Prove none fail.
	catalog := map[string]bool{}
	for _, e := range register.Manifest() {
		a := e["action"].(string)
		if err := rbacParseAction(a); err != nil {
			t.Fatalf("manifest action %q would be REJECTED by rbac ParseAction (%v) → catalog zeroed → all routes 403", a, err)
		}
		catalog[a] = e["workspace_scoped"].(bool)
	}
	// Seed the catalog OPA consumes (rbac RBC-FR-040 key scheme).
	catBytes, _ := json.Marshal(map[string]any{"actions": catalog})
	if err := h.rc.Set(ctx, "perm:catalog:actions", string(catBytes), 0); err != nil {
		t.Fatal(err)
	}
	// Grant the admin every registered action (tenant-scoped).
	grant, _ := json.Marshal(map[string]any{"actions": register.Actions()})
	if err := h.rc.Set(ctx, "perm:"+tenant+":"+admin+":actions", string(grant), 0); err != nil {
		t.Fatal(err)
	}

	az := authz.NewOPAClient(opaURL, h.redisAddr)

	// Every guarded action must be allowed for the granted admin under real OPA.
	for _, action := range register.Actions() {
		if !az.Allow(ctx, authz.Input{Subject: authz.Subject{ID: admin, Typ: "user"}, Action: action, Tenant: tenant}) {
			t.Errorf("granted admin DENIED for guarded action %q (real OPA)", action)
		}
	}

	// A non-canonical action rbac would never have registered → action_known
	// false → deny, even for the admin.
	if az.Allow(ctx, authz.Input{Subject: authz.Subject{ID: admin, Typ: "user"}, Action: "notification.rule.manage", Tenant: tenant}) {
		t.Fatal("non-canonical action notification.rule.manage must be DENIED (not in catalog)")
	}
	// An ungranted user is denied a real guarded action.
	other := "nogrant-" + uuid.NewString()[:8]
	if az.Allow(ctx, authz.Input{Subject: authz.Subject{ID: other, Typ: "user"}, Action: authz.ActionRuleCreate, Tenant: tenant}) {
		t.Fatal("ungranted user must be denied notification.rule.create")
	}
}
