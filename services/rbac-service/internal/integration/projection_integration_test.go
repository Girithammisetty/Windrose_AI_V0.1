package integration

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/projection"
)

// End-to-end recompute: mutation -> transactional dirty marker -> worker ->
// Redis keys, verified at the raw key level (RBC-FR-040/041/042).
func TestIntegration_ProjectionRecomputeEndToEnd(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	user := "u-" + uuid.NewString()[:8]

	ws := h.createWorkspace(t, env, "Proj WS", false)
	cg := h.createGroup(t, env, "Proj Content", domain.GroupTypeContent)
	pg := h.createGroup(t, env, "Proj Perms", domain.GroupTypePermission)
	role := h.createRole(t, env, "Proj Role", []string{"dataset.dataset.read", "rbac.group.list"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)
	h.addMember(t, env, cg.ID, user)
	h.linkGroup(t, env, ws.ID, cg.ID)

	tenant := env.Tenant.String()

	// Wait until the FINAL recompute (after the link mutation) has landed —
	// keyed on the linked-workspace key so the subsequent raw reads are stable
	// even under -race timing. The projection is eventually consistent, so this
	// is the correct synchronization point rather than a bare read.
	var wsv struct {
		Actions  []string `json:"actions"`
		Archived bool     `json:"archived"`
	}
	waitFor(t, 5e9, func() bool {
		aRaw, err := h.redis.Get(ctx, projection.KeyActions(tenant, user)).Result()
		if err != nil {
			return false
		}
		var av struct {
			V       int64    `json:"v"`
			Actions []string `json:"actions"`
		}
		if json.Unmarshal([]byte(aRaw), &av) != nil || av.V == 0 ||
			len(av.Actions) != 1 || av.Actions[0] != "rbac.group.list" {
			return false
		}
		wRaw, err := h.redis.Get(ctx, projection.KeyWorkspace(tenant, user, ws.ID.String())).Result()
		if err != nil {
			return false
		}
		wsv = struct {
			Actions  []string `json:"actions"`
			Archived bool     `json:"archived"`
		}{}
		if json.Unmarshal([]byte(wRaw), &wsv) != nil {
			return false
		}
		return len(wsv.Actions) == 1 && wsv.Actions[0] == "dataset.dataset.read"
	}, "tenant actions + linked workspace keys materialized")

	assert.Equal(t, []string{"dataset.dataset.read"}, wsv.Actions)
	assert.False(t, wsv.Archived)

	// Also materialized for the default public workspace (assignment rule).
	_, err := h.redis.Get(ctx, projection.KeyWorkspace(tenant, user, env.DefaultWs.String())).Result()
	assert.NoError(t, err, "public workspace key exists")

	// flags key present, admin=false.
	raw, err := h.redis.Get(ctx, projection.KeyFlags(tenant, user)).Result()
	require.NoError(t, err)
	var fv struct {
		Admin bool `json:"admin"`
	}
	require.NoError(t, json.Unmarshal([]byte(raw), &fv))
	assert.False(t, fv.Admin)

	// TTL self-healing (RBC-FR-047): ~24h TTL on entries.
	ttl, err := h.redis.TTL(ctx, projection.KeyActions(tenant, user)).Result()
	require.NoError(t, err)
	assert.Greater(t, ttl, 23*time.Hour)
	assert.LessOrEqual(t, ttl, 24*time.Hour)

	// Dirty queue drains.
	waitFor(t, 5e9, func() bool {
		n, err := h.store.DirtyDepth(ctx)
		return err == nil && n == 0
	}, "dirty queue drained")
}

// RBC-FR-042: pub/sub perm.invalidate notifies OPA caches within the 5s SLA.
func TestIntegration_InvalidationPubSub(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	user := "u-" + uuid.NewString()[:8]

	sub := h.redis.Subscribe(ctx, projection.InvalidateChannel)
	defer sub.Close()
	_, err := sub.Receive(ctx) // subscription confirmation
	require.NoError(t, err)
	ch := sub.Channel()

	pg := h.createGroup(t, env, "PubSub Perms", domain.GroupTypePermission)
	h.addMember(t, env, pg.ID, user)

	deadline := time.After(5 * time.Second)
	for {
		select {
		case msg := <-ch:
			var payload projection.InvalidateMessage
			require.NoError(t, json.Unmarshal([]byte(msg.Payload), &payload))
			if payload.Tenant == env.Tenant.String() {
				for _, u := range payload.Users {
					if u == user {
						return // success
					}
				}
			}
		case <-deadline:
			t.Fatal("no perm.invalidate for the affected user within 5s")
		}
	}
}

// AC-4: editing a role's action set propagates to decisions within 5s
// (measured via a canary user).
func TestAC04_RoleEditPropagatesWithin5s(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	canary := "canary-" + uuid.NewString()[:8]

	pg := h.createGroup(t, env, "Canary Perms", domain.GroupTypePermission)
	role := h.createRole(t, env, "Canary Role", []string{"dataset.dataset.read"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, canary)

	readIn := userInput(env, canary, "dataset.dataset.read", env.DefaultWs.String(), "")
	updIn := userInput(env, canary, "dataset.dataset.update", env.DefaultWs.String(), "")
	waitFor(t, 5e9, func() bool { return h.decideRedis(t, readIn).Allowed }, "baseline allow")
	require.False(t, h.decideRedis(t, updIn).Allowed, "baseline: update denied")

	// Widen the role.
	start := time.Now()
	r := h.do(t, http.MethodPut, "/api/v1/roles/"+role.ID.String()+"/actions", env.AdminTok,
		map[string]any{"actions": []string{"dataset.dataset.read", "dataset.dataset.update"}})
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
	waitFor(t, 5e9, func() bool { return h.decideRedis(t, updIn).Allowed }, "role edit visible within 5s")
	assert.Less(t, time.Since(start), 5*time.Second)

	// Narrow it again: revocation also propagates within the SLA.
	r = h.do(t, http.MethodPut, "/api/v1/roles/"+role.ID.String()+"/actions", env.AdminTok,
		map[string]any{"actions": []string{"dataset.dataset.read"}})
	require.Equal(t, http.StatusOK, r.Status)
	waitFor(t, 5e9, func() bool { return !h.decideRedis(t, updIn).Allowed }, "revocation visible within 5s")
}

// AC-12: weekly verification detects injected drift and repairs it; clean
// projections report drift = 0.
func TestAC12_VerifyDetectsAndRepairsDrift(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	user := "u-" + uuid.NewString()[:8]

	pg := h.createGroup(t, env, "Verify Perms", domain.GroupTypePermission)
	role := h.createRole(t, env, "Verify Role", []string{"rbac.group.list"})
	h.bindRole(t, env, pg.ID, role.ID)
	h.addMember(t, env, pg.ID, user)
	waitFor(t, 5e9, func() bool {
		_, err := h.redis.Get(ctx, projection.KeyActions(env.Tenant.String(), user)).Result()
		return err == nil
	}, "projection materialized")

	superTok := h.mint(t, tokenSpec{Sub: env.AdminUser, Tenant: env.Tenant, Scopes: []string{"super_admin"}})
	verifyPath := "/api/v1/admin/projection/verify?tenant=" + env.Tenant.String()

	// Clean state: drift 0.
	r := h.do(t, http.MethodPost, verifyPath, superTok, nil)
	require.Equalf(t, http.StatusOK, r.Status, "body: %s", r.Body)
	var out struct {
		Drift        int      `json:"drift"`
		UsersChecked int      `json:"users_checked"`
		Repaired     []string `json:"repaired_users"`
	}
	r.JSON(t, &out)
	assert.Zero(t, out.Drift, "no drift on a healthy projection")
	assert.Positive(t, out.UsersChecked)

	// Inject drift: tamper the user's actions key with wrong content.
	tampered := `{"v":1,"computed_at":"2020-01-01T00:00:00Z","actions":["dataset.dataset.delete"]}`
	require.NoError(t, h.redis.Set(ctx, projection.KeyActions(env.Tenant.String(), user), tampered, time.Hour).Err())

	r = h.do(t, http.MethodPost, verifyPath, superTok, nil)
	require.Equal(t, http.StatusOK, r.Status)
	r.JSON(t, &out)
	assert.Equal(t, 1, out.Drift, "injected drift detected")
	assert.Contains(t, out.Repaired, user, "drift repaired")

	// Post-repair: drift back to 0 and the key holds ground truth again.
	r = h.do(t, http.MethodPost, verifyPath, superTok, nil)
	require.Equal(t, http.StatusOK, r.Status)
	r.JSON(t, &out)
	assert.Zero(t, out.Drift)
	raw, err := h.redis.Get(ctx, projection.KeyActions(env.Tenant.String(), user)).Result()
	require.NoError(t, err)
	assert.Contains(t, raw, "rbac.group.list")
	assert.NotContains(t, raw, "dataset.dataset.delete")
}

// RBC-FR-043: admin rebuild enqueues every known tenant user.
func TestIntegration_ProjectionRebuild(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	pg := h.createGroup(t, env, "Rebuild Perms", domain.GroupTypePermission)
	h.addMember(t, env, pg.ID, "u-r1")
	h.addMember(t, env, pg.ID, "u-r2")

	superTok := h.mint(t, tokenSpec{Sub: env.AdminUser, Tenant: env.Tenant, Scopes: []string{"super_admin"}})
	r := h.do(t, http.MethodPost, "/api/v1/admin/projection/rebuild?tenant="+env.Tenant.String(), superTok, nil)
	require.Equalf(t, http.StatusAccepted, r.Status, "body: %s", r.Body)
	var out struct {
		OperationID   string `json:"operation_id"`
		UsersEnqueued int64  `json:"users_enqueued"`
	}
	r.JSON(t, &out)
	assert.NotEmpty(t, out.OperationID)
	assert.GreaterOrEqual(t, out.UsersEnqueued, int64(3), "admin + u-r1 + u-r2")

	// Worker drains the rebuild and keys exist for all users.
	ctx := context.Background()
	waitFor(t, 10e9, func() bool {
		for _, u := range []string{"u-r1", "u-r2", env.AdminUser} {
			if h.redis.Exists(ctx, projection.KeyActions(env.Tenant.String(), u)).Val() == 0 {
				return false
			}
		}
		return true
	}, "rebuild rewrote all users")
}

// RBC-FR-048: a stale writer (older version) can never clobber a newer
// projection — versioned last-writer-wins at the Redis layer.
func TestIntegration_VersionedLastWriterWins(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	user := "u-lww"

	newer := projection.Flat{
		TenantID: env.Tenant, UserID: user,
		TenantActions: []string{"rbac.group.list"},
		Version:       1000, ComputedAt: time.Now(),
	}
	require.NoError(t, h.writer.WriteUser(ctx, newer))

	older := projection.Flat{
		TenantID: env.Tenant, UserID: user,
		TenantActions: []string{"audit.log.export"}, // stale content
		Version:       999, ComputedAt: time.Now(),
	}
	require.NoError(t, h.writer.WriteUser(ctx, older))

	actions, found, err := h.reader.TenantActions(ctx, env.Tenant.String(), user)
	require.NoError(t, err)
	require.True(t, found)
	assert.Equal(t, []string{"rbac.group.list"}, actions, "older write must lose")
}

// Regression for the HIGH resurrection defect: a subsidiary key (grant/ws)
// removed by a NEWER snapshot must NOT be recreated by a STALE older writer.
// Before the fix, GC used a raw DELETE, so the stale writer's version-guarded
// SET found no value to compare against and recreated the revoked key.
func TestIntegration_StaleWriterCannotResurrectRevokedGrant(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	tenant := env.Tenant.String()
	user := "u-resurrect"

	urn := "wr:" + tenant + ":chart:dashboard/d-resurrect"
	hash := domain.URNHash(urn)
	wsID := env.DefaultWs

	granted := func(version int64) projection.Flat {
		return projection.Flat{
			TenantID: env.Tenant, UserID: user,
			Resources: map[string]projection.ResourceEntry{
				hash: {URN: urn, Level: "editor", WorkspaceID: wsID.String()},
			},
			Version: version, ComputedAt: time.Now(),
		}
	}
	revoked := func(version int64) projection.Flat {
		return projection.Flat{
			TenantID: env.Tenant, UserID: user,
			Resources: map[string]projection.ResourceEntry{}, // grant gone
			Version:   version, ComputedAt: time.Now(),
		}
	}

	// v5: grant present.
	require.NoError(t, h.writer.WriteUser(ctx, granted(5)))
	_, found, err := h.reader.Resource(ctx, tenant, user, hash)
	require.NoError(t, err)
	require.True(t, found, "grant present at v5")

	// v11: revocation snapshot removes the grant key (now a version-11 tombstone).
	require.NoError(t, h.writer.WriteUser(ctx, revoked(11)))
	_, found, err = h.reader.Resource(ctx, tenant, user, hash)
	require.NoError(t, err)
	require.False(t, found, "grant revoked at v11")

	// v10: a STALE older writer that still sees the grant attempts to recreate
	// the key. The version-carrying tombstone (v11 >= 10) must block it.
	require.NoError(t, h.writer.WriteUser(ctx, granted(10)))
	entry, found, err := h.reader.Resource(ctx, tenant, user, hash)
	require.NoError(t, err)
	assert.Falsef(t, found, "REVOKED GRANT RESURRECTED: stale v10 writer recreated a key removed at v11 (level=%s)", entry.Level)

	// And a legitimate re-grant at a NEWER version still works (tombstone loses).
	require.NoError(t, h.writer.WriteUser(ctx, granted(20)))
	_, found, err = h.reader.Resource(ctx, tenant, user, hash)
	require.NoError(t, err)
	assert.True(t, found, "legitimate re-grant at v20 restores the key")
}

// The same resurrection vector for a workspace-assignment (ws:{id}) key.
func TestIntegration_StaleWriterCannotResurrectWorkspaceAssignment(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	tenant := env.Tenant.String()
	user := "u-ws-resurrect"
	wsID := uuid.New()

	assigned := func(version int64) projection.Flat {
		return projection.Flat{
			TenantID: env.Tenant, UserID: user,
			WorkspaceActions: map[uuid.UUID]projection.WorkspaceEntry{
				wsID: {Actions: []string{"dataset.dataset.read"}},
			},
			Version: version, ComputedAt: time.Now(),
		}
	}
	unassigned := func(version int64) projection.Flat {
		return projection.Flat{
			TenantID: env.Tenant, UserID: user,
			WorkspaceActions: map[uuid.UUID]projection.WorkspaceEntry{},
			Version:          version, ComputedAt: time.Now(),
		}
	}

	require.NoError(t, h.writer.WriteUser(ctx, assigned(5)))
	_, found, err := h.reader.Workspace(ctx, tenant, user, wsID.String())
	require.NoError(t, err)
	require.True(t, found)

	require.NoError(t, h.writer.WriteUser(ctx, unassigned(11)))
	_, found, err = h.reader.Workspace(ctx, tenant, user, wsID.String())
	require.NoError(t, err)
	require.False(t, found, "assignment removed at v11")

	require.NoError(t, h.writer.WriteUser(ctx, assigned(10)))
	_, found, err = h.reader.Workspace(ctx, tenant, user, wsID.String())
	require.NoError(t, err)
	assert.False(t, found, "stale v10 must not resurrect the workspace assignment")
}

// The per-user recompute mutex (RBC-FR-048) serializes holders: a second
// Acquire fails while the first holds it, and succeeds after Release.
func TestIntegration_UserLockSerializesRecompute(t *testing.T) {
	h := requireHarness(t)
	env := h.newTenant(t)
	ctx := context.Background()
	tenant := env.Tenant.String()
	user := "u-lock"

	tok1, ok, err := h.lock.Acquire(ctx, tenant, user)
	require.NoError(t, err)
	require.True(t, ok, "first acquire succeeds")

	_, ok2, err := h.lock.Acquire(ctx, tenant, user)
	require.NoError(t, err)
	assert.False(t, ok2, "second acquire blocked while held")

	h.lock.Release(ctx, tenant, user, tok1)

	tok3, ok3, err := h.lock.Acquire(ctx, tenant, user)
	require.NoError(t, err)
	assert.True(t, ok3, "acquire succeeds after release")
	h.lock.Release(ctx, tenant, user, tok3)

	// A stale holder whose token no longer matches cannot release a successor.
	tokA, _, _ := h.lock.Acquire(ctx, tenant, user)
	h.lock.Release(ctx, tenant, user, "not-the-token")
	_, ok4, _ := h.lock.Acquire(ctx, tenant, user)
	assert.False(t, ok4, "wrong-token release must not free the lock")
	h.lock.Release(ctx, tenant, user, tokA)
}
