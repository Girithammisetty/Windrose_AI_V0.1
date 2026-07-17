package integration

import (
	"context"
	"net/http"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/rbac-service/internal/authz"
	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/events"
)

// ---- shared flow helpers -----------------------------------------------------

func (h *harness) createWorkspace(t *testing.T, env *tenantEnv, name string, public bool) domain.Workspace {
	t.Helper()
	r := h.do(t, http.MethodPost, "/api/v1/workspaces", env.AdminTok,
		map[string]any{"name": name, "public": public})
	require.Equalf(t, http.StatusCreated, r.Status, "body: %s", r.Body)
	var ws domain.Workspace
	r.JSON(t, &ws)
	return ws
}

func (h *harness) createGroup(t *testing.T, env *tenantEnv, name string, gtype domain.GroupType) domain.Group {
	t.Helper()
	r := h.do(t, http.MethodPost, "/api/v1/groups", env.AdminTok,
		map[string]any{"name": name, "group_type": string(gtype)})
	require.Equalf(t, http.StatusCreated, r.Status, "body: %s", r.Body)
	var g domain.Group
	r.JSON(t, &g)
	return g
}

func (h *harness) linkGroup(t *testing.T, env *tenantEnv, ws, group uuid.UUID) {
	t.Helper()
	r := h.do(t, http.MethodPut, "/api/v1/workspaces/"+ws.String()+"/content-groups/"+group.String(), env.AdminTok, nil)
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
}

func (h *harness) addMember(t *testing.T, env *tenantEnv, group uuid.UUID, user string) {
	t.Helper()
	r := h.do(t, http.MethodPut, "/api/v1/groups/"+group.String()+"/members/"+user, env.AdminTok, nil)
	require.Containsf(t, []int{http.StatusOK, http.StatusCreated}, r.Status, "body: %s", r.Body)
}

func (h *harness) createRole(t *testing.T, env *tenantEnv, name string, actions []string) domain.Role {
	t.Helper()
	r := h.do(t, http.MethodPost, "/api/v1/roles", env.AdminTok,
		map[string]any{"name": name, "actions": actions})
	require.Equalf(t, http.StatusCreated, r.Status, "body: %s", r.Body)
	var role domain.Role
	r.JSON(t, &role)
	return role
}

func (h *harness) bindRole(t *testing.T, env *tenantEnv, group, role uuid.UUID) {
	t.Helper()
	r := h.do(t, http.MethodPut, "/api/v1/groups/"+group.String()+"/roles/"+role.String(), env.AdminTok, nil)
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
}

func (h *harness) listWorkspaceIDs(t *testing.T, token string) map[string]bool {
	t.Helper()
	r := h.do(t, http.MethodGet, "/api/v1/workspaces?limit=200", token, nil)
	require.Equal(t, http.StatusOK, r.Status)
	var page struct {
		Data []domain.Workspace `json:"data"`
	}
	r.JSON(t, &page)
	out := map[string]bool{}
	for _, w := range page.Data {
		out[w.ID.String()] = true
	}
	return out
}

func userInput(env *tenantEnv, user, action, ws, urn string) authz.Input {
	return authz.Input{
		Subject:     authz.Subject{ID: user, Typ: domain.TypUser},
		Action:      action,
		WorkspaceID: ws,
		ResourceURN: urn,
		Tenant:      env.Tenant.String(),
	}
}

// ---- AC tests -----------------------------------------------------------------

