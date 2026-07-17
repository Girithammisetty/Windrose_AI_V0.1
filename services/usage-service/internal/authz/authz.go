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
// (opa_client.go); AllowAll/Static below are unit-test doubles only.
type Authorizer interface {
	Allow(ctx context.Context, in Input) bool
}

// AllowAll is the permissive fake for unit tests/local harnesses.
type AllowAll struct{}

func (AllowAll) Allow(context.Context, Input) bool { return true }

// Static allows/denies per action (unit-tier authz matrix fake).
type Static struct {
	Denied map[string]bool
}

func (s Static) Allow(_ context.Context, in Input) bool { return !s.Denied[in.Action] }

// Actions (MASTER-FR-016 grammar <service>.<resource>.<verb>). The BRD's
// read/write shorthand maps to concrete create/update/delete verbs so the
// names are valid in the rbac action grammar.
const (
	// ActionReportRead guards both the JSON and CSV (Accept: text/csv) showback
	// responses on GET /reports/usage; CSV is not a separate action.
	ActionReportRead = "usage.report.read"

	ActionBudgetRead   = "usage.budget.read"
	ActionBudgetCreate = "usage.budget.create"
	ActionBudgetUpdate = "usage.budget.update"
	ActionBudgetDelete = "usage.budget.delete"

	ActionMeterRead = "usage.meter.read"

	ActionRateCardRead   = "usage.ratecard.read"
	ActionRateCardCreate = "usage.ratecard.create"
	ActionRateCardUpdate = "usage.ratecard.update"

	ActionAnomalyRead   = "usage.anomaly.read"
	ActionAnomalyUpdate = "usage.anomaly.update"

	ActionReconRead   = "usage.reconciliation.read"
	ActionReconUpdate = "usage.reconciliation.update"
)

// ManifestEntry is one action for rbac catalog registration (RBC-FR-022).
type ManifestEntry struct {
	Action          string
	WorkspaceScoped bool
	PlatformOnly    bool
}

// Manifest is usage-service's action catalog registered with rbac at startup.
func Manifest() []ManifestEntry {
	return []ManifestEntry{
		{ActionReportRead, false, false},
		{ActionBudgetRead, false, false},
		{ActionBudgetCreate, false, false},
		{ActionBudgetUpdate, false, false},
		{ActionBudgetDelete, false, false},
		{ActionMeterRead, false, false},
		{ActionRateCardRead, false, false},
		{ActionRateCardCreate, false, true},
		{ActionRateCardUpdate, false, true},
		{ActionAnomalyRead, false, false},
		{ActionAnomalyUpdate, false, false},
		{ActionReconRead, false, true},
		{ActionReconUpdate, false, true},
	}
}

// PlatformOnly reports whether an action is restricted to platform operators.
func PlatformOnly(action string) bool {
	for _, e := range Manifest() {
		if e.Action == action {
			return e.PlatformOnly
		}
	}
	return false
}
