// Python single-key projection (authz:proj:*) — the pre-assembled per-
// (tenant, subject, action, workspace) OPA input slice consumed by
// libs/py-common windrose_common.opaclient.OpaClient (MASTER-FR-012).
//
// The Go services read the granular perm:* keys and assemble the projection
// per request (libs/go-common opaclient/projection.go); several Python
// services instead load ONE pre-assembled key:
//
//	authz:proj:{tenant}:{subject}:{action}:{workspace}
//
// whose JSON value is exactly the input.projection shape the
// windrose.authz_input Rego policy evaluates. This file derives those facts
// TRUTHFULLY from the same Flat snapshot that feeds perm:* — the admin flag
// only for real admins, action_scoped from the registered catalog,
// workspace.assigned/actions from the user's actual workspace grants — so a
// role grant in rbac reaches the Python services through the identical
// recompute path, with versioned last-writer-wins and index-based GC.
//
// Key-count bound: one key per (granted action x owning context). Non-admin
// users get keys only for actions they actually hold; admins / use-case
// admins are expanded over the registered catalog (the admin short-circuit
// applies to every known action), which is bounded by the catalog size.
package projection

import (
	"fmt"
	"time"
)

// PyProjectionKey mirrors libs/py-common windrose_common/opaclient.py
// projection_key(): authz:proj:{tenant}:{subject}:{action}:{workspace}.
func PyProjectionKey(tenant, user, action, workspace string) string {
	return fmt.Sprintf("authz:proj:%s:%s:%s:%s", tenant, user, action, workspace)
}

// PyFlags mirrors input.projection.flags.
type PyFlags struct {
	Found   bool     `json:"found"`
	Admin   bool     `json:"admin"`
	WsAdmin []string `json:"ws_admin"`
}

// PyTenantActions mirrors input.projection.tenant_actions.
type PyTenantActions struct {
	Found   bool     `json:"found"`
	Actions []string `json:"actions"`
}

// PyWorkspace mirrors input.projection.workspace.
type PyWorkspace struct {
	Assigned bool     `json:"assigned"`
	Actions  []string `json:"actions"`
	Archived bool     `json:"archived"`
}

// PyResource mirrors input.projection.resource. The single-key scheme cannot
// carry per-URN grants (the key is not resource-qualified), so this is always
// found=false: resource-grant overlays flow through the granular perm:* path.
type PyResource struct {
	Found    bool   `json:"found"`
	Level    string `json:"level"`
	Archived bool   `json:"archived"`
}

// PyFacts is the JSON value of one authz:proj key — the exact
// input.projection shape windrose.authz_input evaluates, plus the versioned
// header (v/computed_at) so casSet gives the same last-writer-wins the perm:*
// keys have. Unknown fields are ignored by the Rego policy.
type PyFacts struct {
	versioned
	ActionKnown             bool            `json:"action_known"`
	ActionScoped            bool            `json:"action_scoped"`
	AutonomousEnabled       bool            `json:"autonomous_enabled"`
	Flags                   PyFlags         `json:"flags"`
	TenantActions           PyTenantActions `json:"tenant_actions"`
	Workspace               PyWorkspace     `json:"workspace"`
	Resource                PyResource      `json:"resource"`
	WorkspaceArchivedTenant bool            `json:"workspace_archived_tenant"`
}

// BuildPyProjection derives the full authz:proj key set for one user from the
// flattened projection — a pure function of Flat (+ the tenant's autonomous
// enablement flag), exhaustively unit-testable without Redis.
//
//   - tenant-scoped granted actions -> one key at workspace "" (the Rego
//     tenant path requires an empty workspace context);
//   - workspace-scoped granted actions -> one key per assigned workspace,
//     carrying that workspace's actual action set + archived flag;
//   - admin / use-case-admin -> expanded over the registered catalog (the
//     admin and ws-admin Rego paths short-circuit the action sets but still
//     require action_known and a projection key to load), with flags.admin
//     set ONLY from the real Admin-group membership;
//   - resource facts are always found=false (see PyResource).
func BuildPyProjection(f Flat, autonomousEnabled bool) map[string]PyFacts {
	tenant, user := f.TenantID.String(), f.UserID
	ver := versioned{V: f.Version, ComputedAt: f.ComputedAt.UTC().Format(time.RFC3339Nano)}

	wsAdmin := make([]string, 0, len(f.Flags.WsAdmin))
	for _, id := range f.Flags.WsAdmin {
		wsAdmin = append(wsAdmin, id.String())
	}
	base := PyFacts{
		versioned:         ver,
		ActionKnown:       true,
		AutonomousEnabled: autonomousEnabled,
		Flags:             PyFlags{Found: true, Admin: f.Flags.Admin, WsAdmin: wsAdmin},
		TenantActions:     PyTenantActions{Found: true, Actions: emptyIfNil(f.TenantActions)},
		Workspace:         PyWorkspace{Actions: []string{}},
		Resource:          PyResource{},
	}

	// Action sets to materialize. Admins (and use-case admins, whose ws_admin
	// short-circuit likewise ignores the granted action list) expand over the
	// whole registered catalog; everyone else gets exactly what they hold.
	tenantScoped := map[string]bool{}
	for _, a := range f.TenantActions {
		tenantScoped[a] = true
	}
	adminExpand := f.Flags.Admin || len(f.Flags.WsAdmin) > 0
	catalogScoped := map[string]bool{}
	if adminExpand {
		for a, scoped := range f.Catalog {
			if scoped {
				catalogScoped[a] = true
			} else {
				tenantScoped[a] = true
			}
		}
	}

	out := make(map[string]PyFacts)
	for a := range tenantScoped {
		facts := base
		facts.ActionScoped = false
		out[PyProjectionKey(tenant, user, a, "")] = facts
	}
	for wsID, entry := range f.WorkspaceActions {
		actions := map[string]bool{}
		for _, a := range entry.Actions {
			actions[a] = true
		}
		for a := range catalogScoped {
			actions[a] = true
		}
		for a := range actions {
			facts := base
			facts.ActionScoped = true
			facts.Workspace = PyWorkspace{
				Assigned: true,
				Actions:  emptyIfNil(entry.Actions),
				Archived: entry.Archived,
			}
			facts.WorkspaceArchivedTenant = entry.Archived
			out[PyProjectionKey(tenant, user, a, wsID.String())] = facts
		}
	}
	return out
}
