package domain

import (
	"fmt"
	"regexp"
	"strings"
)

// Verbs of the platform action grammar `<service>.<resource>.<verb>`
// (MASTER-FR-016, RBC-FR-022).
const (
	VerbRead    = "read"
	VerbList    = "list"
	VerbCreate  = "create"
	VerbUpdate  = "update"
	VerbDelete  = "delete"
	VerbExecute = "execute"
	VerbAssign  = "assign"
	VerbApprove = "approve"
	VerbAdmin   = "admin"
	VerbExport  = "export"
	VerbShare   = "share"
	// VerbMerge: confirm an entity-resolution merge of two records into one
	// resolved entity (BRD 56 inc2, ER-FR-030). A distinct verb so it can be
	// granted independently of read/execute (a steward confirms; a viewer reads).
	VerbMerge = "merge"
)

// AllVerbs is the closed verb set of RBC-FR-022.
var AllVerbs = map[string]bool{
	VerbRead: true, VerbList: true, VerbCreate: true, VerbUpdate: true,
	VerbDelete: true, VerbExecute: true, VerbAssign: true, VerbApprove: true,
	VerbAdmin: true, VerbExport: true, VerbShare: true, VerbMerge: true,
}

// ActionDef is a catalog entry. Actions are static, code-defined strings
// registered by each service at deploy. WorkspaceScoped preserves V1's
// `workspace_dependent` semantics: workspace-scoped actions require a
// workspace context and assignment; tenant-scoped actions must not carry one.
type ActionDef struct {
	Action          string `json:"action"`
	Service         string `json:"service"`
	Resource        string `json:"resource"`
	Verb            string `json:"verb"`
	WorkspaceScoped bool   `json:"workspace_scoped"`
	Description     string `json:"description,omitempty"`
	Deprecated      bool   `json:"deprecated,omitempty"`
}

var actionNameRe = regexp.MustCompile(`^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`)

// ParseAction splits and validates `<service>.<resource>.<verb>`.
func ParseAction(action string) (service, resource, verb string, err error) {
	if !actionNameRe.MatchString(action) {
		return "", "", "", fmt.Errorf("action %q does not match <service>.<resource>.<verb>", action)
	}
	parts := strings.SplitN(action, ".", 3)
	if !AllVerbs[parts[2]] {
		return "", "", "", fmt.Errorf("action %q uses unknown verb %q", action, parts[2])
	}
	return parts[0], parts[1], parts[2], nil
}

// ActionVerb returns the verb component; empty when malformed.
func ActionVerb(action string) string {
	i := strings.LastIndex(action, ".")
	if i < 0 {
		return ""
	}
	return action[i+1:]
}

// resourceSpec drives catalog generation: verbs per resource with scope.
type resourceSpec struct {
	service   string
	resource  string
	verbs     []string
	wsScoped  bool
	descrNote string
}

