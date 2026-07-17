// Package authz implements the decision paths: Decide evaluates the OPA data
// contract (RBC-FR-044) against the permissions_flat projection — the Rego
// policy in policy/ mirrors these exact semantics — plus the SQL fallback
// (/authz/check, RBC-FR-045) and the explain chain (/authz/explain, RBC-FR-046).
package authz

import (
	"context"
	"fmt"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/projection"
)

// Subject mirrors the OPA input subject (RBC-FR-044).
type Subject struct {
	ID     string   `json:"id"`
	Typ    string   `json:"typ"` // user | service | agent_obo | agent_autonomous
	OboSub string   `json:"obo_sub,omitempty"`
	Scopes []string `json:"scopes,omitempty"`
}

// Input is the OPA decision contract input.
type Input struct {
	Subject     Subject `json:"subject"`
	Action      string  `json:"action"`
	ResourceURN string  `json:"resource_urn,omitempty"`
	WorkspaceID string  `json:"workspace_id,omitempty"`
	Tenant      string  `json:"tenant"`
}

// Reason codes (stable; surfaced by /authz/check and /authz/explain).
const (
	ReasonAdminBypass          = "admin_bypass"
	ReasonRoleAction           = "role_action"
	ReasonWorkspaceAdmin       = "workspace_admin"
	ReasonResourceGrant        = "resource_grant"
	ReasonAutonomousScope      = "autonomous_scope"
	ReasonDenyDefault          = "deny_default"
	ReasonUnknownAction        = "unknown_action"
	ReasonScopeExcluded        = "scope_excluded"
	ReasonAutonomousDisabled   = "autonomous_disabled"
	ReasonTenantMismatch       = "tenant_mismatch"
	ReasonWorkspaceCtxRequired = "WORKSPACE_CONTEXT_REQUIRED"
	ReasonWorkspaceCtxForbid   = "WORKSPACE_CONTEXT_FORBIDDEN"
	ReasonWorkspaceArchived    = "workspace_archived"
	ReasonNotAssigned          = "workspace_not_assigned"
	ReasonProjectionMiss       = "projection_miss"
	ReasonUnknownPrincipal     = "unknown_principal_type"
)

// Decision is the evaluation outcome.
type Decision struct {
	Allowed bool   `json:"allowed"`
	Reason  string `json:"reason"`
	// Miss: the user's projection keys were absent — the caller should fall
	// back to SQL ground truth and warm the keys (RBC-FR-045).
	Miss bool `json:"miss,omitempty"`
}

func deny(reason string) Decision  { return Decision{Allowed: false, Reason: reason} }
func allow(reason string) Decision { return Decision{Allowed: true, Reason: reason} }

// EffectiveUser resolves whose projection is evaluated: OBO agents act
// against the original user's grants (MASTER-FR-015).
func (in Input) EffectiveUser() string {
	if in.Subject.Typ == domain.TypAgentOBO && in.Subject.OboSub != "" {
		return in.Subject.OboSub
	}
	return in.Subject.ID
}

