package authz

import (
	"strings"
	"testing"
)

// canonicalVerbs is rbac's closed verb set (RBC-FR-022), mirrored verbatim from
// rbac-service/internal/domain/catalog.go `AllVerbs`. rbac's RegisterActions
// rejects the ENTIRE manifest batch if ANY action carries a verb outside this
// set — which then makes every action unknown_action (deny-before-admin) at
// decide time. This list is the contract; keep it identical to rbac's.
var canonicalVerbs = map[string]bool{
	"read": true, "list": true, "create": true, "update": true,
	"delete": true, "execute": true, "assign": true, "approve": true,
	"admin": true, "export": true, "share": true,
}

// parseActionVerb replicates rbac domain.ParseAction validation: an action must
// be exactly `<service>.<resource>.<verb>` with a canonical verb.
func parseActionVerb(action string) (string, bool) {
	parts := strings.Split(action, ".")
	if len(parts) != 3 || parts[0] == "" || parts[1] == "" || parts[2] == "" {
		return "", false
	}
	if !canonicalVerbs[parts[2]] {
		return parts[2], false
	}
	return parts[2], true
}

// TestManifestVerbsAreCanonical is the drift guard: EVERY registered action
// must parse as `tool.<resource>.<verb>` with a canonical verb, so rbac accepts
// the whole batch (nothing becomes unknown_action collateral).
func TestManifestVerbsAreCanonical(t *testing.T) {
	if len(Manifest()) == 0 {
		t.Fatal("manifest is empty")
	}
	for _, e := range Manifest() {
		if !strings.HasPrefix(e.Action, "tool.") {
			t.Errorf("action %q must be namespaced under tool.", e.Action)
		}
		verb, ok := parseActionVerb(e.Action)
		if !ok {
			t.Errorf("action %q uses non-canonical verb %q; rbac would reject the whole batch", e.Action, verb)
		}
	}
}

// TestNoBannedActions pins the specific regression this package fixed: the
// two-segment "tool.invoke" is not an action (it fails the
// <service>.<resource>.<verb> grammar) and must never reappear in the manifest
// or as the pipeline's OPA action.
func TestNoBannedActions(t *testing.T) {
	for _, e := range Manifest() {
		if e.Action == "tool.invoke" {
			t.Errorf("action %q reintroduces the non-canonical two-segment action", e.Action)
		}
		if strings.Count(e.Action, ".") != 2 {
			t.Errorf("action %q is not <service>.<resource>.<verb>", e.Action)
		}
	}
}

// TestExecuteRegistered pins that the gateway pipeline's action is in the
// manifest (guarded == registered for the data plane).
func TestExecuteRegistered(t *testing.T) {
	for _, e := range Manifest() {
		if e.Action == ActionToolExecute {
			return
		}
	}
	t.Fatalf("pipeline action %q missing from Manifest()", ActionToolExecute)
}