// AC-1: content-group link controls visibility AND workspace-scoped decisions,
// with the 5s propagation SLA after unlink.
func TestAC01_WorkspaceVisibilityFollowsGroupLink(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	user := "u-" + uuid.NewString()[:8]
	userTok := h.mint(t, tokenSpec{Sub: user, Tenant: env.Tenant})

	ws := h.createWorkspace(t, env, "Marketing", false)
	cg := h.createGroup(t, env, "Marketing Content", domain.GroupTypeContent)
	pg := h.createGroup(t, env, "Insights Editors", domain.GroupTypePermission)
	role := h.createRole(t, env, "Dashboard Reader", []string{"chart.dashboard.read", "chart.dashboard.list"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)
	h.addMember(t, env, cg.ID, user)
	h.linkGroup(t, env, ws.ID, cg.ID)

	// Visible in listings.
	assert.True(t, h.listWorkspaceIDs(t, userTok)[ws.ID.String()], "linked workspace must appear")

	// Workspace-scoped action allows within 5s via the projection.
	in := userInput(env, user, "chart.dashboard.read", ws.ID.String(), "")
	waitFor(t, 5e9, func() bool { return h.decideRedis(t, in).Allowed }, "projection should allow after link")

	// Remove the link: workspace disappears and decisions deny within 5s.
	r := h.do(t, http.MethodDelete, "/api/v1/workspaces/"+ws.ID.String()+"/content-groups/"+cg.ID.String(), env.AdminTok, nil)
	require.Equal(t, http.StatusOK, r.Status)
	assert.False(t, h.listWorkspaceIDs(t, userTok)[ws.ID.String()], "unlinked workspace must disappear")
	waitFor(t, 5e9, func() bool { return !h.decideRedis(t, in).Allowed }, "projection should deny after unlink (<=5s SLA)")
}

// AC-2: public workspace + role-held workspace-scoped action allows with no
// content-group membership at all.
func TestAC02_PublicWorkspaceRoleAccess(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	user := "u-" + uuid.NewString()[:8]
	userTok := h.mint(t, tokenSpec{Sub: user, Tenant: env.Tenant})

	pg := h.createGroup(t, env, "Data Readers", domain.GroupTypePermission)
	role := h.createRole(t, env, "DS Reader", []string{"dataset.dataset.read"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)

	// Default workspace is public (RBC-FR-006) => assigned to every tenant user.
	assert.True(t, h.listWorkspaceIDs(t, userTok)[env.DefaultWs.String()], "public workspace visible")
	in := userInput(env, user, "dataset.dataset.read", env.DefaultWs.String(), "")
	waitFor(t, 5e9, func() bool { return h.decideRedis(t, in).Allowed }, "public workspace + role => allow")
}

// AC-5: the grant-integrity rule V1 shipped commented out is enforced:
// granting to a group NOT linked to the workspace is 422 GROUP_NOT_IN_WORKSPACE.
func TestAC05_GrantIntegrity(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ws := h.createWorkspace(t, env, "Grants WS", false)
	unlinked := h.createGroup(t, env, "Unlinked Content", domain.GroupTypeContent)

	urn := "wr:" + env.Tenant.String() + ":chart:dashboard/d-1"
	r := h.do(t, http.MethodPost, "/api/v1/grants", env.AdminTok, map[string]any{
		"workspace_id": ws.ID.String(), "resource_urn": urn,
		"subject": map[string]string{"type": "group", "id": unlinked.ID.String()},
		"level":   "viewer",
	})
	assert.Equal(t, http.StatusUnprocessableEntity, r.Status)
	assert.Equal(t, "GROUP_NOT_IN_WORKSPACE", r.errorCode(t))

	// After linking, the same grant succeeds.
	h.linkGroup(t, env, ws.ID, unlinked.ID)
	r = h.do(t, http.MethodPost, "/api/v1/grants", env.AdminTok, map[string]any{
		"workspace_id": ws.ID.String(), "resource_urn": urn,
		"subject": map[string]string{"type": "group", "id": unlinked.ID.String()},
		"level":   "viewer",
	})
	assert.Equalf(t, http.StatusCreated, r.Status, "body: %s", r.Body)
}

// AC-6: last-admin protection with audited super-admin override.
func TestAC06_LastAdminProtection(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t) // Admin group has exactly one member

	path := "/api/v1/groups/" + env.AdminGroup.String() + "/members/" + env.AdminUser
	r := h.do(t, http.MethodDelete, path, env.AdminTok, nil)
	assert.Equal(t, http.StatusConflict, r.Status)
	assert.Equal(t, "LAST_ADMIN", r.errorCode(t))

	// Override without a reason still refuses.
	superTok := h.mint(t, tokenSpec{Sub: env.AdminUser, Tenant: env.Tenant, Scopes: []string{"super_admin"}})
	r = h.do(t, http.MethodDelete, path, superTok, nil)
	assert.Equal(t, http.StatusConflict, r.Status)

	// Super-admin override with reason succeeds and is audited.
	r = h.do(t, http.MethodDelete, path, superTok, nil, map[string]string{"X-Override-Reason": "tenant offboarding #123"})
	require.Equalf(t, http.StatusNoContent, r.Status, "body: %s", r.Body)

	audits, err := h.store.OutboxEventsByType(context.Background(), env.Tenant.String(), events.EvLastAdminOverridden)
	require.NoError(t, err)
	require.NotEmpty(t, audits, "override must write an audit event")
	assert.Equal(t, "tenant offboarding #123", audits[len(audits)-1].Payload["reason"])
}

// AC-9: duplicate membership adds are idempotent; exactly one row exists.
func TestAC09_DuplicateMemberIdempotent(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	g := h.createGroup(t, env, "Dup Group", domain.GroupTypePermission)
	user := "u-dup"

	r1 := h.do(t, http.MethodPut, "/api/v1/groups/"+g.ID.String()+"/members/"+user, env.AdminTok, nil)
	assert.Equal(t, http.StatusCreated, r1.Status)
	r2 := h.do(t, http.MethodPut, "/api/v1/groups/"+g.ID.String()+"/members/"+user, env.AdminTok, nil)
	assert.Equal(t, http.StatusOK, r2.Status, "second call is a no-op 200")

	r := h.do(t, http.MethodGet, "/api/v1/groups/"+g.ID.String()+"/members", env.AdminTok, nil)
	require.Equal(t, http.StatusOK, r.Status)
	var page struct {
		Data []domain.Member `json:"data"`
	}
	r.JSON(t, &page)
	count := 0
	for _, m := range page.Data {
		if m.UserID == user {
			count++
		}
	}
	assert.Equal(t, 1, count, "exactly one membership row")
}

// AC-10: group deletion cascades content grants — zero orphans (the V1
// orphaned-ACL defect, fixed with FK + cascade).
func TestAC10_GroupDeleteCascadesGrants(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ws := h.createWorkspace(t, env, "Cascade WS", false)
	cg := h.createGroup(t, env, "Cascade Content", domain.GroupTypeContent)
	h.linkGroup(t, env, ws.ID, cg.ID)

	urn := "wr:" + env.Tenant.String() + ":dataset:dataset/ds-c1"
	r := h.do(t, http.MethodPost, "/api/v1/grants", env.AdminTok, map[string]any{
		"workspace_id": ws.ID.String(), "resource_urn": urn,
		"subject": map[string]string{"type": "group", "id": cg.ID.String()},
		"level":   "editor",
	})
	require.Equal(t, http.StatusCreated, r.Status)

	r = h.do(t, http.MethodDelete, "/api/v1/groups/"+cg.ID.String(), env.AdminTok, nil)
	require.Equal(t, http.StatusNoContent, r.Status)

	// Sweep query: zero orphan grants reference the deleted group.
	orphans, err := h.store.OrphanGrantCount(context.Background(), env.Tenant)
	require.NoError(t, err)
	assert.Zero(t, orphans)

	access, err := h.store.EffectiveAccess(context.Background(), env.Tenant, urn)
	require.NoError(t, err)
	assert.Empty(t, access, "grant rows cascade-deleted with the group")
}

// AC-11 + MASTER-FR-003/004: tenant A's admin reading tenant B's resources
// gets 404 (never 403) — enforced below the app by Postgres RLS.
func TestAC11_CrossTenantIs404(t *testing.T) {
	h := requireHarness(t)
	envA := h.newTenant(t)
	envB := h.newTenant(t)

	gB := h.createGroup(t, envB, "B Secret Group", domain.GroupTypePermission)
	wsB := h.createWorkspace(t, envB, "B Secret WS", false)

	for _, path := range []string{
		"/api/v1/groups/" + gB.ID.String(),
		"/api/v1/workspaces/" + wsB.ID.String(),
	} {
		r := h.do(t, http.MethodGet, path, envA.AdminTok, nil)
		assert.Equalf(t, http.StatusNotFound, r.Status, "path %s must 404 cross-tenant, got %d: %s", path, r.Status, r.Body)
	}

	// Mutations too: tenant A admin cannot delete B's group.
	r := h.do(t, http.MethodDelete, "/api/v1/groups/"+gB.ID.String(), envA.AdminTok, nil)
	assert.Equal(t, http.StatusNotFound, r.Status)

	// And B's group still exists for B.
	r = h.do(t, http.MethodGet, "/api/v1/groups/"+gB.ID.String(), envB.AdminTok, nil)
	assert.Equal(t, http.StatusOK, r.Status)
}

// AC-14: archived workspaces reject writes with 409 WORKSPACE_ARCHIVED but
// keep read decisions alive for previously-assigned users.
func TestAC14_ArchivedWorkspaceSemantics(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	user := "u-" + uuid.NewString()[:8]

	ws := h.createWorkspace(t, env, "Archive Me", false)
	cg := h.createGroup(t, env, "Archive Content", domain.GroupTypeContent)
	pg := h.createGroup(t, env, "Archive Perms", domain.GroupTypePermission)
	role := h.createRole(t, env, "DS RW", []string{"dataset.dataset.read", "dataset.dataset.update"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)
	h.addMember(t, env, cg.ID, user)
	h.linkGroup(t, env, ws.ID, cg.ID)

	readIn := userInput(env, user, "dataset.dataset.read", ws.ID.String(), "")
	writeIn := userInput(env, user, "dataset.dataset.update", ws.ID.String(), "")
	waitFor(t, 5e9, func() bool {
		return h.decideRedis(t, readIn).Allowed && h.decideRedis(t, writeIn).Allowed
	}, "pre-archive both allow")

	r := h.do(t, http.MethodPost, "/api/v1/workspaces/"+ws.ID.String()+"/archive", env.AdminTok, nil)
	require.Equal(t, http.StatusOK, r.Status)

	// API writes: 409 WORKSPACE_ARCHIVED.
	r = h.do(t, http.MethodPatch, "/api/v1/workspaces/"+ws.ID.String(), env.AdminTok,
		map[string]any{"description": "nope"})
	assert.Equal(t, http.StatusConflict, r.Status)
	assert.Equal(t, "WORKSPACE_ARCHIVED", r.errorCode(t))

	urn := "wr:" + env.Tenant.String() + ":dataset:dataset/ds-a1"
	r = h.do(t, http.MethodPost, "/api/v1/grants", env.AdminTok, map[string]any{
		"workspace_id": ws.ID.String(), "resource_urn": urn,
		"subject": map[string]string{"type": "group", "id": cg.ID.String()}, "level": "viewer",
	})
	assert.Equal(t, http.StatusConflict, r.Status)
	assert.Equal(t, "WORKSPACE_ARCHIVED", r.errorCode(t))

	// Decisions: reads still allow for previously-assigned users; writes deny.
	waitFor(t, 5e9, func() bool { return !h.decideRedis(t, writeIn).Allowed }, "write denies post-archive")
	assert.True(t, h.decideRedis(t, readIn).Allowed, "read still allowed post-archive")
	assert.Equal(t, authz.ReasonWorkspaceArchived, h.decideRedis(t, writeIn).Reason)

	// Excluded from default listings; visible with ?archived=only.
	assert.False(t, h.listWorkspaceIDs(t, env.AdminTok)[ws.ID.String()])
	r = h.do(t, http.MethodGet, "/api/v1/workspaces?archived=only&limit=200", env.AdminTok, nil)
	require.Equal(t, http.StatusOK, r.Status)
	var page struct {
		Data []domain.Workspace `json:"data"`
	}
	r.JSON(t, &page)
	found := false
	for _, w := range page.Data {
		if w.ID == ws.ID {
			found = true
		}
	}
	assert.True(t, found)

	// Restore reverses everything.
	r = h.do(t, http.MethodPost, "/api/v1/workspaces/"+ws.ID.String()+"/restore", env.AdminTok, nil)
	require.Equal(t, http.StatusOK, r.Status)
	waitFor(t, 5e9, func() bool { return h.decideRedis(t, writeIn).Allowed }, "write allowed after restore")
}

// ---- non-AC API behaviors ------------------------------------------------------

// MASTER-FR-025: Idempotency-Key replay.
func TestIntegration_IdempotencyReplay(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	key := uuid.NewString()

	r1 := h.do(t, http.MethodPost, "/api/v1/workspaces", env.AdminTok,
		map[string]any{"name": "Idem WS"}, map[string]string{"Idempotency-Key": key})
	require.Equal(t, http.StatusCreated, r1.Status)

	r2 := h.do(t, http.MethodPost, "/api/v1/workspaces", env.AdminTok,
		map[string]any{"name": "Idem WS"}, map[string]string{"Idempotency-Key": key})
	assert.Equal(t, http.StatusCreated, r2.Status, "replayed original status")
	assert.Equal(t, "true", r2.Header.Get("Idempotency-Replayed"))
	assert.JSONEq(t, string(r1.Body), string(r2.Body), "replayed original body")

	// Same payload without the key hits the uniqueness rule -> 409.
	r3 := h.do(t, http.MethodPost, "/api/v1/workspaces", env.AdminTok, map[string]any{"name": "Idem WS"})
	assert.Equal(t, http.StatusConflict, r3.Status)
}

// RBC-FR-001: per-tenant unique names, case-insensitive; other tenants unaffected.
func TestIntegration_WorkspaceNameUniquePerTenant(t *testing.T) {
	h := requireHarness(t)
	envA := h.newTenant(t)
	envB := h.newTenant(t)

	h.createWorkspace(t, envA, "Shared Name", false)
	r := h.do(t, http.MethodPost, "/api/v1/workspaces", envA.AdminTok, map[string]any{"name": "shared name"})
	assert.Equal(t, http.StatusConflict, r.Status, "case-insensitive uniqueness")

	// Same name in another tenant is fine (V1's global uniqueness corrected).
	h.createWorkspace(t, envB, "Shared Name", false)
}

// BR-4 + RBC-FR-013/020/021: role lifecycle constraints.
func TestIntegration_RoleLifecycle(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)

	role := h.createRole(t, env, "Temp Role", []string{"dataset.dataset.read"})
	pg := h.createGroup(t, env, "Role Holders", domain.GroupTypePermission)
	h.bindRole(t, env, pg.ID, role.ID)

	r := h.do(t, http.MethodDelete, "/api/v1/roles/"+role.ID.String(), env.AdminTok, nil)
	assert.Equal(t, http.StatusConflict, r.Status)
	assert.Equal(t, "ROLE_IN_USE", r.errorCode(t))

	r = h.do(t, http.MethodDelete, "/api/v1/groups/"+pg.ID.String()+"/roles/"+role.ID.String(), env.AdminTok, nil)
	require.Equal(t, http.StatusNoContent, r.Status)
	r = h.do(t, http.MethodDelete, "/api/v1/roles/"+role.ID.String(), env.AdminTok, nil)
	assert.Equal(t, http.StatusNoContent, r.Status)

	// Unknown catalog actions are rejected.
	r = h.do(t, http.MethodPost, "/api/v1/roles", env.AdminTok,
		map[string]any{"name": "Bad Role", "actions": []string{"made.up.read"}})
	assert.Equal(t, http.StatusBadRequest, r.Status)

	// System roles are immutable.
	var systemRoleID string
	list := h.do(t, http.MethodGet, "/api/v1/roles?limit=200", env.AdminTok, nil)
	require.Equal(t, http.StatusOK, list.Status)
	var rolesPage struct {
		Data []domain.Role `json:"data"`
	}
	list.JSON(t, &rolesPage)
	for _, ro := range rolesPage.Data {
		if ro.System && ro.Name == domain.RoleDataUser {
			systemRoleID = ro.ID.String()
		}
	}
	require.NotEmpty(t, systemRoleID)
	r = h.do(t, http.MethodPut, "/api/v1/roles/"+systemRoleID+"/actions", env.AdminTok,
		map[string]any{"actions": []string{"dataset.dataset.read"}})
	assert.Equal(t, http.StatusConflict, r.Status)
	assert.Equal(t, "SYSTEM_IMMUTABLE", r.errorCode(t))
}

// RBC-FR-016: bulk membership with partial-failure report.
func TestIntegration_BulkMembers(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	g := h.createGroup(t, env, "Bulk Group", domain.GroupTypePermission)

	r := h.do(t, http.MethodPost, "/api/v1/groups/"+g.ID.String()+"/members:bulk", env.AdminTok,
		map[string]any{"operations": []map[string]string{
			{"op": "add", "user_id": "u-b1"},
			{"op": "add", "user_id": "u-b2"},
			{"op": "remove", "user_id": "u-not-there"}, // no-op remove: ok
			{"op": "frobnicate", "user_id": "u-b3"},    // invalid op: fails
		}})
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
	var out struct {
		Succeeded int `json:"succeeded"`
		Failed    int `json:"failed"`
	}
	r.JSON(t, &out)
	assert.Equal(t, 3, out.Succeeded)
	assert.Equal(t, 1, out.Failed)
}