// Decide implements RBC-FR-041/044 against a projection Reader. Semantics
// (deny-by-default, additive only — no negative grants):
//
//  1. tenant mismatch denies (admin flag is tenant-bound, BR-7);
//  2. agent_obo: allow iff the USER projection allows AND action ∈ token
//     scopes (intersection — an agent never widens user permissions, BR-6);
//     agent_autonomous: allow iff action ∈ scopes AND tenant enablement flag;
//  3. workspace-context validation (V1 workspace_dependent semantics, AC-3):
//     workspace-scoped actions require workspace_id; tenant-scoped must not
//     carry one;
//  4. admin flag short-circuits action checks but NOT the archived-workspace
//     write block (BR-7);
//  5. user path: workspace-scoped -> action ∈ ws set for that workspace
//     (absent key ⇒ not assigned ⇒ deny); tenant-scoped -> action ∈ tenant set;
//  6. resource-grant overlay: grant level's verbs allow the action's verb on
//     the granted URN (additive), still subject to the archived write block.
func Decide(ctx context.Context, in Input, r projection.Reader) (Decision, error) {
	if in.Tenant == "" || in.Subject.ID == "" || in.Action == "" {
		return deny(ReasonDenyDefault), nil
	}

	// Action catalog resolution.
	scoped, known, err := r.ActionScoped(ctx, in.Action)
	if err != nil {
		return deny(ReasonDenyDefault), err
	}
	if !known {
		return deny(ReasonUnknownAction), nil
	}

	// Principal-type handling (RBC-FR-044). An explicit default denies unknown
	// principal types (fail-closed), mirroring the Rego bundle where user_path
	// is undefined for anything outside this set — without it an unknown `typ`
	// would fall through to the user path and be ALLOWED (parity break).
	switch in.Subject.Typ {
	case domain.TypUser, domain.TypService:
		// user path below
	case domain.TypAgentAutonomous:
		if !inScopes(in.Subject.Scopes, in.Action) {
			return deny(ReasonScopeExcluded), nil
		}
		enabled, err := r.AutonomousEnabled(ctx, in.Tenant)
		if err != nil {
			return deny(ReasonDenyDefault), err
		}
		if !enabled {
			return deny(ReasonAutonomousDisabled), nil
		}
		return allow(ReasonAutonomousScope), nil
	case domain.TypAgentOBO:
		if !inScopes(in.Subject.Scopes, in.Action) {
			return deny(ReasonScopeExcluded), nil
		}
		// falls through to the user path (intersection with user grants)
	default:
		return deny(ReasonUnknownPrincipal), nil
	}

	user := in.EffectiveUser()

	// Workspace-context validation (AC-3).
	if scoped && in.WorkspaceID == "" {
		return deny(ReasonWorkspaceCtxRequired), nil
	}
	if !scoped && in.WorkspaceID != "" {
		return deny(ReasonWorkspaceCtxForbid), nil
	}

	verb := domain.ActionVerb(in.Action)

	flags, flagsFound, err := r.UserFlags(ctx, in.Tenant, user)
	if err != nil {
		return deny(ReasonDenyDefault), err
	}

	// Admin short-circuit — tenant-bound, archived-write block still applies.
	if flagsFound && flags.Admin {
		if in.WorkspaceID != "" {
			archived, err := r.ArchivedWorkspaces(ctx, in.Tenant)
			if err != nil {
				return deny(ReasonDenyDefault), err
			}
			if archived[in.WorkspaceID] && !domain.ArchivedReadVerbs[verb] {
				return deny(ReasonWorkspaceArchived), nil
			}
		}
		return allow(ReasonAdminBypass), nil
	}

	if scoped {
		entry, assigned, err := r.Workspace(ctx, in.Tenant, user, in.WorkspaceID)
		if err != nil {
			return deny(ReasonDenyDefault), err
		}
		if assigned {
			if entry.Archived && !domain.ArchivedReadVerbs[verb] {
				return deny(ReasonWorkspaceArchived), nil
			}
			if contains(entry.Actions, in.Action) {
				return allow(ReasonRoleAction), nil
			}
			// Workspace admin flag: all workspace-scoped actions in
			// workspaces the user administers.
			if hasWsAdmin(flags, in.WorkspaceID) {
				return allow(ReasonWorkspaceAdmin), nil
			}
		}
		// Resource-grant overlay (additive).
		if d, ok, err := grantOverlay(ctx, in, r, user, verb); err != nil || ok {
			return d, err
		}
		if !assigned {
			if !flagsFound {
				return Decision{Allowed: false, Reason: ReasonProjectionMiss, Miss: true}, nil
			}
			return deny(ReasonNotAssigned), nil
		}
		return deny(ReasonDenyDefault), nil
	}

	// Tenant-scoped path.
	actions, found, err := r.TenantActions(ctx, in.Tenant, user)
	if err != nil {
		return deny(ReasonDenyDefault), err
	}
	if found && contains(actions, in.Action) {
		return allow(ReasonRoleAction), nil
	}
	if d, ok, err := grantOverlay(ctx, in, r, user, verb); err != nil || ok {
		return d, err
	}
	if !found && !flagsFound {
		return Decision{Allowed: false, Reason: ReasonProjectionMiss, Miss: true}, nil
	}
	return deny(ReasonDenyDefault), nil
}

// grantOverlay checks the resource-grant path; ok=true means it produced a
// terminal decision (allow, or archived-write deny).
func grantOverlay(ctx context.Context, in Input, r projection.Reader, user, verb string) (Decision, bool, error) {
	if in.ResourceURN == "" {
		return Decision{}, false, nil
	}
	entry, found, err := r.Resource(ctx, in.Tenant, user, domain.URNHash(in.ResourceURN))
	if err != nil {
		return deny(ReasonDenyDefault), true, err
	}
	if !found {
		return Decision{}, false, nil
	}
	if !domain.LevelAllowsVerb(domain.GrantLevel(entry.Level), verb) {
		return Decision{}, false, nil
	}
	if entry.Archived && !domain.ArchivedReadVerbs[verb] {
		return deny(ReasonWorkspaceArchived), true, nil
	}
	return allow(fmt.Sprintf("%s:%s", ReasonResourceGrant, entry.Level)), true, nil
}

func hasWsAdmin(f projection.Flags, wsID string) bool {
	for _, id := range f.WsAdmin {
		if id.String() == wsID {
			return true
		}
	}
	return false
}

func inScopes(scopes []string, action string) bool {
	for _, s := range scopes {
		if s == action || s == "*" {
			return true
		}
	}
	return false
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
