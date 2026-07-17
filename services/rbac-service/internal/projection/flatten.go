// Package projection implements the permissions_flat projection
// (RBC-FR-040..048): the pure flattening algorithm, the Redis key scheme and
// writer, the reader contract consumed by decision paths, and the recompute
// worker fed by the transactional-outbox dirty queue.
package projection

import (
	"sort"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/rbac-service/internal/domain"
)

// WorkspaceRef is one workspace a user is assigned to (RBC-FR-003).
type WorkspaceRef struct {
	ID       uuid.UUID
	Archived bool
}

// ResourceGrant is the effective (max) grant level a user holds on one URN.
type ResourceGrant struct {
	URN         string
	Level       domain.GrantLevel
	WorkspaceID uuid.UUID
	Archived    bool // grant's workspace is archived
}

// Snapshot is the SQL ground truth for one user, loaded transactionally.
// Flatten is a pure function of it, which keeps the algorithm exhaustively
// unit-testable without a database.
type Snapshot struct {
	TenantID uuid.UUID
	UserID   string

	// Admin: member of a permission group bound to the system "Admin" role.
	Admin bool
	// UseCaseAdmin: holds the system "Use case Admin" role via a permission group.
	UseCaseAdmin bool

	// Actions: union over permission-group memberships -> roles -> actions.
	Actions []string

	// Roles: display names of the system/custom roles the user holds via
	// permission groups. Display-only metadata for the UI capability gate
	// (never consulted in a decision — decisions read Actions/Flags).
	Roles []string

	// AssignedWorkspaces: public workspaces + workspaces linked to content
	// groups the user belongs to (V1 `assigned_to_workspace?`).
	AssignedWorkspaces []WorkspaceRef

	// ResourceGrants: effective max level per URN (direct user grants +
	// grants to content groups the user belongs to that are still linked to
	// the grant's workspace).
	ResourceGrants []ResourceGrant

	// ArchivedWorkspaceIDs: all archived workspaces in the tenant (drives the
	// archived-write block, which even the admin flag does not bypass — BR-7).
	ArchivedWorkspaceIDs []uuid.UUID

	// Catalog: action -> workspace_scoped for every registered action.
	Catalog map[string]bool

	// Version: monotonic (projection_version_seq) for last-writer-wins.
	Version    int64
	ComputedAt time.Time
}

// Flags mirrors perm:{tenant}:{user}:flags.
type Flags struct {
	Admin   bool        `json:"admin"`
	WsAdmin []uuid.UUID `json:"ws_admin"`
	// Roles are the user's role display names (display-only; see Snapshot.Roles).
	Roles []string `json:"roles,omitempty"`
}

// WorkspaceEntry is the value of one perm:{t}:{u}:ws:{w} key.
type WorkspaceEntry struct {
	Actions  []string `json:"actions"`
	Archived bool     `json:"archived"`
}

// ResourceEntry is the value of one perm:{t}:{u}:res:{hash} key.
type ResourceEntry struct {
	URN         string `json:"urn"`
	Level       string `json:"level"`
	WorkspaceID string `json:"workspace_id"`
	Archived    bool   `json:"archived"`
}

// Flat is the flattened projection for one user — exactly what gets
// materialized into Redis (RBC-FR-040).
type Flat struct {
	TenantID uuid.UUID
	UserID   string

	// TenantActions: allowed tenant-scoped actions.
	TenantActions []string
	// WorkspaceActions: workspace id -> allowed workspace-scoped actions.
	// A missing workspace means "not assigned" (empty set => deny).
	WorkspaceActions map[uuid.UUID]WorkspaceEntry
	// Resources: urn hash -> grant entry.
	Resources map[string]ResourceEntry
	Flags     Flags

	// Catalog: action -> workspace_scoped, carried through from the Snapshot so
	// downstream writers (the Python single-key projection) can expand the
	// admin/ws-admin short-circuit over the full registered action set.
	Catalog map[string]bool

	Version    int64
	ComputedAt time.Time
}

// Flatten is the flattening algorithm per RBC-FR-041, a pure function:
//
//  1. union over permission-group memberships -> roles -> actions
//     (already unioned into Snapshot.Actions);
//  2. split by the catalog's workspace_scoped flag: tenant-scoped actions go
//     to the flat tenant set; workspace-scoped actions are intersected with
//     the user's assigned workspaces (RBC-FR-003) — one set per workspace;
//  3. archived workspaces retain only read verbs (read/list/export), so a
//     previously-assigned user keeps read access (RBC-FR-004/AC-14);
//  4. overlay resource grants (level -> verbs happens at decision time; the
//     projection stores the level per URN hash);
//  5. admin flag short-circuits at decision time and is tenant-bound; it is
//     carried in flags, never expanded into action sets.
//
// Deny-by-default: anything not present in the output is denied. There are no
// negative grants — the model is additive only (V1 parity).
func Flatten(s Snapshot) Flat {
	tenantActions := make([]string, 0)
	wsActions := make([]string, 0)
	for _, a := range dedupSorted(s.Actions) {
		scoped, known := s.Catalog[a]
		if !known {
			// Action no longer in catalog (deprecation window passed) — drop.
			continue
		}
		if scoped {
			wsActions = append(wsActions, a)
		} else {
			tenantActions = append(tenantActions, a)
		}
	}

	archivedWs := make([]string, 0, len(wsActions))
	for _, a := range wsActions {
		if domain.ArchivedReadVerbs[domain.ActionVerb(a)] {
			archivedWs = append(archivedWs, a)
		}
	}

	workspaces := make(map[uuid.UUID]WorkspaceEntry, len(s.AssignedWorkspaces))
	wsAdmin := make([]uuid.UUID, 0)
	for _, w := range s.AssignedWorkspaces {
		entry := WorkspaceEntry{Actions: wsActions, Archived: w.Archived}
		if w.Archived {
			entry.Actions = archivedWs
		}
		workspaces[w.ID] = entry
		if s.UseCaseAdmin {
			wsAdmin = append(wsAdmin, w.ID)
		}
	}
	sort.Slice(wsAdmin, func(i, j int) bool { return wsAdmin[i].String() < wsAdmin[j].String() })

	resources := make(map[string]ResourceEntry, len(s.ResourceGrants))
	for _, g := range s.ResourceGrants {
		h := domain.URNHash(g.URN)
		entry := ResourceEntry{
			URN:         g.URN,
			Level:       string(g.Level),
			WorkspaceID: g.WorkspaceID.String(),
			Archived:    g.Archived,
		}
		if prev, ok := resources[h]; ok {
			// Additive model: keep the max level across duplicate URN grants.
			entry.Level = string(domain.MaxLevel(domain.GrantLevel(prev.Level), g.Level))
			if prev.Archived != entry.Archived {
				// Same URN granted via two workspaces; treat as live if any
				// grant's workspace is live.
				entry.Archived = prev.Archived && entry.Archived
			}
		}
		resources[h] = entry
	}

	return Flat{
		TenantID:         s.TenantID,
		UserID:           s.UserID,
		TenantActions:    tenantActions,
		WorkspaceActions: workspaces,
		Resources:        resources,
		Flags:            Flags{Admin: s.Admin, WsAdmin: wsAdmin, Roles: dedupSorted(s.Roles)},
		Catalog:          s.Catalog,
		Version:          s.Version,
		ComputedAt:       s.ComputedAt,
	}
}

func dedupSorted(in []string) []string {
	m := make(map[string]struct{}, len(in))
	for _, s := range in {
		m[s] = struct{}{}
	}
	out := make([]string, 0, len(m))
	for s := range m {
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}
