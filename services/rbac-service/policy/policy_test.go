// Rego policy tests: the policy bundle must mirror internal/authz/decide.go.
// The fixture and the case table are the same shape as decide_test.go, so a
// semantic divergence between Go and Rego fails here.
package policy_test

import (
	"context"
	"os"
	"testing"

	"github.com/open-policy-agent/opa/v1/rego"
	"github.com/open-policy-agent/opa/v1/storage/inmem"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/rbac-service/internal/domain"
)

const (
	tenantA = "00000000-0000-0000-0000-00000000000a"
	tenantB = "00000000-0000-0000-0000-00000000000b"
	ws1     = "00000000-0000-0000-0000-000000000101"
	ws2     = "00000000-0000-0000-0000-000000000102"
	wsArch  = "00000000-0000-0000-0000-000000000103"
	dashURN = "wr:t-a:chart:dashboard/d-9"
)

func catalogData() map[string]any {
	cat := map[string]any{}
	for a, scoped := range map[string]bool{
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
	} {
		cat[a] = map[string]any{"workspace_scoped": scoped}
	}
	return cat
}

// permData mirrors the Redis projection for the same fixture users as
// decide_test.go: u-1 (roles + editor grant), admin-1, wsadmin-1.
func permData(autonomous bool) map[string]any {
	wsActions := []any{"case.case.assign", "chart.dashboard.update", "dataset.dataset.read"}
	archActions := []any{"dataset.dataset.read"} // read verbs only survive archive
	return map[string]any{
		"perm": map[string]any{
			"catalog": catalogData(),
			"tenants": map[string]any{
				tenantA: map[string]any{
					"archived_ws":        map[string]any{wsArch: true},
					"autonomous_enabled": autonomous,
					"users": map[string]any{
						"u-1": map[string]any{
							"actions": []any{"rbac.group.list"},
							"ws": map[string]any{
								ws1:    map[string]any{"actions": wsActions, "archived": false},
								wsArch: map[string]any{"actions": archActions, "archived": true},
							},
							"res": map[string]any{
								domain.URNHash(dashURN): map[string]any{"level": "editor", "archived": false},
							},
							"flags": map[string]any{"admin": false, "ws_admin": []any{}},
						},
						"admin-1": map[string]any{
							"actions": []any{},
							"ws":      map[string]any{},
							"res":     map[string]any{},
							"flags":   map[string]any{"admin": true, "ws_admin": []any{}},
						},
						"wsadmin-1": map[string]any{
							"actions": []any{},
							"ws": map[string]any{
								ws1: map[string]any{"actions": []any{}, "archived": false},
							},
							"res":   map[string]any{},
							"flags": map[string]any{"admin": false, "ws_admin": []any{ws1}},
						},
					},
				},
			},
		},
	}
}

type policyEval func(t *testing.T, input map[string]any) (bool, string)

func newEval(t *testing.T, autonomous bool) policyEval {
	t.Helper()
	src, err := os.ReadFile("windrose_authz.rego")
	require.NoError(t, err)
	prepared, err := rego.New(
		rego.Query("data.windrose.authz.result"),
		rego.Module("windrose_authz.rego", string(src)),
		rego.Store(inmem.NewFromObject(permData(autonomous))),
	).PrepareForEval(context.Background())
	require.NoError(t, err)
	return func(t *testing.T, input map[string]any) (bool, string) {
		t.Helper()
		rs, err := prepared.Eval(context.Background(), rego.EvalInput(input))
		require.NoError(t, err)
		require.Len(t, rs, 1)
		result, ok := rs[0].Expressions[0].Value.(map[string]any)
		require.True(t, ok, "result must be an object, got %T", rs[0].Expressions[0].Value)
		allowed, _ := result["allow"].(bool)
		reason, _ := result["reason"].(string)
		return allowed, reason
	}
}

func in(user, typ, action, ws, urn string, scopes []string, tenant string) map[string]any {
	subject := map[string]any{"id": user, "typ": typ}
	if len(scopes) > 0 {
		s := make([]any, len(scopes))
		for i, v := range scopes {
			s[i] = v
		}
		subject["scopes"] = s
	}
	if typ == "agent_obo" {
		subject["id"] = "agent-7"
		subject["obo_sub"] = user
	}
	input := map[string]any{"subject": subject, "action": action, "tenant": tenant}
	if ws != "" {
		input["workspace_id"] = ws
	}
	if urn != "" {
		input["resource_urn"] = urn
	}
	return input
}

