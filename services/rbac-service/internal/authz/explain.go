package authz

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/projection"
)

// ChainStep is one link of the explain chain (RBC-FR-046, US-7).
type ChainStep struct {
	Type            string `json:"type"` // membership | role | workspace_assignment | grant | flag | scope_excluded
	Group           string `json:"group,omitempty"`
	GroupType       string `json:"group_type,omitempty"`
	Role            string `json:"role,omitempty"`
	Action          string `json:"action,omitempty"`
	WorkspaceScoped *bool  `json:"workspace_scoped,omitempty"`
	ViaGroup        string `json:"via_group,omitempty"`
	Workspace       string `json:"workspace,omitempty"`
	Level           string `json:"level,omitempty"`
	Subject         string `json:"subject,omitempty"`
	Admin           *bool  `json:"admin,omitempty"`
	Detail          string `json:"detail,omitempty"`
}

// Explanation is the /authz/explain response.
type Explanation struct {
	Allowed bool        `json:"allowed"`
	Reason  string      `json:"reason"`
	Chain   []ChainStep `json:"chain"`
}

// Explain answers "why can user X perform action Y on resource Z" with the
// full grant chain: memberships -> roles -> actions, workspace assignment
// provenance, matching content grants, admin flags, and OBO scope exclusions.
func (c *Checker) Explain(ctx context.Context, in Input) (Explanation, error) {
	tenant, err := uuid.Parse(in.Tenant)
	if err != nil {
		return Explanation{Allowed: false, Reason: ReasonTenantMismatch, Chain: []ChainStep{}}, nil
	}
	user := in.EffectiveUser()

	// Decision comes from the same SQL-ground-truth path as /authz/check.
	snap, err := c.Store.LoadSnapshot(ctx, tenant, user)
	if err != nil {
		return Explanation{}, err
	}
	flat := projection.Flatten(snap)
	reader := projection.NewFlatReader(flat, snap.Catalog, snap.ArchivedWorkspaceIDs)
	d, err := Decide(ctx, in, reader)
	if err != nil {
		return Explanation{}, err
	}

	exp := Explanation{Allowed: d.Allowed, Reason: d.Reason, Chain: []ChainStep{}}

	// OBO intersection failure explains itself (AC-7).
	if in.Subject.Typ == domain.TypAgentOBO && !inScopes(in.Subject.Scopes, in.Action) {
		exp.Chain = append(exp.Chain, ChainStep{
			Type:   "scope_excluded",
			Action: in.Action,
			Detail: "agent token scopes exclude this action (intersection rule)",
		})
	}

	err = c.Store.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		// membership + role steps for groups whose roles grant the action.
		rows, err := tx.Query(ctx, `
			SELECT DISTINCT g.name, g.group_type, r.name, a.workspace_scoped
			FROM members m
			JOIN groups g   ON g.id = m.group_id AND g.group_type = 'permission'
			JOIN group_roles gr ON gr.group_id = g.id
			JOIN roles r    ON r.id = gr.role_id
			JOIN role_actions ra ON ra.role_id = r.id AND ra.action = $2
			JOIN actions a  ON a.action = ra.action
			WHERE m.user_id = $1 AND (m.expires_at IS NULL OR m.expires_at > now())
			ORDER BY g.name, r.name`, user, in.Action)
		if err != nil {
			return err
		}
		seenGroups := map[string]bool{}
		for rows.Next() {
			var gName, gType, rName string
			var wsScoped bool
			if err := rows.Scan(&gName, &gType, &rName, &wsScoped); err != nil {
				rows.Close()
				return err
			}
			if !seenGroups[gName] {
				seenGroups[gName] = true
				exp.Chain = append(exp.Chain, ChainStep{Type: "membership", Group: gName, GroupType: gType})
			}
			ws := wsScoped
			exp.Chain = append(exp.Chain, ChainStep{Type: "role", Role: rName, Action: in.Action, WorkspaceScoped: &ws})
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}

		// Admin flag step.
		if snap.Admin {
			t := true
			exp.Chain = append(exp.Chain, ChainStep{Type: "flag", Admin: &t, Detail: "tenant admin bypasses action checks (tenant-bound)"})
		}

		// Workspace assignment provenance.
		if in.WorkspaceID != "" {
			wsID, err := uuid.Parse(in.WorkspaceID)
			if err == nil {
				var public bool
				err := tx.QueryRow(ctx, `SELECT public FROM workspaces WHERE id = $1`, wsID).Scan(&public)
				if err == nil {
					if public {
						exp.Chain = append(exp.Chain, ChainStep{Type: "workspace_assignment", Workspace: in.WorkspaceID, Detail: "public workspace"})
					} else {
						grows, err := tx.Query(ctx, `
							SELECT g.name FROM workspace_groups wg
							JOIN groups g ON g.id = wg.group_id
							JOIN members m ON m.group_id = g.id AND m.user_id = $2
								AND (m.expires_at IS NULL OR m.expires_at > now())
							WHERE wg.workspace_id = $1 ORDER BY g.name`, wsID, user)
						if err != nil {
							return err
						}
						for grows.Next() {
							var gName string
							if err := grows.Scan(&gName); err != nil {
								grows.Close()
								return err
							}
							exp.Chain = append(exp.Chain, ChainStep{Type: "workspace_assignment", Workspace: in.WorkspaceID, ViaGroup: gName})
						}
						grows.Close()
						if err := grows.Err(); err != nil {
							return err
						}
					}
				} else if err != pgx.ErrNoRows {
					return err
				}
			}
		}

		// Matching content grants (direct + via groups).
		if in.ResourceURN != "" {
			grows, err := tx.Query(ctx, `
				SELECT cg.level, cg.subject_type, cg.implicit, COALESCE(g.name, cg.subject_user_id)
				FROM content_grants cg
				LEFT JOIN groups g ON g.id = cg.subject_group_id
				WHERE cg.resource_urn = $1 AND (
					cg.subject_user_id = $2
					OR cg.subject_group_id IN (
						SELECT m.group_id FROM members m
						WHERE m.user_id = $2 AND (m.expires_at IS NULL OR m.expires_at > now()))
				) ORDER BY cg.id`, in.ResourceURN, user)
			if err != nil {
				return err
			}
			for grows.Next() {
				var level, subjType, subjName string
				var implicit bool
				if err := grows.Scan(&level, &subjType, &implicit, &subjName); err != nil {
					grows.Close()
					return err
				}
				step := ChainStep{Type: "grant", Level: level, Subject: subjType + ":" + subjName}
				if implicit {
					step.Detail = "implicit_creator"
				}
				exp.Chain = append(exp.Chain, step)
			}
			grows.Close()
			if err := grows.Err(); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return Explanation{}, err
	}
	return exp, nil
}
