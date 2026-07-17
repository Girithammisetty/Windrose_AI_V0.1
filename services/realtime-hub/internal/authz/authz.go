// Package authz is realtime-hub's per-topic authorization port (RTH-FR-012).
// Every subscribe (initial or incremental) is authorized here. run-status and
// proposal topics are decided by the real OPA sidecar over the Redis
// permissions_flat projection (MASTER-FR-012); notifications and chat use the
// structural rules from RTH-FR-003 (owner-only / session-ownership) that OPA
// does not model. The real runtime implementation is OPAAuthorizer (opa.go);
// Static below is a unit-test double only.
package authz

import (
	"context"

	"github.com/windrose-ai/go-common/opaclient"
	"github.com/windrose-ai/realtime-hub/internal/topics"
)

// Actions (MASTER-FR-016 naming: <service>.<resource>.<verb>). Every action
// here MUST use a catalog-valid verb from the RBC-FR-022 whitelist
// (read/list/create/update/delete/execute/assign/approve/admin/export/share)
// so that rbac's action catalog (which OPA consumes for `action_known`)
// recognises it. These are registered with rbac's idempotent registration API
// at startup (Manifest, internal/register).
const (
	ActionRunStatusRead = "realtime.run_status.read"
	ActionProposalRead  = "realtime.proposal.read"
	ActionStreamExecute = "realtime.stream.execute"   // connect/attach to the push stream
	ActionAdmin         = "realtime.connection.admin" // ops: list/kill live connections
)

// Manifest is realtime-hub's action catalog slice (RBC-FR-022): the exact set
// of actions this service authorizes against. It is registered with rbac at
// startup so the catalog OPA consumes knows each action (`action_known`).
// All realtime actions are tenant/resource-scoped, NOT workspace-scoped: the
// OPA check passes the tenant plus a resource URN (run-status/proposal URNs
// already carry the tenant), never a workspace id.
func Manifest() []ActionManifestEntry {
	return []ActionManifestEntry{
		{Action: ActionRunStatusRead, WorkspaceScoped: false},
		{Action: ActionProposalRead, WorkspaceScoped: false},
		{Action: ActionStreamExecute, WorkspaceScoped: false},
		{Action: ActionAdmin, WorkspaceScoped: false},
	}
}

// ActionManifestEntry is one catalog registration record.
type ActionManifestEntry struct {
	Action          string `json:"action"`
	WorkspaceScoped bool   `json:"workspace_scoped"`
}

// Subject is the caller (mirrors the JWT claims relevant to authz).
type Subject struct {
	ID     string   // sub
	Typ    string   // user | service | agent_obo | agent_autonomous
	OboSub string   // original user for agent_obo
	Scopes []string // token scopes
}

// EffectiveUser resolves whose permission/identity applies (OBO → original).
func (s Subject) EffectiveUser() string {
	if s.Typ == "agent_obo" && s.OboSub != "" {
		return s.OboSub
	}
	return s.ID
}

// isService reports a service principal (may read any user's notifications /
// any session, RTH-FR-003).
func (s Subject) isService() bool { return s.Typ == "service" }

// Request is one authorization question: may Subject subscribe to Topic in Tenant?
type Request struct {
	Subject Subject
	Tenant  string
	Topic   topics.Topic
}

// Decision is the authz result. Deny is surfaced to the client as the per-topic
// control error TOPIC_FORBIDDEN (RTH-FR-012); Reason drives the audit event.
type Decision struct {
	Allow  bool
	Reason string
}

// Authorizer answers per-topic subscribe questions.
type Authorizer interface {
	Authorize(ctx context.Context, req Request) Decision
}

// toOPASubject maps to the shared opaclient subject shape.
func toOPASubject(s Subject) opaclient.Subject {
	return opaclient.Subject{ID: s.ID, Typ: s.Typ, OboSub: s.OboSub, Scopes: s.Scopes}
}
