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
	"github.com/windrose-ai/rbac-service/internal/projection"
	"github.com/windrose-ai/rbac-service/internal/store"
)

func (h *harness) serviceToken(t *testing.T, tenant uuid.UUID) string {
	return h.mint(t, tokenSpec{Sub: "svc-test", Tenant: tenant, Typ: domain.TypService})
}

func (h *harness) check(t *testing.T, tenant uuid.UUID, body map[string]any) authz.Decision {
	t.Helper()
	r := h.do(t, http.MethodPost, "/api/v1/authz/check", h.serviceToken(t, tenant), body)
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
	var d authz.Decision
	r.JSON(t, &d)
	return d
}

// AC-3: workspace-context validation on the fallback path (V1
// workspace_dependent semantics with stable reason codes).
func TestAC03_WorkspaceContextValidation(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)

	// workspace-scoped action without workspace context.
	d := h.check(t, env.Tenant, map[string]any{
		"subject": map[string]any{"id": env.AdminUser, "typ": "user"},
		"action":  "dataset.dataset.read",
		"tenant":  env.Tenant.String(),
	})
	assert.False(t, d.Allowed)
	assert.Equal(t, authz.ReasonWorkspaceCtxRequired, d.Reason)

	// tenant-scoped action carrying a workspace context.
	d = h.check(t, env.Tenant, map[string]any{
		"subject":      map[string]any{"id": env.AdminUser, "typ": "user"},
		"action":       "rbac.group.list",
		"workspace_id": env.DefaultWs.String(),
		"tenant":       env.Tenant.String(),
	})
	assert.False(t, d.Allowed)
	assert.Equal(t, authz.ReasonWorkspaceCtxForbid, d.Reason)

	// Well-formed request allows (admin).
	d = h.check(t, env.Tenant, map[string]any{
		"subject":      map[string]any{"id": env.AdminUser, "typ": "user"},
		"action":       "dataset.dataset.read",
		"workspace_id": env.DefaultWs.String(),
		"tenant":       env.Tenant.String(),
	})
	assert.True(t, d.Allowed)
}

