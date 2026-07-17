// Package authz is the authorization port (MASTER-FR-012): decisions come
// from the local OPA sidecar reading the Redis permissions_flat projection.
// rbac-service is never called synchronously in the request path.
package authz

import "context"

// Input is one authorization question (MASTER-FR-012/016).
type Input struct {
	Subject     Subject `json:"subject"`
	Action      string  `json:"action"` // <service>.<resource>.<verb>
	ResourceURN string  `json:"resource_urn"`
	Tenant      string  `json:"tenant"`
	// WorkspaceID is required for workspace-scoped actions (e.g.
	// query.execution.execute): the OPA projection loads the caller's
	// ws-scoped grant slice under this key. Empty for tenant-scoped actions.
	WorkspaceID string `json:"workspace_id,omitempty"`
}

// Subject describes the caller.
type Subject struct {
	ID     string   `json:"id"`
	Typ    string   `json:"typ"`
	OboSub string   `json:"obo_sub,omitempty"`
	Scopes []string `json:"scopes,omitempty"`
}

// Authorizer answers allow/deny. The real runtime implementation is OPAClient
// (opa_client.go), backed by the shared libs/go-common opaclient: it reads the
// Redis permissions_flat projection and evaluates the OPA sidecar's
// windrose.authz_input bundle. AllowAll/Static below are unit-test doubles only.
type Authorizer interface {
	Allow(ctx context.Context, in Input) bool
}

// AllowAll is the permissive fake for tests/local dev; the authz matrix
// tests use Deny lists on top of it.
type AllowAll struct{}

func (AllowAll) Allow(context.Context, Input) bool { return true }

// Static allows/denies per action (unit-tier authz matrix fake).
type Static struct {
	// Denied actions; everything else allowed.
	Denied map[string]bool
}

func (s Static) Allow(_ context.Context, in Input) bool { return !s.Denied[in.Action] }

// Actions (MASTER-FR-016 naming: <service>.<resource>.<verb>). Every action
// MUST use a verb from rbac's closed canonical set (RBC-FR-022:
// read/list/create/update/delete/execute/assign/approve/admin/export/share) —
// rbac's registration API rejects the whole manifest batch otherwise, and OPA
// then denies every request with action_known=false.
const (
	ActionQueryRead   = "query.query.read"
	ActionQueryCreate = "query.query.create"
	ActionQueryUpdate = "query.query.update"
	ActionQueryDelete = "query.query.delete"
	// Canonical verb is "execute" (rbac's closed verb set has no "run"); this
	// guards /sql/run, /dry-run, saved-query run, /rows and execution cancel
	// (cancel is an execution-control operation on the same capability —
	// "cancel" is not a canonical verb). Renamed from the former non-canonical
	// "query.execution.run" so a role can actually be granted it (rbac's role
	// seed rejects any bound action whose verb isn't canonical).
	ActionExecRun    = "query.execution.execute"
	ActionExecRead   = "query.execution.read"
	ActionExecExport = "query.execution.export"
	ActionStatsRead  = "query.stats.read"
	ActionLimitsRead = "query.limits.read"
	// Canonical verb is "update" ("write" is not in the closed verb set).
	ActionLimitsUpdate = "query.limits.update"
)

// WorkspaceScoped reports whether an action is workspace-scoped per the
// manifest. The OPA context rule (ctx_ok) requires workspace-scoped actions
// to carry a workspace id and tenant-scoped actions to carry NONE — so route
// guards must strip the token's workspace for tenant-scoped actions.
func WorkspaceScoped(action string) bool {
	for _, e := range Manifest() {
		if e.Action == action {
			return e.WorkspaceScoped
		}
	}
	return true // unknown actions default to the stricter ws-scoped contract
}

// ManifestEntry is one action query-service registers with rbac at startup
// (RBC-FR-022) so OPA's catalog knows it (`action_known`).
type ManifestEntry struct {
	Action          string `json:"action"`
	WorkspaceScoped bool   `json:"workspace_scoped"`
}

// Manifest returns ALL actions query-service's route guards authorize
// against — the exact guarded set (kept consistent by the drift tests in
// authz_test.go). Saved queries and executions are workspace-scoped content
// plane resources (matching rbac's canonical catalog); stats and limits are
// tenant-scoped operator views.
func Manifest() []ManifestEntry {
	return []ManifestEntry{
		{Action: ActionQueryRead, WorkspaceScoped: true},
		{Action: ActionQueryCreate, WorkspaceScoped: true},
		{Action: ActionQueryUpdate, WorkspaceScoped: true},
		{Action: ActionQueryDelete, WorkspaceScoped: true},
		{Action: ActionExecRun, WorkspaceScoped: true},
		{Action: ActionExecRead, WorkspaceScoped: true},
		{Action: ActionExecExport, WorkspaceScoped: true},
		{Action: ActionStatsRead, WorkspaceScoped: false},
		{Action: ActionLimitsRead, WorkspaceScoped: false},
		{Action: ActionLimitsUpdate, WorkspaceScoped: false},
	}
}
