package projection

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// A non-admin user gets one authz:proj key per granted action per owning
// context: tenant-scoped actions at ws="", workspace-scoped actions per
// assigned workspace — and NOTHING else (bounded key count).
func TestPyProjection_GrantedActionsOnly(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"rbac.group.list", "dataset.dataset.read", "chart.dashboard.update"}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}}

	keys := BuildPyProjection(Flatten(s), false)

	require.Len(t, keys, 3) // 1 tenant-scoped + 2 scoped x 1 ws
	tk := keys[PyProjectionKey(tenantA.String(), "u-1", "rbac.group.list", "")]
	assert.True(t, tk.ActionKnown)
	assert.False(t, tk.ActionScoped)
	assert.True(t, tk.TenantActions.Found)
	assert.Equal(t, []string{"rbac.group.list"}, tk.TenantActions.Actions)
	assert.False(t, tk.Workspace.Assigned)
	assert.False(t, tk.Flags.Admin, "admin flag must be truthful")
	assert.True(t, tk.Flags.Found)

	wk := keys[PyProjectionKey(tenantA.String(), "u-1", "dataset.dataset.read", ws1.String())]
	assert.True(t, wk.ActionKnown)
	assert.True(t, wk.ActionScoped, "action_scoped comes from the catalog")
	assert.True(t, wk.Workspace.Assigned)
	assert.ElementsMatch(t, []string{"dataset.dataset.read", "chart.dashboard.update"}, wk.Workspace.Actions)
	assert.False(t, wk.Flags.Admin)
	assert.False(t, wk.Resource.Found, "no per-URN grants through the single-key scheme")

	// no key for an action the user does not hold
	_, ok := keys[PyProjectionKey(tenantA.String(), "u-1", "dataset.dataset.export", ws1.String())]
	assert.False(t, ok)
}

// Workspace-scoped grants materialize only under ASSIGNED workspaces; a user
// with scoped actions but no assignment gets no workspace keys (deny).
func TestPyProjection_NoAssignmentNoWorkspaceKeys(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"dataset.dataset.read"}

	keys := BuildPyProjection(Flatten(s), false)

	assert.Empty(t, keys)
}

// Admins expand over the registered catalog (the Rego admin short-circuit
// needs action_known + a loadable key for any action), with admin=true carried
// truthfully from the real Admin-group membership.
func TestPyProjection_AdminExpandsOverCatalog(t *testing.T) {
	s := baseSnapshot()
	s.Admin = true
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}}

	keys := BuildPyProjection(Flatten(s), false)

	// every catalog action is materialized: tenant-scoped at "", scoped at ws1.
	nTenant, nScoped := 0, 0
	for a, scoped := range testCatalog() {
		if scoped {
			nScoped++
			f, ok := keys[PyProjectionKey(tenantA.String(), "u-1", a, ws1.String())]
			require.True(t, ok, "missing scoped admin key for %s", a)
			assert.True(t, f.Flags.Admin)
			assert.True(t, f.ActionScoped)
		} else {
			nTenant++
			f, ok := keys[PyProjectionKey(tenantA.String(), "u-1", a, "")]
			require.True(t, ok, "missing tenant admin key for %s", a)
			assert.True(t, f.Flags.Admin)
			assert.False(t, f.ActionScoped)
		}
	}
	assert.Len(t, keys, nTenant+nScoped)
}

// A NON-admin is never given admin facts — the projection carries exactly the
// real flags (regression guard against the permissive demo-seed shape).
func TestPyProjection_NonAdminNeverAdminFacts(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"dataset.dataset.read", "usage.report.read"}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}, {ID: ws2}}

	for _, f := range BuildPyProjection(Flatten(s), false) {
		assert.False(t, f.Flags.Admin)
		assert.Empty(t, f.Flags.WsAdmin)
	}
}

// Archived workspaces carry archived=true + the read-verb-reduced action set,
// and set workspace_archived_tenant so the admin write block holds (BR-7).
func TestPyProjection_ArchivedWorkspaceFacts(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"dataset.dataset.read", "dataset.dataset.update", "dataset.dataset.export"}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: wsArch, Archived: true}}

	keys := BuildPyProjection(Flatten(s), false)

	// update is dropped from the archived workspace's set, so no key for it.
	_, ok := keys[PyProjectionKey(tenantA.String(), "u-1", "dataset.dataset.update", wsArch.String())]
	assert.False(t, ok)
	f, ok := keys[PyProjectionKey(tenantA.String(), "u-1", "dataset.dataset.read", wsArch.String())]
	require.True(t, ok)
	assert.True(t, f.Workspace.Archived)
	assert.True(t, f.WorkspaceArchivedTenant)
	assert.ElementsMatch(t, []string{"dataset.dataset.read", "dataset.dataset.export"}, f.Workspace.Actions)
}

// Use-case admins (ws_admin flag) also expand over the catalog: the Rego
// ws-admin path allows any known scoped action in an assigned workspace.
func TestPyProjection_WsAdminExpandsScoped(t *testing.T) {
	s := baseSnapshot()
	s.UseCaseAdmin = true
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}}

	keys := BuildPyProjection(Flatten(s), false)

	f, ok := keys[PyProjectionKey(tenantA.String(), "u-1", "chart.dashboard.delete", ws1.String())]
	require.True(t, ok)
	assert.False(t, f.Flags.Admin, "use-case admin is not tenant admin")
	assert.Equal(t, []string{ws1.String()}, f.Flags.WsAdmin)
}

// Version and autonomous flag propagate into every key (CAS last-writer-wins
// + agent_autonomous enablement parity with the Go reader).
func TestPyProjection_VersionAndAutonomousPropagate(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"rbac.group.list"}
	s.Version = 42

	keys := BuildPyProjection(Flatten(s), true)

	require.Len(t, keys, 1)
	for _, f := range keys {
		assert.Equal(t, int64(42), f.V)
		assert.True(t, f.AutonomousEnabled)
	}
}
