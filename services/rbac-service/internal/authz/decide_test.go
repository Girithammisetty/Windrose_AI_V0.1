package authz

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/projection"
	"github.com/windrose-ai/rbac-service/seed"
)

var (
	tenantA = uuid.MustParse("00000000-0000-0000-0000-00000000000a")
	tenantB = uuid.MustParse("00000000-0000-0000-0000-00000000000b")
	ws1     = uuid.MustParse("00000000-0000-0000-0000-000000000101")
	wsArch  = uuid.MustParse("00000000-0000-0000-0000-000000000103")
)

const dashURN = "wr:t-a:chart:dashboard/d-9"

func catalog() map[string]bool {
	return map[string]bool{
		"rbac.group.list":         false,
		"usage.report.read":       false,
		"case.case.assign":        true,
		"dataset.dataset.read":    true,
		"chart.dashboard.read":    true,
		"chart.dashboard.update":  true,
		"chart.dashboard.delete":  true,
		"chart.dashboard.share":   true,
		"chart.dashboard.export":  true,
		"chart.dashboard.admin":   true,
		"chart.dashboard.execute": true,
	}
}

// fixtureReader builds the in-memory policy fake used across the matrix.
func fixtureReader(t *testing.T) *projection.FlatReader {
	t.Helper()
	flat := projection.Flatten(projection.Snapshot{
		TenantID: tenantA,
		UserID:   "u-1",
		Actions:  []string{"rbac.group.list", "dataset.dataset.read", "case.case.assign", "chart.dashboard.update"},
		AssignedWorkspaces: []projection.WorkspaceRef{
			{ID: ws1}, {ID: wsArch, Archived: true},
		},
		ResourceGrants: []projection.ResourceGrant{
			{URN: dashURN, Level: domain.LevelEditor, WorkspaceID: ws1},
		},
		Catalog: catalog(),
		Version: 1,
	})
	r := projection.NewFlatReader(flat, catalog(), []uuid.UUID{wsArch})

	adminFlat := projection.Flatten(projection.Snapshot{
		TenantID: tenantA, UserID: "admin-1", Admin: true, Catalog: catalog(), Version: 1,
	})
	r.Flats["admin-1"] = adminFlat

	wsAdminFlat := projection.Flatten(projection.Snapshot{
		TenantID: tenantA, UserID: "wsadmin-1", UseCaseAdmin: true,
		AssignedWorkspaces: []projection.WorkspaceRef{{ID: ws1}},
		Catalog:            catalog(), Version: 1,
	})
	r.Flats["wsadmin-1"] = wsAdminFlat
	return r
}

func userIn(user, action, ws, urn string) Input {
	return Input{
		Subject:     Subject{ID: user, Typ: domain.TypUser},
		Action:      action,
		WorkspaceID: ws,
		ResourceURN: urn,
		Tenant:      tenantA.String(),
	}
}

