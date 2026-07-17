package integration

import (
	"context"
	"os"
	"testing"

	"github.com/google/uuid"

	gcopa "github.com/windrose-ai/go-common/opaclient"

	"github.com/windrose-ai/rbac-service/internal/authz"
	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/projection"
)

// TestOPAContainerParityWithGoDecide is the real cross-engine parity check
// (RBC-FR-044, MASTER-FR-012): for a matrix of cases, the SAME projection slice
// is fed to (a) rbac's Go reference `authz.Decide` and (b) the shared
// libs/go-common opaclient calling the REAL OPA container (localhost:8281,
// windrose.authz_input bundle). The two engines must return the same allow/deny
// — and the same reason on deny. This proves a service authorizing via opaclient
// gets exactly the decision rbac's SQL-fallback `Decide` would give.
func TestOPAContainerParityWithGoDecide(t *testing.T) {
	if skipReason != "" {
		t.Skipf("%s", skipReason)
	}
	opaURL := os.Getenv("OPA_URL")
	if opaURL == "" {
		opaURL = "http://localhost:8281"
	}
	ctx := context.Background()
	client := gcopa.New(opaURL)

	// Reachability probe (and confirms the input bundle is loaded).
	if _, err := client.Check(ctx, gcopa.Input{
		Subject: gcopa.Subject{ID: "u", Typ: "user"}, Action: "rbac.group.list", Tenant: "t",
		Projection: gcopa.Projection{ActionKnown: true},
	}); err != nil {
		t.Skipf("OPA unavailable at %s: %v (restart windrose-dev-opa-1 after editing policy)", opaURL, err)
	}

	tenant := uuid.New()
	ws := uuid.New()
	user := "user-1"

	// Action catalog: tenant-scoped vs workspace-scoped.
	catalog := map[string]bool{
		"rbac.group.list":        false,
		"rbac.role.list":         false,
		"dataset.dataset.read":   true,
		"dataset.dataset.delete": true,
	}

	type tc struct {
		name    string
		flat    projection.Flat
		auto    bool
		in      authz.Input
		archive []uuid.UUID
	}
	base := func() projection.Flat {
		return projection.Flat{TenantID: tenant, UserID: user, Version: 1, WorkspaceActions: map[uuid.UUID]projection.WorkspaceEntry{}, Resources: map[string]projection.ResourceEntry{}}
	}
	sub := func(id, typ string, obo string, scopes ...string) authz.Subject {
		return authz.Subject{ID: id, Typ: typ, OboSub: obo, Scopes: scopes}
	}

	cases := []tc{
		{
			name: "tenant_role_allow",
			flat: func() projection.Flat { f := base(); f.TenantActions = []string{"rbac.group.list"}; return f }(),
			in:   authz.Input{Subject: sub(user, domain.TypUser, ""), Action: "rbac.group.list", Tenant: tenant.String()},
		},
		{
			name: "tenant_deny_default",
			flat: func() projection.Flat { f := base(); f.TenantActions = []string{"rbac.role.list"}; return f }(),
			in:   authz.Input{Subject: sub(user, domain.TypUser, ""), Action: "rbac.group.list", Tenant: tenant.String()},
		},
		{
			name: "admin_bypass",
			flat: func() projection.Flat { f := base(); f.Flags = projection.Flags{Admin: true}; return f }(),
			in:   authz.Input{Subject: sub(user, domain.TypUser, ""), Action: "rbac.group.list", Tenant: tenant.String()},
		},
		{
			name: "workspace_assigned_allow",
			flat: func() projection.Flat {
				f := base()
				f.WorkspaceActions[ws] = projection.WorkspaceEntry{Actions: []string{"dataset.dataset.read"}}
				return f
			}(),
			in: authz.Input{Subject: sub(user, domain.TypUser, ""), Action: "dataset.dataset.read", WorkspaceID: ws.String(), Tenant: tenant.String()},
		},
		{
			name: "workspace_not_assigned",
			flat: func() projection.Flat { f := base(); f.TenantActions = []string{"rbac.group.list"}; return f }(),
			in:   authz.Input{Subject: sub(user, domain.TypUser, ""), Action: "dataset.dataset.read", WorkspaceID: ws.String(), Tenant: tenant.String()},
		},
		{
			name: "projection_miss_absent_user",
			flat: base(),
			in:   authz.Input{Subject: sub("absent-user", domain.TypUser, ""), Action: "rbac.group.list", Tenant: tenant.String()},
		},
		{
			name: "autonomous_allow",
			flat: base(),
			auto: true,
			in:   authz.Input{Subject: sub("agent-1", domain.TypAgentAutonomous, "", "dataset.dataset.read"), Action: "dataset.dataset.read", WorkspaceID: ws.String(), Tenant: tenant.String()},
		},
		{
			name: "obo_scope_excluded",
			flat: func() projection.Flat { f := base(); f.TenantActions = []string{"rbac.group.list"}; return f }(),
			in:   authz.Input{Subject: sub("agent-1", domain.TypAgentOBO, user, "some.other.action"), Action: "rbac.group.list", Tenant: tenant.String()},
		},
		{
			name: "assigned_archived_write_block",
			flat: func() projection.Flat {
				f := base()
				f.WorkspaceActions[ws] = projection.WorkspaceEntry{Actions: []string{"dataset.dataset.delete"}, Archived: true}
				return f
			}(),
			in: authz.Input{Subject: sub(user, domain.TypUser, ""), Action: "dataset.dataset.delete", WorkspaceID: ws.String(), Tenant: tenant.String()},
		},
		{
			name: "workspace_context_required",
			flat: func() projection.Flat { f := base(); f.Flags = projection.Flags{Admin: true}; return f }(),
			in:   authz.Input{Subject: sub(user, domain.TypUser, ""), Action: "dataset.dataset.read", Tenant: tenant.String()},
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			reader := projection.NewFlatReader(c.flat, catalog, c.archive)
			reader.Autonomous = c.auto

			// (a) Go reference decision.
			goDec, err := authz.Decide(ctx, c.in, reader)
			if err != nil {
				t.Fatalf("go decide: %v", err)
			}

			// (b) OPA-container decision over the SAME projection facts.
			opaIn := toOPAInput(ctx, t, reader, c.in)
			opaDec, err := client.Check(ctx, opaIn)
			if err != nil {
				t.Fatalf("opa check: %v", err)
			}

			if goDec.Allowed != opaDec.Allow {
				t.Fatalf("PARITY BREAK allow: go=%v (%s) opa=%v (%s)", goDec.Allowed, goDec.Reason, opaDec.Allow, opaDec.Reason)
			}
			if !goDec.Allowed && goDec.Reason != opaDec.Reason {
				t.Fatalf("PARITY BREAK deny-reason: go=%q opa=%q", goDec.Reason, opaDec.Reason)
			}
		})
	}
}

