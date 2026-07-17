package store

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/projection"
)

// LoadSnapshot reads the SQL ground truth for one user inside a single
// transaction (consistent view) and allocates a monotonic version from
// projection_version_seq for last-writer-wins (RBC-FR-048).
func (s *Store) LoadSnapshot(ctx context.Context, tenant uuid.UUID, userID string) (projection.Snapshot, error) {
	snap := projection.Snapshot{TenantID: tenant, UserID: userID, ComputedAt: time.Now().UTC()}
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		if err := tx.QueryRow(ctx, `SELECT nextval('projection_version_seq')`).Scan(&snap.Version); err != nil {
			return err
		}

		// 1. Group memberships (non-expired), split by kind.
		rows, err := tx.Query(ctx, `
			SELECT g.id, g.group_type FROM members m
			JOIN groups g ON g.id = m.group_id
			WHERE m.user_id = $1 AND (m.expires_at IS NULL OR m.expires_at > now())`, userID)
		if err != nil {
			return err
		}
		var permGroups, contentGroups []uuid.UUID
		for rows.Next() {
			var id uuid.UUID
			var t domain.GroupType
			if err := rows.Scan(&id, &t); err != nil {
				rows.Close()
				return err
			}
			if t == domain.GroupTypePermission {
				permGroups = append(permGroups, id)
			} else {
				contentGroups = append(contentGroups, id)
			}
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}

		// 2. Roles via permission groups; detect the two special system roles.
		if len(permGroups) > 0 {
			rows, err = tx.Query(ctx, `
				SELECT DISTINCT r.id, r.name, r.system
				FROM group_roles gr JOIN roles r ON r.id = gr.role_id
				WHERE gr.group_id = ANY($1)`, permGroups)
			if err != nil {
				return err
			}
			var roleIDs []uuid.UUID
			for rows.Next() {
				var id uuid.UUID
				var name string
				var system bool
				if err := rows.Scan(&id, &name, &system); err != nil {
					rows.Close()
					return err
				}
				roleIDs = append(roleIDs, id)
				snap.Roles = append(snap.Roles, name)
				if system && name == domain.RoleAdmin {
					snap.Admin = true
				}
				if system && name == domain.RoleUseCaseAdmin {
					snap.UseCaseAdmin = true
				}
			}
			rows.Close()
			if err := rows.Err(); err != nil {
				return err
			}

			// 3. Action union over roles (RBC-FR-041 step 1).
			if len(roleIDs) > 0 {
				rows, err = tx.Query(ctx, `SELECT DISTINCT action FROM role_actions WHERE role_id = ANY($1)`, roleIDs)
				if err != nil {
					return err
				}
				for rows.Next() {
					var a string
					if err := rows.Scan(&a); err != nil {
						rows.Close()
						return err
					}
					snap.Actions = append(snap.Actions, a)
				}
				rows.Close()
				if err := rows.Err(); err != nil {
					return err
				}
			}
		}

		// 4. Assigned workspaces: public OR linked via content groups (RBC-FR-003).
		rows, err = tx.Query(ctx, `
			SELECT DISTINCT w.id, (w.archived_at IS NOT NULL)
			FROM workspaces w
			WHERE w.public
			   OR EXISTS (
				SELECT 1 FROM workspace_groups wg
				WHERE wg.workspace_id = w.id AND wg.group_id = ANY($1))`,
			contentGroupsOrEmpty(contentGroups))
		if err != nil {
			return err
		}
		for rows.Next() {
			var ref projection.WorkspaceRef
			if err := rows.Scan(&ref.ID, &ref.Archived); err != nil {
				rows.Close()
				return err
			}
			snap.AssignedWorkspaces = append(snap.AssignedWorkspaces, ref)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}

		// 5. Resource grants: direct + via content groups still linked to the
		// grant's workspace (integrity defense-in-depth, RBC-FR-031).
		rows, err = tx.Query(ctx, `
			SELECT g.resource_urn, g.level, g.workspace_id, (w.archived_at IS NOT NULL)
			FROM content_grants g JOIN workspaces w ON w.id = g.workspace_id
			WHERE g.subject_user_id = $1
			UNION ALL
			SELECT g.resource_urn, g.level, g.workspace_id, (w.archived_at IS NOT NULL)
			FROM content_grants g
			JOIN workspaces w ON w.id = g.workspace_id
			JOIN workspace_groups wg ON wg.group_id = g.subject_group_id AND wg.workspace_id = g.workspace_id
			WHERE g.subject_group_id = ANY($2)`,
			userID, contentGroupsOrEmpty(contentGroups))
		if err != nil {
			return err
		}
		for rows.Next() {
			var g projection.ResourceGrant
			var level string
			if err := rows.Scan(&g.URN, &level, &g.WorkspaceID, &g.Archived); err != nil {
				rows.Close()
				return err
			}
			g.Level = domain.GrantLevel(level)
			snap.ResourceGrants = append(snap.ResourceGrants, g)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}

		// 6. Tenant archived workspaces (admin path archived-write block).
		rows, err = tx.Query(ctx, `SELECT id FROM workspaces WHERE archived_at IS NOT NULL`)
		if err != nil {
			return err
		}
		for rows.Next() {
			var id uuid.UUID
			if err := rows.Scan(&id); err != nil {
				rows.Close()
				return err
			}
			snap.ArchivedWorkspaceIDs = append(snap.ArchivedWorkspaceIDs, id)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}

		// 7. Catalog.
		rows, err = tx.Query(ctx, `SELECT action, workspace_scoped FROM actions`)
		if err != nil {
			return err
		}
		snap.Catalog = map[string]bool{}
		for rows.Next() {
			var a string
			var scoped bool
			if err := rows.Scan(&a, &scoped); err != nil {
				rows.Close()
				return err
			}
			snap.Catalog[a] = scoped
		}
		rows.Close()
		return rows.Err()
	})
	return snap, err
}