func TestDecide_Matrix(t *testing.T) {
	r := fixtureReader(t)
	ctx := context.Background()

	cases := []struct {
		name   string
		in     Input
		allow  bool
		reason string
	}{
		// -- role/action paths --------------------------------------------
		{"tenant-scoped action allowed", userIn("u-1", "rbac.group.list", "", ""), true, ReasonRoleAction},
		{"tenant-scoped action not held", userIn("u-1", "usage.report.read", "", ""), false, ReasonDenyDefault},
		{"ws-scoped action in assigned ws", userIn("u-1", "dataset.dataset.read", ws1.String(), ""), true, ReasonRoleAction},
		{"ws-scoped action not held in assigned ws", userIn("u-1", "chart.dashboard.delete", ws1.String(), ""), false, ReasonDenyDefault},
		{"ws-scoped action in unassigned ws denied", userIn("u-1", "dataset.dataset.read", uuid.NewString(), ""), false, ReasonNotAssigned},

		// -- workspace-context validation (AC-3) --------------------------
		{"ws-scoped without workspace context", userIn("u-1", "dataset.dataset.read", "", ""), false, ReasonWorkspaceCtxRequired},
		{"tenant-scoped with workspace context", userIn("u-1", "rbac.group.list", ws1.String(), ""), false, ReasonWorkspaceCtxForbid},

		// -- unknown action ------------------------------------------------
		{"unknown action denied", userIn("u-1", "nope.nope.read", "", ""), false, ReasonUnknownAction},

		// -- archived workspace (AC-14) ------------------------------------
		{"archived ws read allowed", userIn("u-1", "dataset.dataset.read", wsArch.String(), ""), true, ReasonRoleAction},
		{"archived ws write denied", userIn("u-1", "chart.dashboard.update", wsArch.String(), ""), false, ReasonWorkspaceArchived},

		// -- admin bypass limits (BR-7) -------------------------------------
		{"admin bypasses tenant-scoped action check", userIn("admin-1", "usage.report.read", "", ""), true, ReasonAdminBypass},
		{"admin bypasses ws-scoped action check", userIn("admin-1", "chart.dashboard.delete", ws1.String(), ""), true, ReasonAdminBypass},
		{"admin blocked on archived ws write", userIn("admin-1", "chart.dashboard.update", wsArch.String(), ""), false, ReasonWorkspaceArchived},
		{"admin allowed archived ws read", userIn("admin-1", "chart.dashboard.read", wsArch.String(), ""), true, ReasonAdminBypass},
		{"admin still context-validated", userIn("admin-1", "dataset.dataset.read", "", ""), false, ReasonWorkspaceCtxRequired},

		// -- workspace admin flag -------------------------------------------
		{"ws-admin any ws action in own ws", userIn("wsadmin-1", "chart.dashboard.delete", ws1.String(), ""), true, ReasonWorkspaceAdmin},
		{"ws-admin denied outside own ws", userIn("wsadmin-1", "chart.dashboard.delete", uuid.NewString(), ""), false, ReasonNotAssigned},

		// -- level -> verb mapping via grant overlay (RBC-FR-030) -----------
		// (update is also held via role, and the role path wins first)
		{"editor grant allows update", userIn("u-1", "chart.dashboard.update", ws1.String(), dashURN), true, ReasonRoleAction},
		{"editor grant allows read", userIn("u-1", "chart.dashboard.read", ws1.String(), dashURN), true, ReasonResourceGrant + ":editor"},
		{"editor grant allows share", userIn("u-1", "chart.dashboard.share", ws1.String(), dashURN), true, ReasonResourceGrant + ":editor"},
		{"editor grant allows execute", userIn("u-1", "chart.dashboard.execute", ws1.String(), dashURN), true, ReasonResourceGrant + ":editor"},
		{"editor grant denies delete", userIn("u-1", "chart.dashboard.delete", ws1.String(), dashURN), false, ReasonDenyDefault},
		{"editor grant denies admin verb", userIn("u-1", "chart.dashboard.admin", ws1.String(), dashURN), false, ReasonDenyDefault},

		// -- tenant boundary --------------------------------------------------
		{"wrong tenant denies even for admin", Input{
			Subject: Subject{ID: "admin-1", Typ: domain.TypUser},
			Action:  "usage.report.read", Tenant: tenantB.String(),
		}, false, ReasonProjectionMiss},

		// -- trusted service token: explicit least-privilege scope (task #79) --
		{"service token allowed its explicit scope", Input{
			Subject: Subject{ID: "svc:agent-runtime", Typ: domain.TypService,
				Scopes: []string{"ai.key.write"}},
			Action: "ai.key.write", Tenant: tenantA.String(),
		}, true, ReasonServiceScope},
		{"service token denied an action not in its scopes", Input{
			Subject: Subject{ID: "svc:agent-runtime", Typ: domain.TypService,
				Scopes: []string{"ai.key.write"}},
			Action: "usage.report.read", Tenant: tenantA.String(),
		}, false, ReasonProjectionMiss},
		{"service wildcard '*' NOT honored by the service-scope path", Input{
			Subject: Subject{ID: "svc:mystery", Typ: domain.TypService,
				Scopes: []string{"*"}},
			Action: "usage.report.read", Tenant: tenantA.String(),
		}, false, ReasonProjectionMiss},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d, err := Decide(ctx, tc.in, r)
			require.NoError(t, err)
			assert.Equal(t, tc.allow, d.Allowed, "allowed mismatch (reason=%s)", d.Reason)
			assert.Equal(t, tc.reason, d.Reason)
		})
	}
}

