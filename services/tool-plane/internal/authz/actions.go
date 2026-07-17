package authz

// Actions (MASTER-FR-016 naming: <service>.<resource>.<verb>). Every verb here
// MUST be in rbac's canonical closed verb set (RBC-FR-022:
// read,list,create,update,delete,execute,assign,approve,admin,export,share) —
// rbac rejects the ENTIRE registration batch if any action carries a
// non-canonical verb. Per platform convention (see chart-service/query-service)
// list handlers are guarded by the `.read` action, not a `.list` action.
const (
	// Catalog / lifecycle (TPL-FR-001/002/003). Version add/publish/deprecate
	// mutate the tool's callable surface → update; retire removes a version from
	// the callable set permanently → delete.
	ActionToolCreate = "tool.tool.create"
	ActionToolRead   = "tool.tool.read" // also guards list/schema/diff/health/discovery
	ActionToolUpdate = "tool.tool.update"
	ActionToolDelete = "tool.tool.delete" // guards retire

	// Data-plane invocation (TPL-FR-030..036): the mcp-gateway enforcement
	// pipeline authorizes every tools/call as this action (formerly the
	// non-canonical "tool.invoke", which rbac's catalog could never accept).
	ActionToolExecute = "tool.tool.execute"

	// Per-tenant enablement (TPL-FR-004).
	ActionEnablementUpdate = "tool.enablement.update"

	// Kill switches (TPL-FR-052). List/read maps to the .read verb per the
	// platform's list-guarded-by-read convention (see comment above).
	ActionKillCreate = "tool.kill.create"
	ActionKillDelete = "tool.kill.delete"
	ActionKillRead   = "tool.kill.read"

	// BYO onboarding (TPL-FR-040). Approve/reject are both operator decisions on
	// a submission → one `approve` action guards both.
	ActionBYOCreate  = "tool.byo.create"
	ActionBYOApprove = "tool.byo.approve"
)

// ScopeSuperAdmin is the platform-operator scope (mirrors rbac-service
// api.ScopeSuperAdmin): carried on service/user tokens minted for platform
// operators. It is the ONLY identity allowed to act across tenants (e.g. set a
// tenant-scoped kill switch for a tenant other than its own).
const ScopeSuperAdmin = "super_admin"

// ManifestEntry is one action tool-plane registers with rbac at startup
// (RBC-FR-022). WorkspaceScoped mirrors rbac's grant scoping.
type ManifestEntry struct {
	Action          string
	WorkspaceScoped bool
}

// Manifest returns all tool-plane actions — the exact set the admin route
// guards and the gateway enforcement pipeline authorize against (kept
// consistent by TestManifestVerbsAreCanonical and
// TestGuardedActionsAreRegistered).
//
// All actions are tenant-scoped (workspace_scoped=false): the registry admin
// plane operates on tenant/platform catalog rows, and the gateway pipeline's
// normative OPA input document (BRD 13 §3) carries tenant + affected URNs but
// no workspace id, so tool.tool.execute is registered tenant-scoped too.
func Manifest() []ManifestEntry {
	return []ManifestEntry{
		{ActionToolCreate, false}, {ActionToolRead, false}, {ActionToolUpdate, false},
		{ActionToolDelete, false}, {ActionToolExecute, false},
		{ActionEnablementUpdate, false},
		{ActionKillCreate, false}, {ActionKillDelete, false}, {ActionKillRead, false},
		{ActionBYOCreate, false}, {ActionBYOApprove, false},
	}
}
