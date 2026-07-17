package api

import (
	"strings"
	"testing"

	"github.com/windrose-ai/usage-service/internal/authz"
)

// canonicalVerbs is rbac's closed action-grammar verb set (RBC-FR-022,
// MASTER-FR-016). Every action usage-service authorizes against must end in one
// of these or rbac would reject the manifest registration.
var canonicalVerbs = map[string]bool{
	"read": true, "list": true, "create": true, "update": true, "delete": true,
	"execute": true, "assign": true, "approve": true, "admin": true, "export": true, "share": true,
}

// TestActionCatalogDrift is the catalog drift guard: every action bound to a
// route must be registered in the rbac manifest, every manifest action must be
// route-bound (no registered-but-unguarded actions), and every action must use
// a canonical <service>.<resource>.<verb> grammar with a known verb.
func TestActionCatalogDrift(t *testing.T) {
	srv := &Server{Verifier: &Verifier{}}
	_ = srv.Router() // records guarded actions via RequireAction

	manifest := map[string]bool{}
	for _, e := range authz.Manifest() {
		manifest[e.Action] = true
		parts := strings.Split(e.Action, ".")
		if len(parts) != 3 || parts[0] != "usage" {
			t.Fatalf("action %q not <usage>.<resource>.<verb>", e.Action)
		}
		if !canonicalVerbs[parts[2]] {
			t.Fatalf("action %q uses non-canonical verb %q", e.Action, parts[2])
		}
	}

	guarded := map[string]bool{}
	for _, a := range srv.GuardedActions() {
		guarded[a] = true
		if !manifest[a] {
			t.Fatalf("route guards action %q which is NOT in the rbac manifest", a)
		}
	}
	// No registered-but-unguarded actions.
	for a := range manifest {
		if !guarded[a] {
			t.Fatalf("manifest action %q is registered but bound to no route", a)
		}
	}
	if len(guarded) == 0 {
		t.Fatal("no guarded actions recorded")
	}
}
