package projection

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Roles flow into the flattened flags (display-only metadata), deduped+sorted.
func TestFlatten_CarriesRoles(t *testing.T) {
	s := baseSnapshot()
	s.Roles = []string{"Case Manager", "Case Analyst", "Case Manager"}
	f := Flatten(s)
	assert.Equal(t, []string{"Case Analyst", "Case Manager"}, f.Flags.Roles)
}

// EffectiveCapabilities unions tenant- + workspace-scoped actions and surfaces
// roles + admin — the exact display view the UI capability gate consumes.
func TestEffectiveCapabilities_UnionsTenantAndWorkspace(t *testing.T) {
	s := baseSnapshot()
	s.Roles = []string{"Data User"}
	s.Actions = []string{"rbac.group.list", "dataset.dataset.read", "chart.dashboard.update"}
	s.AssignedWorkspaces = []WorkspaceRef{{ID: ws1}}
	f := Flatten(s)

	reader := &FlatReader{Flats: map[string]Flat{f.UserID: f}, Catalog: testCatalog()}
	roles, actions, admin, found, err := reader.EffectiveCapabilities(context.Background(), tenantA.String(), "u-1")
	require.NoError(t, err)
	assert.True(t, found)
	assert.False(t, admin)
	assert.Equal(t, []string{"Data User"}, roles)
	assert.ElementsMatch(t,
		[]string{"rbac.group.list", "dataset.dataset.read", "chart.dashboard.update"},
		actions)
}

func TestEffectiveCapabilities_AdminFlag(t *testing.T) {
	s := baseSnapshot()
	s.Admin = true
	f := Flatten(s)
	reader := &FlatReader{Flats: map[string]Flat{f.UserID: f}, Catalog: testCatalog()}
	_, _, admin, found, err := reader.EffectiveCapabilities(context.Background(), tenantA.String(), "u-1")
	require.NoError(t, err)
	assert.True(t, found)
	assert.True(t, admin)
}