// toOPAInput reads the exact same projection facts Decide consulted from the
// shared Reader and packs them into the opaclient input, so both engines see an
// identical projection slice.
func toOPAInput(ctx context.Context, t *testing.T, r projection.Reader, in authz.Input) gcopa.Input {
	t.Helper()
	user := in.EffectiveUser()
	scoped, known, err := r.ActionScoped(ctx, in.Action)
	mustOK(t, err)
	flags, flagsFound, err := r.UserFlags(ctx, in.Tenant, user)
	mustOK(t, err)
	tActions, taFound, err := r.TenantActions(ctx, in.Tenant, user)
	mustOK(t, err)
	auto, err := r.AutonomousEnabled(ctx, in.Tenant)
	mustOK(t, err)

	p := gcopa.Projection{
		ActionKnown:       known,
		ActionScoped:      scoped,
		AutonomousEnabled: auto,
	}
	wsAdmin := make([]string, 0, len(flags.WsAdmin))
	for _, id := range flags.WsAdmin {
		wsAdmin = append(wsAdmin, id.String())
	}
	p.Flags = gcopa.Flags{Found: flagsFound, Admin: flags.Admin, WsAdmin: wsAdmin}
	p.TenantActions = gcopa.TenantActions{Found: taFound, Actions: tActions}

	if in.WorkspaceID != "" {
		entry, assigned, err := r.Workspace(ctx, in.Tenant, user, in.WorkspaceID)
		mustOK(t, err)
		p.Workspace = gcopa.WorkspaceFacts{Assigned: assigned, Actions: entry.Actions, Archived: entry.Archived}
		arch, err := r.ArchivedWorkspaces(ctx, in.Tenant)
		mustOK(t, err)
		p.WorkspaceArchivedTenant = arch[in.WorkspaceID]
	}
	if in.ResourceURN != "" {
		entry, found, err := r.Resource(ctx, in.Tenant, user, domain.URNHash(in.ResourceURN))
		mustOK(t, err)
		p.Resource = gcopa.ResourceFacts{Found: found, Level: string(entry.Level), Archived: entry.Archived}
	}

	return gcopa.Input{
		Subject:     gcopa.Subject{ID: in.Subject.ID, Typ: in.Subject.Typ, OboSub: in.Subject.OboSub, Scopes: in.Subject.Scopes},
		Action:      in.Action,
		ResourceURN: in.ResourceURN,
		WorkspaceID: in.WorkspaceID,
		Tenant:      in.Tenant,
		Projection:  p,
	}
}

func mustOK(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