// The same matrix as TestDecide_Matrix — policy parity (RBC-FR-044: the
// bundle is versioned and integration-tested in this repo).
func TestPolicy_MirrorsDecideMatrix(t *testing.T) {
	eval := newEval(t, false)

	cases := []struct {
		name   string
		input  map[string]any
		allow  bool
		reason string // "" = don't assert reason
	}{
		{"tenant-scoped action allowed", in("u-1", "user", "rbac.group.list", "", "", nil, tenantA), true, ""},
		{"tenant-scoped action not held", in("u-1", "user", "usage.report.read", "", "", nil, tenantA), false, "deny_default"},
		{"ws-scoped action in assigned ws", in("u-1", "user", "dataset.dataset.read", ws1, "", nil, tenantA), true, ""},
		{"ws-scoped action not held", in("u-1", "user", "chart.dashboard.delete", ws1, "", nil, tenantA), false, ""},
		{"ws-scoped in unassigned ws", in("u-1", "user", "dataset.dataset.read", ws2, "", nil, tenantA), false, ""},

		{"ws-scoped without ws context", in("u-1", "user", "dataset.dataset.read", "", "", nil, tenantA), false, "WORKSPACE_CONTEXT_REQUIRED"},
		{"tenant-scoped with ws context", in("u-1", "user", "rbac.group.list", ws1, "", nil, tenantA), false, "WORKSPACE_CONTEXT_FORBIDDEN"},

		{"unknown action", in("u-1", "user", "nope.nope.read", "", "", nil, tenantA), false, "unknown_action"},

		{"archived ws read allowed", in("u-1", "user", "dataset.dataset.read", wsArch, "", nil, tenantA), true, ""},
		{"archived ws write denied", in("u-1", "user", "chart.dashboard.update", wsArch, "", nil, tenantA), false, ""},

		{"admin bypass tenant-scoped", in("admin-1", "user", "usage.report.read", "", "", nil, tenantA), true, ""},
		{"admin bypass ws-scoped", in("admin-1", "user", "chart.dashboard.delete", ws1, "", nil, tenantA), true, ""},
		{"admin blocked archived write", in("admin-1", "user", "chart.dashboard.update", wsArch, "", nil, tenantA), false, ""},
		{"admin allowed archived read", in("admin-1", "user", "chart.dashboard.read", wsArch, "", nil, tenantA), true, ""},
		{"admin still context-validated", in("admin-1", "user", "dataset.dataset.read", "", "", nil, tenantA), false, "WORKSPACE_CONTEXT_REQUIRED"},

		{"ws-admin any action in own ws", in("wsadmin-1", "user", "chart.dashboard.delete", ws1, "", nil, tenantA), true, ""},
		{"ws-admin denied outside own ws", in("wsadmin-1", "user", "chart.dashboard.delete", ws2, "", nil, tenantA), false, ""},

		{"editor grant allows update", in("u-1", "user", "chart.dashboard.update", ws1, dashURN, nil, tenantA), true, ""},
		{"editor grant allows share", in("u-1", "user", "chart.dashboard.share", ws1, dashURN, nil, tenantA), true, ""},
		{"editor grant denies delete", in("u-1", "user", "chart.dashboard.delete", ws1, dashURN, nil, tenantA), false, ""},
		{"editor grant denies admin verb", in("u-1", "user", "chart.dashboard.admin", ws1, dashURN, nil, tenantA), false, ""},

		{"wrong tenant denies admin", in("admin-1", "user", "usage.report.read", "", "", nil, tenantB), false, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			allowed, reason := eval(t, tc.input)
			assert.Equal(t, tc.allow, allowed, "reason=%s", reason)
			if tc.reason != "" {
				assert.Equal(t, tc.reason, reason)
			}
		})
	}
}

// Parity: an unknown principal type fails closed in both Go and Rego. This is
// the exact probe that diverged before the fix (Go allowed typ="banana").
func TestPolicy_UnknownPrincipalTypeDenies(t *testing.T) {
	eval := newEval(t, false)
	for _, typ := range []string{"banana", "admin", "root"} {
		allowed, reason := eval(t, in("admin-1", typ, "usage.report.read", "", "", nil, tenantA))
		assert.Falsef(t, allowed, "typ=%q must deny", typ)
		assert.Equalf(t, "unknown_principal_type", reason, "typ=%q reason", typ)
	}
	// Same admin subject with a valid typ is allowed (admin flag).
	allowed, _ := eval(t, in("admin-1", "user", "usage.report.read", "", "", nil, tenantA))
	assert.True(t, allowed)
}

// OBO intersection (BR-6/AC-7) in Rego.
func TestPolicy_OBOIntersection(t *testing.T) {
	eval := newEval(t, false)

	allowed, _ := eval(t, in("u-1", "agent_obo", "case.case.assign", ws1, "", []string{"case.case.assign"}, tenantA))
	assert.True(t, allowed, "user allows + scope includes")

	allowed, reason := eval(t, in("u-1", "agent_obo", "case.case.assign", ws1, "", []string{"chart.dashboard.read"}, tenantA))
	assert.False(t, allowed)
	assert.Equal(t, "scope_excluded", reason)

	allowed, _ = eval(t, in("u-1", "agent_obo", "chart.dashboard.delete", ws1, "", []string{"chart.dashboard.delete"}, tenantA))
	assert.False(t, allowed, "scope includes but user cannot")

	allowed, _ = eval(t, in("u-1", "agent_obo", "usage.report.read", "", "", []string{"*"}, tenantA))
	assert.False(t, allowed, "wildcard scope never widens user permissions")
}

// Autonomous agents in Rego: scopes AND tenant enablement.
func TestPolicy_AutonomousAgent(t *testing.T) {
	evalOff := newEval(t, false)
	allowed, reason := evalOff(t, in("agent-9", "agent_autonomous", "rbac.group.list", "", "", []string{"rbac.group.list"}, tenantA))
	assert.False(t, allowed)
	assert.Equal(t, "autonomous_disabled", reason)

	evalOn := newEval(t, true)
	allowed, _ = evalOn(t, in("agent-9", "agent_autonomous", "rbac.group.list", "", "", []string{"rbac.group.list"}, tenantA))
	assert.True(t, allowed)

	allowed, reason = evalOn(t, in("agent-9", "agent_autonomous", "usage.report.read", "", "", []string{"rbac.group.list"}, tenantA))
	assert.False(t, allowed)
	assert.Equal(t, "scope_excluded", reason)
}