// Grant levels exhaustively: viewer ⊂ editor ⊂ owner (RBC-FR-030).
func TestDecide_LevelVerbMapping(t *testing.T) {
	ctx := context.Background()
	verbs := []string{"read", "list", "export", "update", "execute", "share", "delete", "admin"}
	expect := map[domain.GrantLevel]map[string]bool{
		domain.LevelViewer: {"read": true, "list": true, "export": true},
		domain.LevelEditor: {"read": true, "list": true, "export": true, "update": true, "execute": true, "share": true},
		domain.LevelOwner:  {"read": true, "list": true, "export": true, "update": true, "execute": true, "share": true, "delete": true, "admin": true},
	}
	cat := map[string]bool{}
	for _, v := range verbs {
		cat["chart.dashboard."+v] = true
	}
	cat["chart.dashboard.list"] = true

	for level, allowed := range expect {
		flat := projection.Flatten(projection.Snapshot{
			TenantID: tenantA, UserID: "u-g",
			ResourceGrants: []projection.ResourceGrant{{URN: dashURN, Level: level, WorkspaceID: ws1}},
			Catalog:        cat, Version: 1,
		})
		r := projection.NewFlatReader(flat, cat, nil)
		for _, v := range verbs {
			in := userIn("u-g", "chart.dashboard."+v, ws1.String(), dashURN)
			d, err := Decide(ctx, in, r)
			require.NoError(t, err)
			assert.Equalf(t, allowed[v], d.Allowed, "level=%s verb=%s reason=%s", level, v, d.Reason)
		}
	}
}

// OBO intersection (BR-6, AC-7): agent scopes AND user grants; agents never
// widen user permissions.
func TestDecide_OBOIntersection(t *testing.T) {
	r := fixtureReader(t)
	ctx := context.Background()

	obo := func(action string, scopes []string, ws string) Input {
		return Input{
			Subject:     Subject{ID: "agent-7", Typ: domain.TypAgentOBO, OboSub: "u-1", Scopes: scopes},
			Action:      action,
			WorkspaceID: ws,
			Tenant:      tenantA.String(),
		}
	}

	// User can assign cases; agent scope includes it -> allow.
	d, err := Decide(ctx, obo("case.case.assign", []string{"case.case.assign"}, ws1.String()), r)
	require.NoError(t, err)
	assert.True(t, d.Allowed)

	// User can, but agent scopes exclude -> deny scope_excluded (AC-7).
	d, err = Decide(ctx, obo("case.case.assign", []string{"chart.dashboard.read"}, ws1.String()), r)
	require.NoError(t, err)
	assert.False(t, d.Allowed)
	assert.Equal(t, ReasonScopeExcluded, d.Reason)

	// Agent scope includes, but user CANNOT -> deny (intersection).
	d, err = Decide(ctx, obo("chart.dashboard.delete", []string{"chart.dashboard.delete"}, ws1.String()), r)
	require.NoError(t, err)
	assert.False(t, d.Allowed)

	// Wildcard scope still cannot widen beyond the user.
	d, err = Decide(ctx, obo("usage.report.read", []string{"*"}, ""), r)
	require.NoError(t, err)
	assert.False(t, d.Allowed)

	// An agent with write scopes acting for an editor-level user gets
	// editor-level outcomes (BR-6): delete stays denied.
	in := obo("chart.dashboard.delete", []string{"*"}, ws1.String())
	in.ResourceURN = dashURN
	d, err = Decide(ctx, in, r)
	require.NoError(t, err)
	assert.False(t, d.Allowed)
}

// Autonomous agents: action ∈ scopes AND tenant enablement flag (RBC-FR-044).
func TestDecide_AutonomousAgent(t *testing.T) {
	r := fixtureReader(t)
	ctx := context.Background()
	in := Input{
		Subject: Subject{ID: "agent-9", Typ: domain.TypAgentAutonomous, Scopes: []string{"rbac.group.list"}},
		Action:  "rbac.group.list",
		Tenant:  tenantA.String(),
	}

	d, err := Decide(ctx, in, r)
	require.NoError(t, err)
	assert.False(t, d.Allowed)
	assert.Equal(t, ReasonAutonomousDisabled, d.Reason, "tenant flag off -> deny")

	r.Autonomous = true
	d, err = Decide(ctx, in, r)
	require.NoError(t, err)
	assert.True(t, d.Allowed)
	assert.Equal(t, ReasonAutonomousScope, d.Reason)

	in.Action = "usage.report.read" // not in scopes
	d, err = Decide(ctx, in, r)
	require.NoError(t, err)
	assert.False(t, d.Allowed)
	assert.Equal(t, ReasonScopeExcluded, d.Reason)
}

// An unknown principal type must fail closed (parity with the Rego bundle,
// which has no allow path for it). Before the fix the switch had no default
// and `typ="banana"` fell through to the user path and was allowed.
func TestDecide_UnknownPrincipalTypeDenies(t *testing.T) {
	r := fixtureReader(t)
	ctx := context.Background()
	for _, typ := range []string{"banana", "admin", "root", ""} {
		in := Input{
			Subject: Subject{ID: "admin-1", Typ: typ}, // admin-1 IS a tenant admin
			Action:  "usage.report.read",
			Tenant:  tenantA.String(),
		}
		d, err := Decide(ctx, in, r)
		require.NoError(t, err)
		assert.Falsef(t, d.Allowed, "typ=%q must deny even for an admin subject", typ)
		if typ != "" {
			assert.Equal(t, ReasonUnknownPrincipal, d.Reason)
		}
	}
	// The same subject with a valid typ IS allowed (admin bypass) — proving the
	// deny above is caused by the unknown typ, not a missing projection.
	d, err := Decide(ctx, Input{
		Subject: Subject{ID: "admin-1", Typ: domain.TypUser},
		Action:  "usage.report.read", Tenant: tenantA.String(),
	}, r)
	require.NoError(t, err)
	assert.True(t, d.Allowed)
}

