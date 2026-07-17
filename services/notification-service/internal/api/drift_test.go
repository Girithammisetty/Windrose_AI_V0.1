package api

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"

	"github.com/windrose-ai/notification-service/internal/authz"
	"github.com/windrose-ai/notification-service/internal/register"
)

// canonicalVerbs replicates rbac-service's closed verb set (RBC-FR-022,
// domain.AllVerbs). It cannot be imported (rbac's internal package), so it is
// mirrored here and this test is the guard that keeps the two in sync: any
// registered action whose verb is not in this set would be rejected by rbac's
// ParseAction, zeroing the catalog and 403-ing every guarded route.
var canonicalVerbs = map[string]bool{
	"read": true, "list": true, "create": true, "update": true, "delete": true,
	"execute": true, "assign": true, "approve": true, "admin": true, "export": true, "share": true,
}

var actionNameRe = regexp.MustCompile(`^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`)

// actionConstByName maps every authz.Action* identifier to its value so the
// route-source scan can resolve RequireAction(authz.X) → the action string.
var actionConstByName = map[string]string{
	"ActionRuleCreate": authz.ActionRuleCreate, "ActionRuleRead": authz.ActionRuleRead,
	"ActionRuleUpdate": authz.ActionRuleUpdate, "ActionRuleDelete": authz.ActionRuleDelete,
	"ActionWebhookCreate": authz.ActionWebhookCreate, "ActionWebhookRead": authz.ActionWebhookRead,
	"ActionWebhookUpdate": authz.ActionWebhookUpdate, "ActionWebhookDelete": authz.ActionWebhookDelete,
	"ActionWebhookExecute": authz.ActionWebhookExecute,
	"ActionTemplateCreate": authz.ActionTemplateCreate, "ActionTemplateRead": authz.ActionTemplateRead,
	"ActionTemplateUpdate": authz.ActionTemplateUpdate,
	"ActionPrefRead":       authz.ActionPrefRead, "ActionPrefUpdate": authz.ActionPrefUpdate,
	"ActionInboxRead":         authz.ActionInboxRead,
	"ActionAdminRead":         authz.ActionAdminRead,
	"ActionSuppressionDelete": authz.ActionSuppressionDelete,
	"ActionReportCreate":      authz.ActionReportCreate,
	"ActionReportRead":        authz.ActionReportRead,
	"ActionReportUpdate":      authz.ActionReportUpdate,
	"ActionReportDelete":      authz.ActionReportDelete,
}

func manifestSet() map[string]bool {
	out := map[string]bool{}
	for _, e := range register.Manifest() {
		out[e["action"].(string)] = true
	}
	return out
}

// guardedActions scans server.go source for every RequireAction(authz.X) and
// resolves it to the action string.
func guardedActions(t *testing.T) map[string]bool {
	t.Helper()
	_, thisFile, _, _ := runtime.Caller(0)
	src, err := os.ReadFile(filepath.Join(filepath.Dir(thisFile), "server.go"))
	if err != nil {
		t.Fatalf("read server.go: %v", err)
	}
	re := regexp.MustCompile(`RequireAction\(authz\.(\w+)\)`)
	out := map[string]bool{}
	for _, m := range re.FindAllStringSubmatch(string(src), -1) {
		name := m[1]
		val, ok := actionConstByName[name]
		if !ok {
			t.Fatalf("server.go guards authz.%s but drift test has no mapping for it (add it)", name)
		}
		out[val] = true
	}
	if len(out) == 0 {
		t.Fatal("no RequireAction guards found in server.go — scan broken")
	}
	return out
}

// TestDrift_EveryRegisteredVerbIsCanonical proves no registered action uses a
// verb rbac would reject (the "manage" class of bug).
func TestDrift_EveryRegisteredVerbIsCanonical(t *testing.T) {
	for _, e := range register.Manifest() {
		a := e["action"].(string)
		if !actionNameRe.MatchString(a) {
			t.Errorf("action %q does not match <service>.<resource>.<verb>", a)
			continue
		}
		verb := a[strings.LastIndex(a, ".")+1:]
		if !canonicalVerbs[verb] {
			t.Errorf("action %q uses NON-canonical verb %q — rbac ParseAction would reject the batch", a, verb)
		}
		if parts := strings.SplitN(a, ".", 3); parts[0] != "notification" {
			t.Errorf("action %q must be in the notification service namespace", a)
		}
	}
}

// TestDrift_GuardedEqualsRegistered proves every guarded action is registered
// and every registered action is actually guarded (no drift either way).
func TestDrift_GuardedEqualsRegistered(t *testing.T) {
	guarded := guardedActions(t)
	registered := manifestSet()

	for a := range guarded {
		if !registered[a] {
			t.Errorf("route guards %q but it is NOT in the registered manifest → action_known=false → 403", a)
		}
	}
	for a := range registered {
		if !guarded[a] {
			t.Errorf("manifest registers %q but no route guards it (registered-but-unbound)", a)
		}
	}
}