// canonicalSpecs is the reviewed canonical action list from RBC-FR-022.
// Tenant-scoped: identity.*, rbac.*, usage.*, audit.*, ai.budget.
// Workspace-scoped: all content-plane resources.
var canonicalSpecs = []resourceSpec{
	// identity
	{"identity", "tenant", []string{VerbRead, VerbUpdate, VerbAdmin}, false, "tenant settings"},
	{"identity", "user", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbAdmin}, false, "user management"},
	{"identity", "service_account", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbAdmin}, false, "service accounts"},
	{"identity", "credential", []string{VerbRead}, false, "tenant credential material"},
	// rbac
	{"rbac", "workspace", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbAdmin}, false, "workspace lifecycle"},
	{"rbac", "group", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbAssign}, false, "groups and membership"},
	{"rbac", "role", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete}, false, "custom roles"},
	{"rbac", "grant", []string{VerbRead, VerbList, VerbCreate, VerbDelete, VerbShare}, false, "content grants"},
	// ingestion
	{"ingestion", "connection", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbExecute}, true, "source connections"},
	{"ingestion", "ingestion", []string{VerbRead, VerbList, VerbCreate, VerbExecute}, true, "ingestion runs"},
	{"ingestion", "upload", []string{VerbRead, VerbCreate, VerbUpdate, VerbExecute, VerbDelete}, true, "resumable file uploads"},
	// dataset
	{"dataset", "dataset", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbExport, VerbShare}, true, "datasets"},
	{"dataset", "profile", []string{VerbRead, VerbExecute}, true, "profiling"},
	{"dataset", "lineage", []string{VerbRead}, true, "lineage graph"},
	{"dataset", "entity", []string{VerbExecute, VerbRead, VerbMerge}, true, "entity resolution"},
	{"dataset", "ontology", []string{VerbRead, VerbList, VerbCreate, VerbDelete}, true, "domain ontology (entity types, inc11)"},
	// query
	{"query", "query", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbExecute, VerbShare}, true, "saved queries"},
	{"query", "execution", []string{VerbRead, VerbList, VerbExecute, VerbExport}, true, "query executions"},
	{"query", "stats", []string{VerbRead}, false, "tenant query stats (operator view)"},
	{"query", "limits", []string{VerbRead, VerbUpdate}, false, "tenant execution ceilings"},
	// realtime (tenant/resource-scoped: the hub's OPA input carries tenant + URN, never a workspace)
	{"realtime", "run_status", []string{VerbRead}, false, "run-status topic subscribe"},
	{"realtime", "proposal", []string{VerbRead}, false, "proposal topic subscribe"},
	{"realtime", "stream", []string{VerbExecute}, false, "push-stream connect"},
	{"realtime", "connection", []string{VerbAdmin}, false, "ops: list/kill live connections"},
	// tool-plane (registry admin plane + gateway invocation)
	{"tool", "tool", []string{VerbRead, VerbCreate, VerbUpdate, VerbDelete, VerbExecute}, false, "tool catalog + invocation"},
	{"tool", "enablement", []string{VerbUpdate}, false, "per-tenant tool enablement"},
	{"tool", "kill", []string{VerbCreate, VerbDelete}, false, "tool kill switches"},
	{"tool", "byo", []string{VerbCreate, VerbApprove}, false, "BYO tool onboarding"},
	// semantic
	{"semantic", "model", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbApprove}, true, "semantic models"},
	{"semantic", "compile", []string{VerbExecute}, true, "metric compilation"},
	{"semantic", "measure", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete}, true, "measures"},
	{"semantic", "verified_query", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbApprove}, true, "verified NL<->SQL pairs"},
	// chart
	{"chart", "dashboard", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbShare, VerbExport}, true, "dashboards"},
	{"chart", "chart", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete}, true, "charts"},
	// case
	{"case", "case", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbAssign, VerbExecute}, true, "cases"},
	{"case", "disposition", []string{VerbRead, VerbCreate, VerbUpdate, VerbApprove}, true, "dispositions"},
	{"case", "bulk", []string{VerbExecute, VerbApprove}, true, "bulk case ops"},
	{"case", "evidence", []string{VerbRead, VerbCreate, VerbDelete}, true, "case evidence files"},
	{"case", "schema", []string{VerbRead, VerbList, VerbCreate, VerbDelete}, true, "typed case schemas (case types, inc10)"},
	// pipeline
	{"pipeline", "template", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete}, true, "pipeline templates"},
	{"pipeline", "run", []string{VerbRead, VerbList, VerbCreate, VerbExecute}, true, "pipeline runs"},
	{"pipeline", "schedule", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbExecute}, true, "pipeline schedules"},
	{"pipeline", "quota", []string{VerbAdmin}, false, "tenant pipeline quotas"},
	{"pipeline", "component", []string{VerbRead, VerbList}, true, "pipeline component catalog"},
	{"pipeline", "algorithm", []string{VerbRead, VerbList}, true, "pipeline algorithm templates"},
	// experiment
	{"experiment", "experiment", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete}, true, "experiments"},
	{"experiment", "run", []string{VerbRead, VerbList, VerbCreate, VerbExecute}, true, "experiment runs"},
	{"experiment", "model", []string{VerbRead, VerbList, VerbUpdate, VerbApprove}, true, "trained models"},
	{"experiment", "archetype", []string{VerbRead, VerbList, VerbCreate, VerbDelete}, true, "model archetypes (governed blueprints, inc9)"},
	{"experiment", "model_card", []string{VerbRead, VerbUpdate}, true, "model card edits"},
	{"experiment", "promotion", []string{VerbRead, VerbApprove}, true, "model promotion decisions (four-eyes)"},
	// inference
	{"inference", "job", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete, VerbExecute}, true, "inference jobs"},
	{"inference", "schedule", []string{VerbRead, VerbList, VerbCreate, VerbUpdate, VerbDelete}, true, "inference schedules"},
	// eval (eval-service: suites/runs/cases/canaries/scorers/gates/trends/SLOs).
	// eval.dataset + eval.case are RECONCILED to the closed verb set (inc8): the
	// former non-closed write/curate became create+update, so eval-service can
	// register them and pack-service can materialize golden eval sets (a dataset
	// + its input->expected cases). eval.suite/canary/slo still use non-closed
	// verbs (write/manage/operator) and remain a follow-up (out of inc8 scope).
	{"eval", "dataset", []string{VerbRead, VerbCreate, VerbUpdate}, true, "eval datasets (golden sets)"},
	{"eval", "case", []string{VerbRead, VerbCreate, VerbUpdate}, true, "eval golden cases"},
	{"eval", "run", []string{VerbRead, VerbExecute}, true, "eval scoring runs"},
	{"eval", "gate", []string{VerbRead}, true, "eval quality gates"},
	{"eval", "scorer", []string{VerbAdmin}, true, "eval scorer registry"},
	{"eval", "slo", []string{VerbRead}, true, "eval SLO targets"},
	{"eval", "trends", []string{VerbRead}, true, "eval trend/quality history"},
	// memory (memory-service: agent memories, RAG corpora, tenant policy,
	// GDPR erasure, stats — content-plane, workspace-scoped)
	{"memory", "memory", []string{VerbRead, VerbCreate, VerbUpdate, VerbDelete}, true, "agent/user memories"},
	{"memory", "corpus", []string{VerbAdmin}, true, "RAG corpora"},
	{"memory", "policy", []string{VerbRead, VerbUpdate}, true, "tenant memory policy"},
	{"memory", "erasure", []string{VerbRead, VerbCreate}, true, "erasure requests"},
	{"memory", "stats", []string{VerbRead}, true, "memory stats"},
	// ai
	{"ai", "agent_session", []string{VerbRead, VerbList, VerbCreate, VerbExecute}, true, "agent sessions"},
	{"ai", "proposal", []string{VerbRead, VerbList, VerbApprove}, true, "agent proposals"},
	{"ai", "memory", []string{VerbRead, VerbUpdate, VerbDelete}, true, "agent memory"},
	{"ai", "budget", []string{VerbRead, VerbUpdate}, false, "AI budgets"},
	// Tenant agent administration (agent-runtime): browse the agent catalog +
	// read/write per-tenant agent config (enable/pin/prompt_params/auto-execute)
	// + kill switches. Tenant-scoped (not per-workspace). Lets a tenant-defined
	// CUSTOM role unlock the agent-admin surfaces instead of the built-in Admin
	// being the only key (ART-FR-060..073). read = browse/read config; admin =
	// configure + kill switches.
	{"ai", "agent", []string{VerbRead, VerbAdmin}, false, "tenant agent catalog + config + kill switches"},
	// ai-gateway admin (LLM-infra control plane). READ-ONLY subset for now —
	// ai-gateway's own require()/require_operator() calls use "write" and
	// "invalidate", verbs outside this catalog's closed set; ai.cache has no
	// valid verb at all yet and is intentionally absent. Reconciling
	// ai-gateway's action names to the closed set is a follow-up decision,
	// not made here. ai.budget above already covers ai-gateway's own
	// budget/spend read (its route checks ai.budget.read same as agent-runtime).
	{"ai", "provider", []string{VerbRead}, false, "ai-gateway LLM provider catalog"},
	{"ai", "ladder", []string{VerbRead}, false, "ai-gateway model routing ladders"},
	{"ai", "spend", []string{VerbRead}, false, "ai-gateway live LLM spend"},
	{"ai", "key", []string{VerbRead}, false, "ai-gateway virtual keys"},
	{"ai", "guardrail", []string{VerbRead}, false, "ai-gateway guardrail policy"},
	{"ai", "platform", []string{VerbAdmin}, false, "ai-gateway platform-operator scope"},
	// usage
	{"usage", "report", []string{VerbRead, VerbList, VerbExport}, false, "usage reports"},
	{"usage", "budget", []string{VerbRead, VerbUpdate}, false, "usage budgets"},
	// audit
	{"audit", "log", []string{VerbRead, VerbList, VerbExport}, false, "audit logs"},
	// notification (tenant-scoped: inbox + preferences)
	{"notification", "inbox", []string{VerbRead}, false, "in-app inbox reads + marks"},
	{"notification", "preference", []string{VerbRead, VerbUpdate}, false, "notification preferences"},
	// scheduled dashboard report subscriptions (NOTIF-FR-060, "Team Reports")
	{"notification", "report", []string{VerbCreate, VerbRead, VerbUpdate, VerbDelete}, false, "scheduled dashboard report subscriptions"},
	// pack (pack-service, BRD 23: capability-pack catalog + governed install lifecycle)
	{"pack", "pack", []string{VerbRead}, true, "capability pack catalog"},
	{"pack", "install", []string{VerbRead, VerbExecute}, true, "pack installs into a workspace"},
}

// CanonicalCatalog returns the canonical platform action list (RBC-FR-022),
// deterministically ordered. rbac-service registers this at startup; other
// services re-register their slices idempotently at deploy.
func CanonicalCatalog() []ActionDef {
	var out []ActionDef
	for _, s := range canonicalSpecs {
		for _, v := range s.verbs {
			out = append(out, ActionDef{
				Action:          s.service + "." + s.resource + "." + v,
				Service:         s.service,
				Resource:        s.resource,
				Verb:            v,
				WorkspaceScoped: s.wsScoped,
				Description:     s.descrNote,
			})
		}
	}
	return out
}

// CatalogMap returns action -> workspace_scoped for the canonical catalog.
func CatalogMap() map[string]bool {
	m := map[string]bool{}
	for _, a := range CanonicalCatalog() {
		m[a.Action] = a.WorkspaceScoped
	}
	return m
}