// Projection miss surfaces Miss=true so callers fall back (RBC-FR-045).
func TestDecide_ProjectionMiss(t *testing.T) {
	r := projection.NewFlatReader(projection.Flat{UserID: "someone-else"}, catalog(), nil)
	d, err := Decide(context.Background(), userIn("ghost", "rbac.group.list", "", ""), r)
	require.NoError(t, err)
	assert.False(t, d.Allowed)
	assert.True(t, d.Miss)
	assert.Equal(t, ReasonProjectionMiss, d.Reason)
}

// Authz matrix over the 10 system roles' seeded actions — the unit-tier
// variant of the master §2.8-071 matrix with the in-memory policy fake.
func TestDecide_SystemRoleMatrix(t *testing.T) {
	cat := domain.CatalogMap()
	ctx := context.Background()

	type probe struct {
		action string
		ws     bool
	}
	probes := []probe{
		{"case.case.assign", true},
		{"dataset.dataset.create", true},
		{"ingestion.connection.create", true},
		{"chart.dashboard.create", true},
		{"experiment.run.execute", true},
		{"audit.log.read", false},
	}
	// expected allow per role per probe (from seed/roles_actions.yaml).
	// Model Builder + Case Manager author dashboards (no-code chart editor),
	// so both bind chart.dashboard.create in the seed matrix.
	expect := map[string][]bool{
		// Use case Admin gained ingestion.connection.create/delete in the seed
		// (pack-service inc12 write_adapters: pack installs materialize governed
		// outgoing connections), so probe[2] (ingestion.connection.create) is allow.
		domain.RoleUseCaseAdmin:    {true, true, true, true, false, false},
		domain.RoleDataUser:        {false, true, true, false, false, false},
		domain.RoleModelBuilder:    {false, false, false, true, true, false},
		domain.RoleDataIntegration: {false, false, true, false, false, false},
		domain.RoleInsights:        {false, false, false, true, false, false},
		domain.RoleInsightsAdHoc:   {false, false, false, false, false, false},
		domain.RoleCaseAnalyst:     {false, false, false, false, false, false},
		domain.RoleCaseManager:     {true, false, false, true, false, false},
		domain.RoleCaseExecutive:   {false, false, false, false, false, false},
	}

	seeds, err := domain.ParseRoleSeeds(seedYAML(t))
	require.NoError(t, err)
	byName := map[string][]string{}
	for _, s := range seeds {
		byName[s.Name] = s.Actions
	}

	for role, wants := range expect {
		flat := projection.Flatten(projection.Snapshot{
			TenantID: tenantA, UserID: "u-r",
			Actions:            byName[role],
			AssignedWorkspaces: []projection.WorkspaceRef{{ID: ws1}},
			Catalog:            cat, Version: 1,
		})
		r := projection.NewFlatReader(flat, cat, nil)
		for i, p := range probes {
			ws := ""
			if p.ws {
				ws = ws1.String()
			}
			d, err := Decide(ctx, userIn("u-r", p.action, ws, ""), r)
			require.NoError(t, err)
			assert.Equalf(t, wants[i], d.Allowed, "role=%s action=%s reason=%s", role, p.action, d.Reason)
		}
	}

	// Admin role relies on the flag, not bindings.
	adminFlat := projection.Flatten(projection.Snapshot{
		TenantID: tenantA, UserID: "u-a", Admin: true,
		AssignedWorkspaces: []projection.WorkspaceRef{{ID: ws1}},
		Catalog:            cat, Version: 1,
	})
	r := projection.NewFlatReader(adminFlat, cat, nil)
	for _, p := range probes {
		ws := ""
		if p.ws {
			ws = ws1.String()
		}
		d, err := Decide(ctx, userIn("u-a", p.action, ws, ""), r)
		require.NoError(t, err)
		assert.Truef(t, d.Allowed, "admin should bypass %s", p.action)
	}
}

func seedYAML(t *testing.T) []byte {
	t.Helper()
	return seed.RolesActionsYAML
}
