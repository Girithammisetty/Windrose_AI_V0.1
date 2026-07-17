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

// TestManifestVerbsAreCanonical is the drift guard that would have caught the
// archive/link regression: EVERY registered action must parse and use a
// canonical verb, so rbac accepts the whole batch (nothing becomes
// unknown_action collateral).
func TestManifestVerbsAreCanonical(t *testing.T) {
	if len(Manifest()) == 0 {
		t.Fatal("manifest is empty")
	}
	for _, e := range Manifest() {
		if !strings.HasPrefix(e.Action, "chart.") {
			t.Errorf("action %q must be namespaced under chart.", e.Action)
		}
		verb, ok := parseActionVerb(e.Action)
		if !ok {
			t.Errorf("action %q uses non-canonical verb %q; rbac would reject the whole batch", e.Action, verb)
		}
	}
}

// TestNoBannedVerbs pins the specific verbs that broke verification.
func TestNoBannedVerbs(t *testing.T) {
	for _, e := range Manifest() {
		if strings.HasSuffix(e.Action, ".archive") || strings.HasSuffix(e.Action, ".link") ||
			strings.HasSuffix(e.Action, ".restore") || strings.HasSuffix(e.Action, ".unlink") {
			t.Errorf("action %q reintroduces a non-canonical verb", e.Action)
		}
	}
}
