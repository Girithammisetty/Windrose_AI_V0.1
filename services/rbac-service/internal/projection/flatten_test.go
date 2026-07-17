package projection

import (
	"sort"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/rbac-service/internal/domain"
)

var (
	tenantA = uuid.MustParse("00000000-0000-0000-0000-00000000000a")
	ws1     = uuid.MustParse("00000000-0000-0000-0000-000000000101")
	ws2     = uuid.MustParse("00000000-0000-0000-0000-000000000102")
	wsArch  = uuid.MustParse("00000000-0000-0000-0000-000000000103")
)

// testCatalog: two tenant-scoped, several workspace-scoped actions.
func testCatalog() map[string]bool {
	return map[string]bool{
		"rbac.group.list":        false,
		"usage.report.read":      false,
		"dataset.dataset.read":   true,
		"dataset.dataset.update": true,
		"dataset.dataset.export": true,
		"chart.dashboard.update": true,
		"chart.dashboard.delete": true,
		"chart.dashboard.list":   true,
	}
}

func baseSnapshot() Snapshot {
	return Snapshot{
		TenantID:   tenantA,
		UserID:     "u-1",
		Catalog:    testCatalog(),
		Version:    7,
		ComputedAt: time.Now().UTC(),
	}
}

// RBC-FR-041 step 2: actions split by the catalog's workspace_scoped flag;
// workspace-scoped actions are intersected with assigned workspaces.
func TestFlatten_TenantVsWorkspaceScopedSplit(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"rbac.group.list", "dataset.dataset.read", "chart.dashboard.update"}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}, {ID: ws2}}

	f := Flatten(s)

	assert.Equal(t, []string{"rbac.group.list"}, f.TenantActions)
	require.Len(t, f.WorkspaceActions, 2)
	for _, ws := range []uuid.UUID{ws1, ws2} {
		entry := f.WorkspaceActions[ws]
		assert.ElementsMatch(t, []string{"chart.dashboard.update", "dataset.dataset.read"}, entry.Actions)
		assert.False(t, entry.Archived)
	}
}

// A user with workspace-scoped actions but NO assigned workspace gets no ws
// keys at all (∅ ⇒ not assigned ⇒ deny; RBC-FR-003).
func TestFlatten_NoAssignmentNoWorkspaceKeys(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"dataset.dataset.read"}

	f := Flatten(s)

	assert.Empty(t, f.WorkspaceActions)
	assert.Empty(t, f.TenantActions)
}

// Public workspaces are assigned to everyone; they flow through Snapshot the
// same way (AC-2's projection half: role action + public workspace = allow).
func TestFlatten_PublicWorkspaceAssignment(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"dataset.dataset.read"}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}} // loader added public ws

	f := Flatten(s)

	entry, ok := f.WorkspaceActions[ws1]
	require.True(t, ok)
	assert.Contains(t, entry.Actions, "dataset.dataset.read")
}

// Union over multiple permission groups' roles: duplicates collapse.
func TestFlatten_MultiGroupUnionDedup(t *testing.T) {
	s := baseSnapshot()
	// Same action arriving from two groups/roles + distinct ones.
	s.Actions = []string{
		"dataset.dataset.read", "dataset.dataset.read",
		"chart.dashboard.update", "rbac.group.list", "rbac.group.list",
	}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}}

	f := Flatten(s)

	assert.Equal(t, []string{"rbac.group.list"}, f.TenantActions)
	assert.Equal(t, []string{"chart.dashboard.update", "dataset.dataset.read"}, f.WorkspaceActions[ws1].Actions)
}

// Actions no longer in the catalog (post-deprecation) are dropped, not leaked.
func TestFlatten_UnknownActionsDropped(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{"legacy.thing.read", "rbac.group.list"}

	f := Flatten(s)

	assert.Equal(t, []string{"rbac.group.list"}, f.TenantActions)
}