func contentGroupsOrEmpty(ids []uuid.UUID) []uuid.UUID {
	if ids == nil {
		return []uuid.UUID{}
	}
	return ids
}

// MarkDirty enqueues explicit users for recompute outside a mutation
// (rebuilds, refresh-on-read, event handlers).
func (s *Store) MarkDirty(ctx context.Context, tenant uuid.UUID, users []string, reason string) error {
	return s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		return markDirtyUsers(ctx, tx, tenant, users, reason)
	})
}

// MarkTenantDirty enqueues every known user of a tenant (full rebuild,
// RBC-FR-043).
func (s *Store) MarkTenantDirty(ctx context.Context, tenant uuid.UUID, reason string) (int64, error) {
	var count int64
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		if err := markDirtyAllTenantUsers(ctx, tx, tenant, reason); err != nil {
			return err
		}
		return tx.QueryRow(ctx, `
			SELECT count(DISTINCT user_id) FROM projection_dirty WHERE tenant_id = $1 AND claimed_at IS NULL`,
			tenant).Scan(&count)
	})
	return count, err
}

// DirtyClaim groups the claimed dirty rows of one (tenant, user).
type DirtyClaim struct {
	TenantID       uuid.UUID
	UserID         string
	IDs            []int64
	OldestEnqueued time.Time
}

// ClaimDirty claims a batch of dirty rows (SKIP LOCKED; rows claimed longer
// than visibility ago are reclaimed — BR-8 crash recovery) and groups them
// per user. At-least-once: rows are deleted only after a successful write.
func (s *Store) ClaimDirty(ctx context.Context, workerID string, batch int, visibility time.Duration) ([]DirtyClaim, error) {
	byUser := map[string]*DirtyClaim{}
	var order []string
	err := s.WithWorker(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			WITH c AS (
				SELECT id FROM projection_dirty
				WHERE claimed_at IS NULL OR claimed_at < now() - $1::interval
				ORDER BY id
				LIMIT $2
				FOR UPDATE SKIP LOCKED
			)
			UPDATE projection_dirty d SET claimed_at = now(), claimed_by = $3
			FROM c WHERE d.id = c.id
			RETURNING d.id, d.tenant_id, d.user_id, d.enqueued_at`,
			visibility.String(), batch, workerID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id int64
			var tenant uuid.UUID
			var user string
			var enq time.Time
			if err := rows.Scan(&id, &tenant, &user, &enq); err != nil {
				return err
			}
			key := tenant.String() + "|" + user
			c, ok := byUser[key]
			if !ok {
				c = &DirtyClaim{TenantID: tenant, UserID: user, OldestEnqueued: enq}
				byUser[key] = c
				order = append(order, key)
			}
			c.IDs = append(c.IDs, id)
			if enq.Before(c.OldestEnqueued) {
				c.OldestEnqueued = enq
			}
		}
		return rows.Err()
	})
	if err != nil {
		return nil, err
	}
	out := make([]DirtyClaim, 0, len(order))
	for _, k := range order {
		out = append(out, *byUser[k])
	}
	return out, nil
}

// DeleteDirty removes processed dirty rows (completes the at-least-once cycle).
func (s *Store) DeleteDirty(ctx context.Context, ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	return s.WithWorker(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `DELETE FROM projection_dirty WHERE id = ANY($1)`, ids)
		return err
	})
}

// DirtyDepth reports pending queue depth (metrics/tests).
func (s *Store) DirtyDepth(ctx context.Context) (int, error) {
	var n int
	err := s.WithWorker(ctx, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT count(*) FROM projection_dirty`).Scan(&n)
	})
	return n, err
}

// TenantUserIDs lists every user known to rbac in a tenant (rebuild scope).
func (s *Store) TenantUserIDs(ctx context.Context, tenant uuid.UUID) ([]string, error) {
	var users []string
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT DISTINCT user_id FROM members
			UNION
			SELECT DISTINCT subject_user_id FROM content_grants WHERE subject_user_id IS NOT NULL
			ORDER BY 1`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var u string
			if err := rows.Scan(&u); err != nil {
				return err
			}
			users = append(users, u)
		}
		return rows.Err()
	})
	return users, err
}

// NextVersion allocates a projection version outside a snapshot (tenant-level
// key writes such as archived_ws and the catalog).
func (s *Store) NextVersion(ctx context.Context) (int64, error) {
	var v int64
	err := s.pool.QueryRow(ctx, `SELECT nextval('projection_version_seq')`).Scan(&v)
	return v, err
}
