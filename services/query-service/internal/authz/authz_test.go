package authz

import (
	"strings"
	"testing"
)

// canonicalVerbs is rbac's closed verb set (RBC-FR-022), mirrored verbatim
// from rbac-service/internal/domain/catalog.go `AllVerbs`. rbac's
// RegisterActions rejects the ENTIRE manifest batch if ANY action carries a
// verb outside this set — which then makes every action unknown_action
// (deny-before-admin) at decide time. This list is the contract; keep it
// identical to rbac's.
var canonicalVerbs = map[string]bool{
	"read": true, "list": true, "create": true, "update": true,
	"delete": true, "execute": true, "assign": true, "approve": true,
	"admin": true, "export": true, "share": true,
}

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
// must parse as query.<resource>.<verb> with a canonical verb, so rbac
// accepts the whole batch (nothing becomes unknown_action collateral).
func TestManifestVerbsAreCanonical(t *testing.T) {
	if len(Manifest()) == 0 {
		t.Fatal("manifest is empty")
	}
	for _, e := range Manifest() {
		if !strings.HasPrefix(e.Action, "query.") {
			t.Errorf("action %q must be namespaced under query.", e.Action)
		}
		verb, ok := parseActionVerb(e.Action)
		if !ok {
			t.Errorf("action %q uses non-canonical verb %q; rbac would reject the whole batch", e.Action, verb)
		}
	}
}

// TestNoBannedVerbs pins the specific non-canonical verbs this service used
// to guard with (cancel/write): they made the routes forever-403 because no
// role could ever be granted them.
func TestNoBannedVerbs(t *testing.T) {
	for _, e := range Manifest() {
		if strings.HasSuffix(e.Action, ".cancel") || strings.HasSuffix(e.Action, ".write") ||
			strings.HasSuffix(e.Action, ".run") {
			t.Errorf("action %q reintroduces a non-canonical verb", e.Action)
		}
	}
}

// TestGuardedEqualsRegistered asserts guarded == registered: every action
// constant the route guards use appears in Manifest() and vice versa, so a
// new guard cannot ship without being registered (RBC-FR-022).
func TestGuardedEqualsRegistered(t *testing.T) {
	guarded := map[string]bool{
		ActionQueryRead: true, ActionQueryCreate: true, ActionQueryUpdate: true,
		ActionQueryDelete: true, ActionExecRun: true, ActionExecRead: true,
		ActionExecExport: true, ActionStatsRead: true, ActionLimitsRead: true,
		ActionLimitsUpdate: true,
	}
	registered := map[string]bool{}
	for _, e := range Manifest() {
		if registered[e.Action] {
			t.Errorf("action %q registered twice", e.Action)
		}
		registered[e.Action] = true
	}
	for a := range guarded {
		if !registered[a] {
			t.Errorf("guarded action %q is not in the registration manifest", a)
		}
	}
	for a := range registered {
		if !guarded[a] {
			t.Errorf("registered action %q is not guarded by any route", a)
		}
	}
}
