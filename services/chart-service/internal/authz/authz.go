// Package authz is chart-service's authorization port (MASTER-FR-012).
// Decisions come from the local OPA sidecar reading the Redis permissions_flat
// projection via go-common/opaclient — rbac-service is never called
// synchronously in the request path. The real runtime authorizer is OPA
// (opa.go); the permissive/static doubles here are used only by unit tests.
package authz

import "context"

// Input is one authorization question (MASTER-FR-012/016).
type Input struct {
	Subject     Subject
	Action      string
	ResourceURN string
	WorkspaceID string
	Tenant      string
}

// Subject describes the caller.
type Subject struct {
	ID     string
	Typ    string
	OboSub string
	Scopes []string
}

// Authorizer answers allow/deny. The real implementation is *OPA.
type Authorizer interface {
	Allow(ctx context.Context, in Input) bool
}

// AllowAll is the permissive unit-test double (never wired from cmd/server).
type AllowAll struct{}

// Allow always permits.
func (AllowAll) Allow(context.Context, Input) bool { return true }

// Static denies the listed actions and allows the rest (authz-matrix unit
// double).
type Static struct{ Denied map[string]bool }

// Allow permits unless the action is in Denied.
func (s Static) Allow(_ context.Context, in Input) bool { return !s.Denied[in.Action] }

// Actions (MASTER-FR-016 naming: <service>.<resource>.<verb>). Every verb here
// MUST be in rbac's canonical closed verb set (RBC-FR-022:
// read,list,create,update,delete,execute,assign,approve,admin,export,share) —
// rbac rejects the ENTIRE registration batch if any action carries a
// non-canonical verb. State changes that V1 called "archive/restore/link/unlink"
// are modelled as `update` (they flip a persisted flag / back-reference), not
// as their own verbs.
const (
	ActionDashboardCreate = "chart.dashboard.create"
	ActionDashboardRead   = "chart.dashboard.read"
	ActionDashboardUpdate = "chart.dashboard.update" // also guards archive/restore
	ActionDashboardDelete = "chart.dashboard.delete"
	ActionDashboardShare  = "chart.dashboard.share"
	ActionDashboardExport = "chart.dashboard.export"
	ActionChartCreate     = "chart.chart.create"
	ActionChartRead       = "chart.chart.read"
	ActionChartUpdate     = "chart.chart.update" // also guards link/unlink
	ActionChartDelete     = "chart.chart.delete"
	ActionChartExport     = "chart.chart.export"
)

// ManifestEntry is one action chart-service registers with rbac at startup
// (RBC-FR-022). workspace_scoped mirrors rbac's grant scoping.
type ManifestEntry struct {
	Action          string
	WorkspaceScoped bool
}

// Manifest returns all chart-service actions — the exact set the route guards
// authorize against (kept consistent by TestManifestVerbsAreCanonical and
// TestGuardedActionsAreRegistered).
func Manifest() []ManifestEntry {
	return []ManifestEntry{
		{ActionDashboardCreate, true}, {ActionDashboardRead, true}, {ActionDashboardUpdate, true},
		{ActionDashboardDelete, true}, {ActionDashboardShare, true}, {ActionDashboardExport, true},
		{ActionChartCreate, true}, {ActionChartRead, true}, {ActionChartUpdate, true},
		{ActionChartDelete, true}, {ActionChartExport, true},
	}
}