// AC-7: OBO intersection — agent scopes exclude an action the user could
// perform; deny + explain shows scope_excluded.
func TestAC07_OBOScopeExcluded(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	user := "u-" + uuid.NewString()[:8]

	pg := h.createGroup(t, env, "Case Perms", domain.GroupTypePermission)
	role := h.createRole(t, env, "Assigner", []string{"case.case.assign"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)

	// The user CAN assign (via fallback path).
	d := h.check(t, env.Tenant, map[string]any{
		"subject":      map[string]any{"id": user, "typ": "user"},
		"action":       "case.case.assign",
		"workspace_id": env.DefaultWs.String(),
		"tenant":       env.Tenant.String(),
	})
	require.True(t, d.Allowed, "underlying user can assign")

	// The OBO agent with excluding scopes cannot.
	d = h.check(t, env.Tenant, map[string]any{
		"subject": map[string]any{
			"id": "agent-1", "typ": "agent_obo", "obo_sub": user,
			"scopes": []string{"chart.dashboard.read"},
		},
		"action":       "case.case.assign",
		"workspace_id": env.DefaultWs.String(),
		"tenant":       env.Tenant.String(),
	})
	assert.False(t, d.Allowed)
	assert.Equal(t, authz.ReasonScopeExcluded, d.Reason)

	// Explain shows the scope_excluded step (US-7).
	r := h.do(t, http.MethodPost, "/api/v1/authz/explain", env.AdminTok, map[string]any{
		"user_id": user, "typ": "agent_obo", "scopes": []string{"chart.dashboard.read"},
		"action": "case.case.assign", "workspace_id": env.DefaultWs.String(),
	})
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
	var exp authz.Explanation
	r.JSON(t, &exp)
	assert.False(t, exp.Allowed)
	found := false
	for _, step := range exp.Chain {
		if step.Type == "scope_excluded" {
			found = true
		}
	}
	assert.True(t, found, "chain contains scope_excluded, got %+v", exp.Chain)
}

// RBC-FR-046: the explain chain carries the full grant provenance.
func TestIntegration_ExplainFullChain(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	user := "u-" + uuid.NewString()[:8]

	ws := h.createWorkspace(t, env, "Explain WS", false)
	cg := h.createGroup(t, env, "Marketing Content", domain.GroupTypeContent)
	pg := h.createGroup(t, env, "Insights Editors", domain.GroupTypePermission)
	role := h.createRole(t, env, "Dash Updater", []string{"chart.dashboard.update"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)
	h.addMember(t, env, cg.ID, user)
	h.linkGroup(t, env, ws.ID, cg.ID)

	urn := "wr:" + env.Tenant.String() + ":chart:dashboard/d-9"
	r := h.do(t, http.MethodPost, "/api/v1/grants", env.AdminTok, map[string]any{
		"workspace_id": ws.ID.String(), "resource_urn": urn,
		"subject": map[string]string{"type": "group", "id": cg.ID.String()}, "level": "editor",
	})
	require.Equal(t, http.StatusCreated, r.Status)

	r = h.do(t, http.MethodPost, "/api/v1/authz/explain", env.AdminTok, map[string]any{
		"user_id": user, "action": "chart.dashboard.update",
		"resource_urn": urn, "workspace_id": ws.ID.String(),
	})
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
	var exp authz.Explanation
	r.JSON(t, &exp)
	assert.True(t, exp.Allowed)

	types := map[string][]authz.ChainStep{}
	for _, s := range exp.Chain {
		types[s.Type] = append(types[s.Type], s)
	}
	require.NotEmpty(t, types["membership"], "membership step present")
	assert.Equal(t, "Insights Editors", types["membership"][0].Group)
	require.NotEmpty(t, types["role"], "role step present")
	assert.Equal(t, "Dash Updater", types["role"][0].Role)
	require.NotEmpty(t, types["workspace_assignment"], "assignment step present")
	assert.Equal(t, "Marketing Content", types["workspace_assignment"][0].ViaGroup)
	require.NotEmpty(t, types["grant"], "grant step present")
	assert.Equal(t, "editor", types["grant"][0].Level)
	assert.Equal(t, "group:Marketing Content", types["grant"][0].Subject)
}

// AC-8: Redis loss — the SQL fallback still answers, re-warms keys, and the
// fallback-rate metric moves.
func TestAC08_RedisFlushFallback(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	user := "u-" + uuid.NewString()[:8]

	pg := h.createGroup(t, env, "Flush Perms", domain.GroupTypePermission)
	role := h.createRole(t, env, "Flush Role", []string{"rbac.group.list"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)
	waitFor(t, 5e9, func() bool {
		return h.redis.Exists(ctx, projection.KeyActions(env.Tenant.String(), user)).Val() == 1
	}, "projection warm before flush")

	// Simulate Redis loss.
	require.NoError(t, h.redis.FlushDB(ctx).Err())
	_, found, err := h.reader.TenantActions(ctx, env.Tenant.String(), user)
	require.NoError(t, err)
	require.False(t, found, "keys gone after flush")

	before := h.fallback.Load()
	d := h.check(t, env.Tenant, map[string]any{
		"subject": map[string]any{"id": user, "typ": "user"},
		"action":  "rbac.group.list",
		"tenant":  env.Tenant.String(),
	})
	assert.True(t, d.Allowed, "fallback answers from SQL ground truth")
	assert.Greater(t, h.fallback.Load(), before, "fallback metric increments (alarm SLI)")

	// The fallback warmed the user's keys back into Redis.
	actions, found, err := h.reader.TenantActions(ctx, env.Tenant.String(), user)
	require.NoError(t, err)
	require.True(t, found, "keys re-warmed by fallback")
	assert.Contains(t, actions, "rbac.group.list")

	// Restore the global catalog key for subsequent tests (in production the
	// deploy-time registration and full rebuild repopulate it).
	catalog, err := h.store.CatalogMap(ctx)
	require.NoError(t, err)
	v, err := h.store.NextVersion(ctx)
	require.NoError(t, err)
	require.NoError(t, h.writer.WriteCatalog(ctx, catalog, v))
}

// AC-13: `*.created` event -> implicit owner grant with implicit_creator
// provenance in the effective-access list.
func TestAC13_ImplicitCreatorGrant(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	creator := "u-creator-" + uuid.NewString()[:8]
	urn := "wr:" + env.Tenant.String() + ":dataset:dataset/ds-implicit"

	// Simulate the dataset-service event through the consumer handler.
	handler := &events.Handler{Store: &store.ConsumerAdapter{S: h.store, DropProjection: h.writer.DropUser}}
	env2 := events.NewEnvelope("dataset.created", env.Tenant,
		events.Actor{Type: "user", ID: creator}, urn, "trace-ac13",
		map[string]any{"workspace_id": env.DefaultWs.String()})
	require.NoError(t, handler.HandleEvent(ctx, env2))

	// Replay is idempotent (consumer dedup + upsert semantics).
	require.NoError(t, handler.HandleEvent(ctx, env2))

	r := h.do(t, http.MethodGet, "/api/v1/grants?resource_urn="+urn, env.AdminTok, nil)
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
	var out struct {
		Data []store.EffectiveAccessEntry `json:"data"`
	}
	r.JSON(t, &out)
	require.Len(t, out.Data, 1, "exactly one implicit grant despite replay")
	assert.Equal(t, domain.SubjectUser, out.Data[0].SubjectType)
	assert.Equal(t, creator, out.Data[0].SubjectID)
	assert.Equal(t, domain.LevelOwner, out.Data[0].Level)
	assert.Equal(t, "implicit_creator", out.Data[0].Provenance)

	// The creator's decisions honor the owner grant within the SLA.
	in := userInput(env, creator, "dataset.dataset.delete", env.DefaultWs.String(), urn)
	waitFor(t, 5e9, func() bool { return h.decideRedis(t, in).Allowed }, "owner grant allows delete")
}

// The authz matrix over live HTTP: every mutating endpoint denies a plain
// user with no roles (deny-by-default at the API layer, BR-12/MASTER-071).
func TestIntegration_APIDeniesUnprivilegedUser(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	nobody := h.mint(t, tokenSpec{Sub: "u-nobody", Tenant: env.Tenant})

	probes := []struct {
		method, path string
		body         map[string]any
	}{
		{http.MethodPost, "/api/v1/workspaces", map[string]any{"name": "x"}},
		{http.MethodPost, "/api/v1/groups", map[string]any{"name": "x", "group_type": "content"}},
		{http.MethodPost, "/api/v1/roles", map[string]any{"name": "x"}},
		{http.MethodPost, "/api/v1/grants", map[string]any{}},
		{http.MethodGet, "/api/v1/groups", nil},
		{http.MethodPost, "/api/v1/authz/explain", map[string]any{"user_id": "u", "action": "a.b.read"}},
		{http.MethodPost, "/api/v1/admin/projection/rebuild?tenant=" + env.Tenant.String(), nil},
	}
	for _, p := range probes {
		r := h.do(t, p.method, p.path, nobody, p.body)
		assert.Equalf(t, http.StatusForbidden, r.Status, "%s %s must deny, got %d: %s", p.method, p.path, r.Status, r.Body)
	}

	// And entirely unauthenticated requests are 401.
	r := h.do(t, http.MethodGet, "/api/v1/workspaces", "", nil)
	assert.Equal(t, http.StatusUnauthorized, r.Status)

	// The check endpoint requires service identity.
	r = h.do(t, http.MethodPost, "/api/v1/authz/check", nobody,
		map[string]any{"subject": map[string]any{"id": "u"}, "action": "a.b.read", "tenant": env.Tenant.String()})
	assert.Equal(t, http.StatusForbidden, r.Status)
}
