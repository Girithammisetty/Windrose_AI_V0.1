package store

import (
	"context"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/events"
)

// EnsureSystemRoles seeds the 10 system roles (RBC-FR-020, tenant_id NULL =
// shared) and their default action matrix (RBC-FR-024). Idempotent: re-runs
// converge on the seed. Bindings of system roles are only writable here —
// the API rejects mutation (RBC-FR-013/020).
func (s *Store) EnsureSystemRoles(ctx context.Context, seeds []domain.RoleSeed) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		for _, seed := range seeds {
			var roleID uuid.UUID
			err := tx.QueryRow(ctx, `SELECT id FROM roles WHERE tenant_id IS NULL AND lower(name) = lower($1)`, seed.Name).Scan(&roleID)
			if err == pgx.ErrNoRows {
				roleID = NewID()
				if _, err := tx.Exec(ctx,
					`INSERT INTO roles (id, tenant_id, name, system) VALUES ($1, NULL, $2, true)`,
					roleID, seed.Name); err != nil {
					return fmt.Errorf("seed role %s: %w", seed.Name, err)
				}
			} else if err != nil {
				return err
			}
			if err := replaceRoleActions(ctx, tx, roleID, seed.Actions); err != nil {
				return fmt.Errorf("seed role %s actions: %w", seed.Name, err)
			}
		}
		return nil
	})
}

// SeedTenant provisions a tenant (consumed from identity.events.v1
// tenant.provisioned): one system permission group per system role, named
// after the role with an immutable binding (RBC-FR-013), plus the default
// public workspace "Default use case" (RBC-FR-006). Idempotent.
func (s *Store) SeedTenant(ctx context.Context, op Op) error {
	return s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		// System permission groups, one per system role.
		for _, roleName := range domain.SystemRoleNames() {
			var roleID uuid.UUID
			if err := tx.QueryRow(ctx,
				`SELECT id FROM roles WHERE tenant_id IS NULL AND system AND lower(name) = lower($1)`,
				roleName).Scan(&roleID); err != nil {
				return fmt.Errorf("system role %q missing (run EnsureSystemRoles first): %w", roleName, err)
			}
			var groupID uuid.UUID
			// Scope the lookup to THIS tenant. group names are unique only per
			// (tenant, type) — without the tenant filter, a superuser/BYPASSRLS
			// app connection (RLS not enforced) matches another tenant's identically
			// named system group and skips creation, leaving the new tenant with no
			// permission groups (multi-tenant provisioning defect).
			err := tx.QueryRow(ctx,
				`SELECT id FROM groups WHERE tenant_id = $1 AND group_type = 'permission' AND lower(name) = lower($2)`,
				op.Tenant, roleName).Scan(&groupID)
			if err == pgx.ErrNoRows {
				groupID = NewID()
				if _, err := tx.Exec(ctx, `
					INSERT INTO groups (id, tenant_id, name, description, group_type, system)
					VALUES ($1,$2,$3,$4,'permission',true)`,
					groupID, op.Tenant, roleName, "System group for role "+roleName); err != nil {
					return fmt.Errorf("seed group %s: %w", roleName, err)
				}
			} else if err != nil {
				return err
			}
			if _, err := tx.Exec(ctx, `
				INSERT INTO group_roles (id, tenant_id, group_id, role_id) VALUES ($1,$2,$3,$4)
				ON CONFLICT (group_id, role_id) DO NOTHING`,
				NewID(), op.Tenant, groupID, roleID); err != nil {
				return fmt.Errorf("seed group_role %s: %w", roleName, err)
			}
		}

		// Default public workspace.
		var wsID uuid.UUID
		// Tenant-scoped for the same reason as the group lookup above: workspace
		// names are unique per tenant, so an untenanted match would alias another
		// tenant's default workspace and skip creating this tenant's own.
		err := tx.QueryRow(ctx,
			`SELECT id FROM workspaces WHERE tenant_id = $1 AND lower(name) = lower($2)`,
			op.Tenant, domain.DefaultWorkspaceName).Scan(&wsID)
		if err == pgx.ErrNoRows {
			wsID = NewID()
			if _, err := tx.Exec(ctx, `
				INSERT INTO workspaces (id, tenant_id, name, description, public, created_by)
				VALUES ($1,$2,$3,'Default workspace',true,$4)`,
				wsID, op.Tenant, domain.DefaultWorkspaceName, op.Actor.ID); err != nil {
				return fmt.Errorf("seed default workspace: %w", err)
			}
			if err := op.emit(ctx, tx, events.EvWorkspaceDefaultCreated, workspaceURN(op.Tenant, wsID),
				map[string]any{"name": domain.DefaultWorkspaceName}); err != nil {
				return err
			}
		} else if err != nil {
			return err
		}

		// Friendly preset roles (task-shaped, EDITABLE custom roles). Created
		// once per tenant; if one already exists (by name) leave it as-is so a
		// tenant's edits/renames to a preset are never clobbered on re-seed.
		for _, preset := range domain.PresetRoleSeeds() {
			var existing uuid.UUID
			perr := tx.QueryRow(ctx,
				`SELECT id FROM roles WHERE tenant_id = $1 AND lower(name) = lower($2)`,
				op.Tenant, preset.Name).Scan(&existing)
			if perr == nil {
				continue // already present — preserve any tenant customisation
			}
			if perr != pgx.ErrNoRows {
				return perr
			}
			roleID := NewID()
			if _, err := tx.Exec(ctx,
				`INSERT INTO roles (id, tenant_id, name, system) VALUES ($1,$2,$3,false)`,
				roleID, op.Tenant, preset.Name); err != nil {
				return fmt.Errorf("seed preset role %s: %w", preset.Name, err)
			}
			if err := replaceRoleActions(ctx, tx, roleID, preset.Actions); err != nil {
				return fmt.Errorf("seed preset role %s actions: %w", preset.Name, err)
			}
		}
		return nil
	})
}
