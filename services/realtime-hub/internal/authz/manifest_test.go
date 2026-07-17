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

// TestManifestVerbsAreCanonical is the drift guard for the connect/connections
// regression: EVERY registered action must parse as
// `realtime.<resource>.<verb>` with a canonical verb, so rbac accepts the
// whole batch (nothing becomes unknown_action collateral).
func TestManifestVerbsAreCanonical(t *testing.T) {
	if len(Manifest()) == 0 {
		t.Fatal("manifest is empty")
	}
	for _, e := range Manifest() {
		if !strings.HasPrefix(e.Action, "realtime.") {
			t.Errorf("action %q must be namespaced under realtime.", e.Action)
		}
		verb, ok := parseActionVerb(e.Action)
		if !ok {
			t.Errorf("action %q uses non-canonical verb %q; rbac would reject the whole batch", e.Action, verb)
		}
	}
}

// TestNoBannedVerbs pins the specific non-canonical verbs that broke the hub:
// `connect` (realtime.stream.connect) and `connections`
// (realtime.admin.connections) are not in rbac's verb whitelist.
func TestNoBannedVerbs(t *testing.T) {
	for _, e := range Manifest() {
		if strings.HasSuffix(e.Action, ".connect") || strings.HasSuffix(e.Action, ".connections") {
			t.Errorf("action %q reintroduces a non-canonical verb", e.Action)
		}
	}
}

// TestGuardedActionsAreRegistered is the guarded==registered drift guard
// (RBC-FR-022): every action constant the hub authorizes with (OPA checks in
// opa.go, the admin-scope gate in internal/api) must appear in Manifest(), and
// the manifest must not register actions the hub never guards.
func TestGuardedActionsAreRegistered(t *testing.T) {
	guarded := map[string]bool{
		ActionRunStatusRead: true, // opa.go SchemeRunStatus
		ActionProposalRead:  true, // opa.go SchemeProposal
		ActionStreamExecute: true, // stream connect/attach capability
		ActionAdmin:         true, // internal/api adminAllowed scope gate
	}
	manifest := map[string]bool{}
	for _, e := range Manifest() {
		if manifest[e.Action] {
			t.Errorf("manifest lists %q twice", e.Action)
		}
		manifest[e.Action] = true
	}
	for a := range guarded {
		if !manifest[a] {
			t.Errorf("guarded action %q missing from Manifest(); rbac catalog would report action_known=false", a)
		}
	}
	for a := range manifest {
		if !guarded[a] {
			t.Errorf("manifest registers %q but the hub never guards it", a)
		}
	}
}

// TestOPADecidedActionsAreTenantScoped pins workspace_scoped=false for the
// OPA-decided topic actions: the hub's OPA input carries tenant + resource URN
// (the URN itself names the tenant) and never a workspace id, so rbac must not
// require a workspace context for them.
func TestOPADecidedActionsAreTenantScoped(t *testing.T) {
	for _, e := range Manifest() {
		if e.WorkspaceScoped {
			t.Errorf("action %q registered workspace_scoped=true; the hub never passes a workspace id to OPA", e.Action)
		}
	}
}
