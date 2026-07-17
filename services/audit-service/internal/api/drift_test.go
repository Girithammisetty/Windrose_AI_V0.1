package api

import (
	"os"
	"regexp"
	"strings"
	"testing"

	"github.com/windrose-ai/audit-service/internal/authz"
)

// canonicalVerbs is rbac's canonical action-verb set (MASTER-FR-016). Every
// action audit-service guards must end in one of these.
var canonicalVerbs = map[string]bool{
	"read": true, "list": true, "create": true, "update": true, "delete": true,
	"execute": true, "assign": true, "approve": true, "admin": true,
	"export": true, "share": true,
}

// constByName maps the authz const identifiers referenced in the router to their
// values. If a route references a NEW const not listed here, the regex match
// won't resolve and the test fails — forcing this map (and the manifest) to be
// updated in lockstep with the routes.
var constByName = map[string]string{
	"ActionEventRead":      authz.ActionEventRead,
	"ActionEventExport":    authz.ActionEventExport,
	"ActionExportRead":     authz.ActionExportRead,
	"ActionChainVerify":    authz.ActionChainVerify,
	"ActionComplianceRead": authz.ActionComplianceRead,
	"ActionDLQRedrive":     authz.ActionDLQRedrive,
}

// TestActionCatalogNoDrift scans the router source and asserts that every guarded
// action (1) resolves to a known constant, (2) is present in the manifest
// registered with rbac (`action_known`), and (3) ends in a canonical verb. It
// also asserts the manifest carries no unbound (registered-but-unused) action.
func TestActionCatalogNoDrift(t *testing.T) {
	src, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatalf("read router source: %v", err)
	}
	re := regexp.MustCompile(`RequireAction\(authz\.(\w+)\)`)
	matches := re.FindAllStringSubmatch(string(src), -1)
	if len(matches) == 0 {
		t.Fatal("no guarded routes found; the drift scan is not seeing the router")
	}

	manifest := map[string]bool{}
	for _, e := range authz.Manifest() {
		manifest[e.Action] = true
	}

	usedActions := map[string]bool{}
	for _, m := range matches {
		name := m[1]
		val, ok := constByName[name]
		if !ok {
			t.Fatalf("route guards on authz.%s which is not in constByName — update the drift test AND the manifest", name)
		}
		usedActions[val] = true
		if !manifest[val] {
			t.Errorf("guarded action %q (%s) is NOT in the registered manifest — OPA would deny it as unknown_action", val, name)
		}
		verb := val[strings.LastIndexByte(val, '.')+1:]
		if !canonicalVerbs[verb] {
			t.Errorf("action %q uses non-canonical verb %q (allowed: read/list/create/update/delete/execute/assign/approve/admin/export/share)", val, verb)
		}
	}

	// Every registered action must be bound to a route (no dead entries such as a
	// registered-but-unbound export action).
	for action := range manifest {
		if !usedActions[action] {
			t.Errorf("manifest action %q is registered but bound to no route — bind it or remove it", action)
		}
	}
}
