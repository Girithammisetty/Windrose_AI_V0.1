//go:build integration

// Integration tests for the OPA client against the real OPA server
// (deploy/docker-compose.dev.yml, localhost:8281) evaluating the
// windrose.authz_input Rego bundle. NOTE: OPA loads the /policy dir at startup
// without --watch, so restart the container after editing the bundle:
//
//	docker restart windrose-dev-opa-1
package opaclient

import (
	"context"
	"os"
	"testing"
)

func opaURL() string {
	if u := os.Getenv("OPA_URL"); u != "" {
		return u
	}
	return "http://localhost:8281"
}

func TestOPAInputMatrix(t *testing.T) {
	ctx := context.Background()
	c := New(opaURL())

	// Sanity: server reachable.
	if _, err := c.Check(ctx, Input{
		Subject: Subject{ID: "u", Typ: "user"}, Action: "rbac.group.list", Tenant: "t",
		Projection: Projection{ActionKnown: true},
	}); err != nil {
		t.Skipf("OPA unavailable at %s: %v (did you restart windrose-dev-opa-1?)", opaURL(), err)
	}

	cases := []struct {
		name       string
		in         Input
		wantAllow  bool
		wantReason string // checked only on deny
	}{
		{
			name: "tenant_scoped_role_action_allow",
			in: Input{
				Subject: Subject{ID: "u", Typ: "user"}, Action: "rbac.group.list", Tenant: "t",
				Projection: Projection{
					ActionKnown: true, ActionScoped: false,
					Flags:         Flags{Found: true},
					TenantActions: TenantActions{Found: true, Actions: []string{"rbac.group.list"}},
				},
			},
			wantAllow: true,
		},
		{
			name: "tenant_scoped_deny_default",
			in: Input{
				Subject: Subject{ID: "u", Typ: "user"}, Action: "rbac.group.list", Tenant: "t",
				Projection: Projection{
					ActionKnown: true, Flags: Flags{Found: true},
					TenantActions: TenantActions{Found: true, Actions: []string{"rbac.role.list"}},
				},
			},
			wantAllow: false, wantReason: "deny_default",
		},
		{
			name: "workspace_scoped_role_action_allow",
			in: Input{
				Subject: Subject{ID: "u", Typ: "user"}, Action: "dataset.dataset.read",
				WorkspaceID: "ws-1", Tenant: "t",
				Projection: Projection{
					ActionKnown: true, ActionScoped: true, Flags: Flags{Found: true},
					Workspace: WorkspaceFacts{Assigned: true, Actions: []string{"dataset.dataset.read"}},
				},
			},
			wantAllow: true,
		},
		{
			name: "admin_bypass_tenant_scoped",
			in: Input{
				Subject: Subject{ID: "u", Typ: "user"}, Action: "rbac.group.list", Tenant: "t",
				Projection: Projection{ActionKnown: true, Flags: Flags{Found: true, Admin: true}},
			},
			wantAllow: true,
		},
		{
			name: "admin_archived_write_blocked",
			in: Input{
				Subject: Subject{ID: "u", Typ: "user"}, Action: "dataset.dataset.delete",
				WorkspaceID: "ws-9", Tenant: "t",
				Projection: Projection{
					ActionKnown: true, ActionScoped: true,
					Flags:                   Flags{Found: true, Admin: true},
					Workspace:               WorkspaceFacts{Assigned: true},
					WorkspaceArchivedTenant: true,
				},
			},
			wantAllow: false, wantReason: "workspace_archived",
		},
		{
			name: "autonomous_scope_allow",
			in: Input{
				Subject: Subject{ID: "a", Typ: "agent_autonomous", Scopes: []string{"dataset.dataset.read"}},
				Action:  "dataset.dataset.read", Tenant: "t",
				Projection: Projection{ActionKnown: true, ActionScoped: false, AutonomousEnabled: true},
			},
			wantAllow: true,
		},
		{
			name: "autonomous_disabled_deny",
			in: Input{
				Subject: Subject{ID: "a", Typ: "agent_autonomous", Scopes: []string{"dataset.dataset.read"}},
				Action:  "dataset.dataset.read", Tenant: "t",
				Projection: Projection{ActionKnown: true, AutonomousEnabled: false},
			},
			wantAllow: false, wantReason: "autonomous_disabled",
		},
		{
			name: "obo_scope_excluded",
			in: Input{
				Subject: Subject{ID: "a", Typ: "agent_obo", OboSub: "u", Scopes: []string{"other.action"}},
				Action:  "rbac.group.list", Tenant: "t",
				Projection: Projection{ActionKnown: true, Flags: Flags{Found: true}, TenantActions: TenantActions{Found: true, Actions: []string{"rbac.group.list"}}},
			},
			wantAllow: false, wantReason: "scope_excluded",
		},
		{
			name: "unknown_action",
			in: Input{
				Subject:    Subject{ID: "u", Typ: "user"}, Action: "bogus.action.nope", Tenant: "t",
				Projection: Projection{ActionKnown: false},
			},
			wantAllow: false, wantReason: "unknown_action",
		},
		{
			name: "projection_miss",
			in: Input{
				Subject: Subject{ID: "u", Typ: "user"}, Action: "rbac.group.list", Tenant: "t",
				Projection: Projection{ActionKnown: true, Flags: Flags{Found: false}, TenantActions: TenantActions{Found: false}},
			},
			wantAllow: false, wantReason: "projection_miss",
		},
		{
			name: "workspace_context_required",
			in: Input{
				Subject: Subject{ID: "u", Typ: "user"}, Action: "dataset.dataset.read", Tenant: "t",
				Projection: Projection{ActionKnown: true, ActionScoped: true, Flags: Flags{Found: true}},
			},
			wantAllow: false, wantReason: "WORKSPACE_CONTEXT_REQUIRED",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d, err := c.Check(ctx, tc.in)
			if err != nil {
				t.Fatalf("check: %v", err)
			}
			if d.Allow != tc.wantAllow {
				t.Fatalf("allow=%v want %v (reason=%q)", d.Allow, tc.wantAllow, d.Reason)
			}
			if !tc.wantAllow && tc.wantReason != "" && d.Reason != tc.wantReason {
				t.Fatalf("reason=%q want %q", d.Reason, tc.wantReason)
			}
		})
	}
}
