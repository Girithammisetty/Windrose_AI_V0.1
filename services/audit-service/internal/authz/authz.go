// Package authz is audit-service's authorization port (MASTER-FR-012): every
// admin API decision comes from the local OPA sidecar evaluating the Redis
// permissions_flat projection. rbac-service is never called synchronously in
// the request path. The real runtime implementation is OPAClient (opa_client.go);
// AllowAll/Static are unit-test doubles only and are unreachable from cmd/server.
package authz

import "context"

// Input is one authorization question (MASTER-FR-012/016).
type Input struct {
	Subject     Subject
	Action      string
	ResourceURN string
	Tenant      string
}

// Subject describes the caller.
type Subject struct {
	ID     string
	Typ    string
	OboSub string
	Scopes []string
}

// Authorizer answers allow/deny.
type Authorizer interface {
	Allow(ctx context.Context, in Input) bool
}

// AllowAll is the permissive unit-test fake.
type AllowAll struct{}

func (AllowAll) Allow(context.Context, Input) bool { return true }

// Static denies the listed actions and allows the rest (authz-matrix unit fake).
type Static struct{ Denied map[string]bool }

func (s Static) Allow(_ context.Context, in Input) bool { return !s.Denied[in.Action] }

// Actions (MASTER-FR-016: <service>.<resource>.<verb>). All tenant-scoped
// (workspace_scoped=false): audit is a tenant-level admin plane.
// Verbs are drawn from rbac's canonical set (read/list/create/update/delete/
// execute/assign/approve/admin/export/share) — enforced by the action-catalog
// drift test. Chain verification and DLQ redrive are execute-class operations.
const (
	ActionEventRead      = "audit.event.read"      // search, agent-activity, single event (AUD-FR-030/031/033)
	ActionEventExport    = "audit.event.export"    // CSV/NDJSON export (AUD-FR-032)
	ActionExportRead     = "audit.export.read"     // list sealed WORM batches (AUD-FR-023)
	ActionChainVerify    = "audit.chain.execute"   // POST /audit/verify (AUD-FR-051)
	ActionComplianceRead = "audit.compliance.read" // compliance packs (AUD-FR-060/061)
	ActionDLQRedrive     = "audit.dlq.execute"     // POST /admin/dlq/redrive (AUD-FR-006) — platform operator
)

// ManifestEntry is one action-catalog row pushed to rbac (RBC-FR-022).
type ManifestEntry struct {
	Action          string `json:"action"`
	WorkspaceScoped bool   `json:"workspace_scoped"`
}

// Manifest is audit-service's action-catalog slice registered with rbac at
// startup so OPA's catalog knows each action (`action_known`).
func Manifest() []ManifestEntry {
	actions := []string{
		ActionEventRead, ActionEventExport, ActionExportRead,
		ActionChainVerify, ActionComplianceRead, ActionDLQRedrive,
	}
	out := make([]ManifestEntry, 0, len(actions))
	for _, a := range actions {
		out = append(out, ManifestEntry{Action: a, WorkspaceScoped: false})
	}
	return out
}