// Archived workspaces retain only read verbs (RBC-FR-004 / AC-14): reads by
// previously-assigned users still allow, writes are stripped.
func TestFlatten_ArchivedWorkspaceKeepsOnlyReadVerbs(t *testing.T) {
	s := baseSnapshot()
	s.Actions = []string{
		"dataset.dataset.read", "dataset.dataset.update",
		"dataset.dataset.export", "chart.dashboard.delete", "chart.dashboard.list",
	}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}, {ID: wsArch, Archived: true}}

	f := Flatten(s)

	live := f.WorkspaceActions[ws1]
	assert.ElementsMatch(t, []string{
		"dataset.dataset.read", "dataset.dataset.update",
		"dataset.dataset.export", "chart.dashboard.delete", "chart.dashboard.list",
	}, live.Actions)

	arch := f.WorkspaceActions[wsArch]
	assert.True(t, arch.Archived)
	assert.ElementsMatch(t, []string{
		"dataset.dataset.read", "dataset.dataset.export", "chart.dashboard.list",
	}, arch.Actions, "only read/list/export survive archive")
}

// Resource grants are keyed by URN hash and keep the MAX level across
// duplicates (additive model, no negative grants).
func TestFlatten_ResourceGrantsMaxLevel(t *testing.T) {
	urn := "wr:t-1:chart:dashboard/d-9"
	s := baseSnapshot()
	s.ResourceGrants = []ResourceGrant{
		{URN: urn, Level: domain.LevelViewer, WorkspaceID: ws1},
		{URN: urn, Level: domain.LevelEditor, WorkspaceID: ws2}, // via 2nd group
		{URN: "wr:t-1:dataset:dataset/ds-1", Level: domain.LevelOwner, WorkspaceID: ws1},
	}

	f := Flatten(s)

	require.Len(t, f.Resources, 2)
	e := f.Resources[domain.URNHash(urn)]
	assert.Equal(t, "editor", e.Level, "max(viewer, editor) = editor")
	assert.Equal(t, urn, e.URN)
	o := f.Resources[domain.URNHash("wr:t-1:dataset:dataset/ds-1")]
	assert.Equal(t, "owner", o.Level)
}

// A grant reachable via a live workspace beats the same URN's archived one.
func TestFlatten_ResourceGrantArchivedMerge(t *testing.T) {
	urn := "wr:t-1:chart:dashboard/d-9"
	s := baseSnapshot()
	s.ResourceGrants = []ResourceGrant{
		{URN: urn, Level: domain.LevelEditor, WorkspaceID: wsArch, Archived: true},
		{URN: urn, Level: domain.LevelEditor, WorkspaceID: ws1, Archived: false},
	}

	f := Flatten(s)
	assert.False(t, f.Resources[domain.URNHash(urn)].Archived)
}

// Admin flag is carried, never expanded into action sets (short-circuit
// happens at decision time and stays tenant-bound).
func TestFlatten_AdminFlagNotExpanded(t *testing.T) {
	s := baseSnapshot()
	s.Admin = true

	f := Flatten(s)

	assert.True(t, f.Flags.Admin)
	assert.Empty(t, f.TenantActions)
	assert.Empty(t, f.WorkspaceActions)
}

// ws_admin lists assigned workspaces only when the user holds Use case Admin.
func TestFlatten_WsAdminFlag(t *testing.T) {
	s := baseSnapshot()
	s.UseCaseAdmin = true
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws2}, {ID: ws1}}

	f := Flatten(s)

	want := []string{ws1.String(), ws2.String()}
	got := []string{}
	for _, id := range f.Flags.WsAdmin {
		got = append(got, id.String())
	}
	sort.Strings(want)
	assert.Equal(t, want, got, "sorted, deterministic")

	s.UseCaseAdmin = false
	assert.Empty(t, Flatten(s).Flags.WsAdmin)
}

// Version and timestamps propagate for last-writer-wins (RBC-FR-048).
func TestFlatten_VersionPropagates(t *testing.T) {
	s := baseSnapshot()
	f := Flatten(s)
	assert.Equal(t, int64(7), f.Version)
	assert.Equal(t, s.ComputedAt, f.ComputedAt)
	assert.Equal(t, tenantA, f.TenantID)
	assert.Equal(t, "u-1", f.UserID)
}

// Deny-by-default sanity: a zero snapshot flattens to zero access.
func TestFlatten_EmptySnapshotDeniesEverything(t *testing.T) {
	f := Flatten(Snapshot{TenantID: tenantA, UserID: "u-0", Catalog: testCatalog()})
	assert.Empty(t, f.TenantActions)
	assert.Empty(t, f.WorkspaceActions)
	assert.Empty(t, f.Resources)
	assert.False(t, f.Flags.Admin)
	assert.Empty(t, f.Flags.WsAdmin)
}
