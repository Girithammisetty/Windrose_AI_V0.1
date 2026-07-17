// Package authz is the authorization port (MASTER-FR-012): decisions come from
// the local OPA sidecar reading the Redis permissions_flat projection.
// rbac-service is never called synchronously in the request path.
package authz

import "context"

// Input is one authorization question (MASTER-FR-012/016).
type Input struct {
	Subject     Subject `json:"subject"`
	Action      string  `json:"action"`
	ResourceURN string  `json:"resource_urn"`
	WorkspaceID string  `json:"workspace_id"`
	Tenant      string  `json:"tenant"`
}

// Subject describes the caller.
type Subject struct {
	ID     string   `json:"id"`
	Typ    string   `json:"typ"`
	OboSub string   `json:"obo_sub,omitempty"`
	Scopes []string `json:"scopes,omitempty"`
}

// Authorizer answers allow/deny. The real runtime implementation is OPAClient
// (opa_client.go). AllowAll/Static are unit-test doubles only.
type Authorizer interface {
	Allow(ctx context.Context, in Input) bool
}

// AllowAll is the permissive fake (unit/integration tests only).
type AllowAll struct{}

func (AllowAll) Allow(context.Context, Input) bool { return true }

// Static allows/denies per action (unit-tier authz matrix fake).
type Static struct {
	Denied map[string]bool
}

func (s Static) Allow(_ context.Context, in Input) bool { return !s.Denied[in.Action] }

// Actions (MASTER-FR-016 naming: <service>.<resource>.<verb>). Every action
// here MUST use a catalog-valid verb from the RBC-FR-022 whitelist
// (read/list/create/update/delete/execute/assign/approve/admin/export/share)
// and a canonical case resource (case, disposition, bulk) so that rbac's
// action catalog (which OPA consumes for `action_known`) recognises it. These
// are registered with rbac's idempotent registration API at startup (Manifest).
const (
	ActionCaseCreate = "case.case.create"
	ActionCaseRead   = "case.case.read"
	ActionCaseUpdate = "case.case.update"
	ActionCaseWork   = "case.case.execute" // begin/advance work on a case
	ActionCaseAssign = "case.case.assign"
	ActionCaseManage = "case.case.update" // reopen/close/escalate transitions
	ActionCaseResolve = "case.case.update" // resolve transition
	ActionCaseBulk    = "case.bulk.execute"
	ActionCaseExport  = "case.case.export"
	ActionCaseComment = "case.case.update" // add/edit/delete a comment
	// ActionProposalApply gates applying an approved copilot proposal (a human-
	// approved disposition write); modelled as an approve on the disposition.
	ActionProposalApply     = "case.disposition.approve"
	ActionDispositionRead   = "case.disposition.read"
	ActionDispositionCreate = "case.disposition.create"
	ActionDispositionUpdate = "case.disposition.update"
	ActionFieldRead         = "case.case.read"   // case field configs read
	ActionFieldManage       = "case.case.update" // case field configs write
	ActionAdminReindex      = "case.case.admin"  // operator reindex
	ActionSLAManage         = "case.case.admin"  // SLA policy config
	// Case evidence attachments (task #77): list/download vs upload vs remove.
	ActionEvidenceRead   = "case.evidence.read"
	ActionEvidenceCreate = "case.evidence.create"
	ActionEvidenceDelete = "case.evidence.delete"
)

// Manifest is case-service's action catalog slice (RBC-FR-022): the exact set
// of actions this service authorizes against. It is registered with rbac at
// startup so the catalog OPA consumes knows each action (`action_known`). All
// case content-plane actions are workspace-scoped.
func Manifest() []ActionManifestEntry {
	seen := map[string]bool{}
	var out []ActionManifestEntry
	for _, a := range []string{
		ActionCaseCreate, ActionCaseRead, ActionCaseUpdate, ActionCaseWork,
		ActionCaseAssign, ActionCaseManage, ActionCaseResolve, ActionCaseBulk,
		ActionCaseExport, ActionCaseComment, ActionProposalApply,
		ActionDispositionRead, ActionDispositionCreate, ActionDispositionUpdate,
		ActionFieldRead, ActionFieldManage, ActionAdminReindex, ActionSLAManage,
		ActionEvidenceRead, ActionEvidenceCreate, ActionEvidenceDelete,
	} {
		if seen[a] {
			continue
		}
		seen[a] = true
		out = append(out, ActionManifestEntry{Action: a, WorkspaceScoped: true})
	}
	return out
}

// ActionManifestEntry is one catalog registration record.
type ActionManifestEntry struct {
	Action          string `json:"action"`
	WorkspaceScoped bool   `json:"workspace_scoped"`
}
