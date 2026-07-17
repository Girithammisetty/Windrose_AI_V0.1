package domain_test

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/seed"
)

func TestSystemRoleCatalog(t *testing.T) {
	names := domain.SystemRoleNames()
	assert.Len(t, names, 10, "the 10-role V1 catalog")
	assert.Contains(t, names, "Admin")
	assert.Contains(t, names, "Use case Admin")
	assert.Contains(t, names, "Data User") // renamed from V1 "IDO User"
	assert.Contains(t, names, "Insights Ad-Hoc User")
	assert.Contains(t, names, "Case Executive")
}

func TestCanonicalCatalog(t *testing.T) {
	cat := domain.CanonicalCatalog()
	require.NotEmpty(t, cat)
	seen := map[string]bool{}
	for _, a := range cat {
		svc, res, verb, err := domain.ParseAction(a.Action)
		require.NoErrorf(t, err, "catalog action %q must be well-formed", a.Action)
		assert.Equal(t, a.Service, svc)
		assert.Equal(t, a.Resource, res)
		assert.Equal(t, a.Verb, verb)
		assert.Falsef(t, seen[a.Action], "duplicate catalog action %q", a.Action)
		seen[a.Action] = true
	}
	// Spot-check BRD examples (MASTER-FR-016) and scoping semantics.
	m := domain.CatalogMap()
	scoped, ok := m["dataset.dataset.read"]
	require.True(t, ok)
	assert.True(t, scoped, "dataset actions are workspace-scoped")
	scoped, ok = m["case.case.assign"]
	require.True(t, ok)
	assert.True(t, scoped)
	scoped, ok = m["chart.dashboard.delete"]
	require.True(t, ok)
	assert.True(t, scoped)
	scoped, ok = m["rbac.group.create"]
	require.True(t, ok)
	assert.False(t, scoped, "rbac admin actions are tenant-scoped")
	scoped, ok = m["audit.log.read"]
	require.True(t, ok)
	assert.False(t, scoped)
}

func TestParseAction(t *testing.T) {
	_, _, _, err := domain.ParseAction("dataset.dataset.read")
	assert.NoError(t, err)
	for _, bad := range []string{
		"dataset.read",           // missing segment
		"dataset.dataset.frob",   // unknown verb
		"Dataset.dataset.read",   // uppercase
		"dataset..read",          // empty segment
		"dataset.dataset.read.x", // extra segment
		"",
	} {
		_, _, _, err := domain.ParseAction(bad)
		assert.Errorf(t, err, "%q should be rejected", bad)
	}
	assert.Equal(t, "read", domain.ActionVerb("dataset.dataset.read"))
}

func TestURN(t *testing.T) {
	u, err := domain.ParseURN("wr:t-42:dataset:dataset/ds-9f2")
	require.NoError(t, err)
	assert.Equal(t, "t-42", u.TenantID)
	assert.Equal(t, "dataset", u.Service)
	assert.Equal(t, "dataset", u.ResourceType)
	assert.Equal(t, "ds-9f2", u.ResourceID)
	assert.Equal(t, "wr:t-42:dataset:dataset/ds-9f2", u.String())

	for _, bad := range []string{"", "wr:t:svc:notype", "xx:t:svc:a/b", "wr:t:svc:/id", "wr::svc:a/b"} {
		_, err := domain.ParseURN(bad)
		assert.Errorf(t, err, "%q should be rejected", bad)
	}

	h := domain.URNHash("wr:t-42:dataset:dataset/ds-9f2")
	assert.Len(t, h, 32)
	assert.Equal(t, h, domain.URNHash("wr:t-42:dataset:dataset/ds-9f2"), "stable")
	assert.NotEqual(t, h, domain.URNHash("wr:t-42:dataset:dataset/other"))
}

func TestLevelLattice(t *testing.T) {
	assert.True(t, domain.LevelAllowsVerb(domain.LevelViewer, "read"))
	assert.True(t, domain.LevelAllowsVerb(domain.LevelViewer, "export"))
	assert.False(t, domain.LevelAllowsVerb(domain.LevelViewer, "update"))
	assert.True(t, domain.LevelAllowsVerb(domain.LevelEditor, "update"))
	assert.True(t, domain.LevelAllowsVerb(domain.LevelEditor, "share"))
	assert.False(t, domain.LevelAllowsVerb(domain.LevelEditor, "delete"))
	assert.True(t, domain.LevelAllowsVerb(domain.LevelOwner, "delete"))
	assert.True(t, domain.LevelAllowsVerb(domain.LevelOwner, "admin"))
	assert.False(t, domain.LevelAllowsVerb(domain.LevelOwner, "create"), "create comes from roles, not grants")

	assert.Equal(t, domain.LevelOwner, domain.MaxLevel(domain.LevelViewer, domain.LevelOwner))
	assert.Equal(t, domain.LevelEditor, domain.MaxLevel(domain.LevelEditor, domain.LevelViewer))
	assert.True(t, domain.LevelAtLeast(domain.LevelOwner, domain.LevelViewer))
	assert.False(t, domain.LevelAtLeast(domain.LevelViewer, domain.LevelEditor))
}

// The shipped seed matrix must parse, cover all 10 roles, and reference only
// canonical catalog actions (RBC-FR-024).
func TestRoleSeedsShipValid(t *testing.T) {
	seeds, err := domain.ParseRoleSeeds(seed.RolesActionsYAML)
	require.NoError(t, err)
	assert.Len(t, seeds, 10)
	names := map[string]bool{}
	for _, s := range seeds {
		names[s.Name] = true
		assert.NotEmptyf(t, s.Actions, "role %s must bind actions", s.Name)
	}
	for _, want := range domain.SystemRoleNames() {
		assert.Truef(t, names[want], "seed missing system role %q", want)
	}
}

func TestRoleSeedsRejectBadInput(t *testing.T) {
	_, err := domain.ParseRoleSeeds([]byte("roles:\n  - name: Nope\n    actions: [dataset.dataset.read]\n"))
	assert.Error(t, err, "unknown role name")

	_, err = domain.ParseRoleSeeds([]byte("roles:\n  - name: Admin\n    actions: [not.a.realverb]\n"))
	assert.Error(t, err, "unknown verb")
}
