package store

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/events"
)

const roleCols = `id, tenant_id, name, system, version, created_at, updated_at`

func scanRole(row pgx.Row) (domain.Role, error) {
	var r domain.Role
	err := row.Scan(&r.ID, &r.TenantID, &r.Name, &r.System, &r.Version, &r.CreatedAt, &r.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, ErrNotFound
	}
	return r, err
}

// CreateCustomRole implements RBC-FR-021 (named action sets, unique per tenant).
func (s *Store) CreateCustomRole(ctx context.Context, op Op, name string, actions []string) (domain.Role, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return domain.Role{}, &ValidationError{Code: CodeValidationFailed, Message: "name is required"}
	}
	var role domain.Role
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		if err := validateActionsExist(ctx, tx, actions); err != nil {
			return err
		}
		var err error
		role, err = scanRole(tx.QueryRow(ctx, `
			INSERT INTO roles (id, tenant_id, name, system) VALUES ($1,$2,$3,false) RETURNING `+roleCols,
			NewID(), op.Tenant, name))
		if err != nil {
			if isUniqueViolation(err) {
				return &ConflictError{Code: CodeConflict, Message: fmt.Sprintf("role %q already exists", name)}
			}
			return err
		}
		if err := replaceRoleActions(ctx, tx, role.ID, actions); err != nil {
			return err
		}
		role.Actions = dedupActions(actions)
		return op.emit(ctx, tx, events.EvRoleCreated, roleURN(op.Tenant, role.ID),
			map[string]any{"name": role.Name, "actions": role.Actions})
	})
	return role, err
}

// GetRole loads a role with its action bindings; system roles are visible to
// every tenant.
func (s *Store) GetRole(ctx context.Context, tenant uuid.UUID, id uuid.UUID) (domain.Role, error) {
	var role domain.Role
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		role, err = scanRole(tx.QueryRow(ctx, `SELECT `+roleCols+` FROM roles WHERE id = $1`, id))
		if err != nil {
			return err
		}
		role.Actions, err = roleActionsTx(ctx, tx, id)
		return err
	})
	return role, err
}

func roleActionsTx(ctx context.Context, tx pgx.Tx, roleID uuid.UUID) ([]string, error) {
	rows, err := tx.Query(ctx, `SELECT action FROM role_actions WHERE role_id = $1 ORDER BY action`, roleID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	actions := []string{}
	for rows.Next() {
		var a string
		if err := rows.Scan(&a); err != nil {
			return nil, err
		}
		actions = append(actions, a)
	}
	return actions, rows.Err()
}

// ListRoles returns system + tenant custom roles.
func (s *Store) ListRoles(ctx context.Context, tenant uuid.UUID, cursor string, limit int) (Page[domain.Role], error) {
	limit = ClampLimit(limit)
	var page Page[domain.Role]
	args := []any{limit + 1}
	where := "true"
	if cursor != "" {
		// Composite cursor "<0|1>:<uuid>" for the (system DESC, id DESC) order.
		curSystem, cidStr, ok := strings.Cut(cursor, ":")
		cid, err := uuid.Parse(cidStr)
		if !ok || err != nil {
			return page, &ValidationError{Code: CodeValidationFailed, Message: "invalid cursor"}
		}
		sysBool := curSystem == "1"
		// Rows AFTER the cursor in (system DESC, id DESC): a lower system class,
		// or the same class with a smaller (older) id.
		where = "(system < $2 OR (system = $2 AND id < $3))"
		args = append(args, sysBool, cid)
	}
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		// System roles first (a small, stable set — always on page 1 so their
		// "Edit hidden" behavior is visible), then newest-first custom roles so
		// a just-created role is near the TOP of page 1 rather than buried past
		// 50 older ones. UUIDv7 ids are time-ordered.
		rows, err := tx.Query(ctx, `SELECT `+roleCols+` FROM roles WHERE `+where+` ORDER BY system DESC, id DESC LIMIT $1`, args...)
		if err != nil {
			return err
		}
		var roles []domain.Role
		for rows.Next() {
			r, err := scanRole(rows)
			if err != nil {
				rows.Close()
				return err
			}
			roles = append(roles, r)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}
		// Populate each role's action set (they live in a separate table) so list
		// rows carry actions — the roles admin edit dialog prefills its actions
		// textarea from these; an empty set otherwise disables Save. Sequential
		// (one query at a time per pgx conn) after the cursor is drained.
		for i := range roles {
			if roles[i].Actions, err = roleActionsTx(ctx, tx, roles[i].ID); err != nil {
				return err
			}
		}
		page.Data = roles
		return nil
	})
	if err != nil {
		return page, err
	}
	if len(page.Data) > limit {
		page.Data = page.Data[:limit]
		page.HasMore = true
		last := page.Data[limit-1]
		sys := "0"
		if last.System {
			sys = "1"
		}
		page.NextCursor = sys + ":" + last.ID.String()
	}
	return page, nil
}

// SetRoleActions replaces a custom role's action set (RBC-FR-023: versioned,
// emits role.updated with an added/removed diff, recomputes affected users).
func (s *Store) SetRoleActions(ctx context.Context, op Op, roleID uuid.UUID, actions []string) (domain.Role, error) {
	var role domain.Role
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		var err error
		role, err = scanRole(tx.QueryRow(ctx, `SELECT `+roleCols+` FROM roles WHERE id = $1 FOR UPDATE`, roleID))
		if err != nil {
			return err
		}
		if role.System {
			return &ConflictError{Code: CodeSystemImmutable, Message: "system role bindings are immutable"}
		}
		if err := validateActionsExist(ctx, tx, actions); err != nil {
			return err
		}
		before, err := roleActionsTx(ctx, tx, roleID)
		if err != nil {
			return err
		}
		if err := replaceRoleActions(ctx, tx, roleID, actions); err != nil {
			return err
		}
		role, err = scanRole(tx.QueryRow(ctx, `
			UPDATE roles SET version = version + 1, updated_at = now() WHERE id = $1 RETURNING `+roleCols, roleID))
		if err != nil {
			return err
		}
		role.Actions = dedupActions(actions)
		added, removed := diffActions(before, role.Actions)
		if err := markDirtyRoleUsers(ctx, tx, op.Tenant, roleID, "role.updated"); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvRoleUpdated, roleURN(op.Tenant, roleID),
			map[string]any{"name": role.Name, "version": role.Version, "added": added, "removed": removed})
	})
	return role, err
}

// UpdateRole edits a custom role's name and/or its composed action set in a
// single transaction (RBC-FR-021/023). Both name and actions are optional
// (nil = leave unchanged); at least one must be supplied. Renaming a role and
// replacing its bindings share the versioning/dirty-recompute semantics of
// SetRoleActions: the version bumps and affected users are marked dirty only
// when the action set actually changes. System roles are immutable.
func (s *Store) UpdateRole(ctx context.Context, op Op, roleID uuid.UUID, name *string, actions *[]string) (domain.Role, error) {
	if name == nil && actions == nil {
		return domain.Role{}, &ValidationError{Code: CodeValidationFailed, Message: "name or actions is required"}
	}
	var namePtr *string
	if name != nil {
		trimmed := strings.TrimSpace(*name)
		if trimmed == "" {
			return domain.Role{}, &ValidationError{Code: CodeValidationFailed, Message: "name is required"}
		}
		namePtr = &trimmed
	}
	var role domain.Role
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		cur, err := scanRole(tx.QueryRow(ctx, `SELECT `+roleCols+` FROM roles WHERE id = $1 FOR UPDATE`, roleID))
		if err != nil {
			return err
		}
		if cur.System {
			return &ConflictError{Code: CodeSystemImmutable, Message: "system roles cannot be edited"}
		}
		var before []string
		versionBump := 0
		if actions != nil {
			if err := validateActionsExist(ctx, tx, *actions); err != nil {
				return err
			}
			if before, err = roleActionsTx(ctx, tx, roleID); err != nil {
				return err
			}
			if err := replaceRoleActions(ctx, tx, roleID, *actions); err != nil {
				return err
			}
			versionBump = 1
		}
		role, err = scanRole(tx.QueryRow(ctx, `
			UPDATE roles SET name = COALESCE($2, name), version = version + $3, updated_at = now()
			WHERE id = $1 RETURNING `+roleCols, roleID, namePtr, versionBump))
		if err != nil {
			if isUniqueViolation(err) && namePtr != nil {
				return &ConflictError{Code: CodeConflict, Message: fmt.Sprintf("role %q already exists", *namePtr)}
			}
			return err
		}
		payload := map[string]any{"name": role.Name, "version": role.Version}
		if actions != nil {
			role.Actions = dedupActions(*actions)
			added, removed := diffActions(before, role.Actions)
			payload["added"], payload["removed"] = added, removed
			if err := markDirtyRoleUsers(ctx, tx, op.Tenant, roleID, "role.updated"); err != nil {
				return err
			}
		} else if role.Actions, err = roleActionsTx(ctx, tx, roleID); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvRoleUpdated, roleURN(op.Tenant, roleID), payload)
	})
	return role, err
}

// DeleteRole is blocked while any group binds it (BR-4: 409 ROLE_IN_USE) and
// for system roles.
func (s *Store) DeleteRole(ctx context.Context, op Op, roleID uuid.UUID) error {
	return s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		role, err := scanRole(tx.QueryRow(ctx, `SELECT `+roleCols+` FROM roles WHERE id = $1 FOR UPDATE`, roleID))
		if err != nil {
			return err
		}
		if role.System {
			return &ConflictError{Code: CodeSystemImmutable, Message: "system roles cannot be deleted"}
		}
		var bound bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM group_roles WHERE role_id = $1)`, roleID).Scan(&bound); err != nil {
			return err
		}
		if bound {
			return &ConflictError{Code: CodeRoleInUse, Message: "role is assigned to one or more groups"}
		}
		if _, err := tx.Exec(ctx, `DELETE FROM roles WHERE id = $1`, roleID); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvRoleDeleted, roleURN(op.Tenant, roleID), map[string]any{"name": role.Name})
	})
}

// BindGroupRole attaches a role to a permission group (idempotent).
func (s *Store) BindGroupRole(ctx context.Context, op Op, groupID, roleID uuid.UUID) error {
	return s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		g, err := s.getGroupTx(ctx, tx, groupID)
		if err != nil {
			return err
		}
		if g.Type != domain.GroupTypePermission {
			return &ValidationError{Code: CodeValidationFailed, Message: "roles bind to permission groups only"}
		}
		if g.System {
			return &ConflictError{Code: CodeSystemImmutable, Message: "system group role bindings are immutable"}
		}
		if _, err := scanRole(tx.QueryRow(ctx, `SELECT `+roleCols+` FROM roles WHERE id = $1`, roleID)); err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `
			INSERT INTO group_roles (id, tenant_id, group_id, role_id) VALUES ($1,$2,$3,$4)
			ON CONFLICT (group_id, role_id) DO NOTHING`, NewID(), op.Tenant, groupID, roleID)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return nil
		}
		if err := markDirtyGroupMembers(ctx, tx, op.Tenant, groupID, "group_role.bound"); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvGroupUpdated, groupURN(op.Tenant, groupID),
			map[string]any{"role_id": roleID.String(), "binding": "added"})
	})
}

// UnbindGroupRole detaches a role from a permission group.
func (s *Store) UnbindGroupRole(ctx context.Context, op Op, groupID, roleID uuid.UUID) error {
	return s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		g, err := s.getGroupTx(ctx, tx, groupID)
		if err != nil {
			return err
		}
		if g.System {
			return &ConflictError{Code: CodeSystemImmutable, Message: "system group role bindings are immutable"}
		}
		tag, err := tx.Exec(ctx, `DELETE FROM group_roles WHERE group_id=$1 AND role_id=$2`, groupID, roleID)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return ErrNotFound
		}
		if err := markDirtyGroupMembers(ctx, tx, op.Tenant, groupID, "group_role.unbound"); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvGroupUpdated, groupURN(op.Tenant, groupID),
			map[string]any{"role_id": roleID.String(), "binding": "removed"})
	})
}

// RolesForGroup returns the roles currently bound to a permission group,
// cursor-paginated by role id — the read side of BindGroupRole (mirrors
// ListMembers). RLS hides other tenants' rows, so an invisible group 404s.
func (s *Store) RolesForGroup(ctx context.Context, tenant uuid.UUID, groupID uuid.UUID, cursor string, limit int) (Page[domain.Role], error) {
	limit = ClampLimit(limit)
	var page Page[domain.Role]
	args := []any{groupID, limit + 1}
	where := "gr.group_id = $1"
	if cursor != "" {
		cid, err := uuid.Parse(cursor)
		if err != nil {
			return page, &ValidationError{Code: CodeValidationFailed, Message: "invalid cursor"}
		}
		where += " AND r.id > $3"
		args = append(args, cid)
	}
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		if _, err := s.getGroupTx(ctx, tx, groupID); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, `
			SELECT r.id, r.tenant_id, r.name, r.system, r.version, r.created_at, r.updated_at
			FROM group_roles gr JOIN roles r ON r.id = gr.role_id
			WHERE `+where+` ORDER BY r.id LIMIT $2`, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			role, err := scanRole(rows)
			if err != nil {
				return err
			}
			page.Data = append(page.Data, role)
		}
		return rows.Err()
	})
	if err != nil {
		return page, err
	}
	if len(page.Data) > limit {
		page.Data = page.Data[:limit]
		page.HasMore = true
		page.NextCursor = page.Data[limit-1].ID.String()
	}
	return page, nil
}

func validateActionsExist(ctx context.Context, tx pgx.Tx, actions []string) error {
	if len(actions) == 0 {
		return nil
	}
	for _, a := range actions {
		if _, _, _, err := domain.ParseAction(a); err != nil {
			return &ValidationError{Code: CodeValidationFailed, Message: err.Error()}
		}
	}
	rows, err := tx.Query(ctx, `SELECT action FROM actions WHERE action = ANY($1)`, actions)
	if err != nil {
		return err
	}
	defer rows.Close()
	known := map[string]bool{}
	for rows.Next() {
		var a string
		if err := rows.Scan(&a); err != nil {
			return err
		}
		known[a] = true
	}
	if err := rows.Err(); err != nil {
		return err
	}
	var missing []string
	for _, a := range dedupActions(actions) {
		if !known[a] {
			missing = append(missing, a)
		}
	}
	if len(missing) > 0 {
		return &ValidationError{Code: CodeValidationFailed, Message: "unknown catalog actions", Details: missing}
	}
	return nil
}

func replaceRoleActions(ctx context.Context, tx pgx.Tx, roleID uuid.UUID, actions []string) error {
	if _, err := tx.Exec(ctx, `DELETE FROM role_actions WHERE role_id = $1`, roleID); err != nil {
		return err
	}
	for _, a := range dedupActions(actions) {
		if _, err := tx.Exec(ctx, `INSERT INTO role_actions (role_id, action) VALUES ($1,$2)`, roleID, a); err != nil {
			return err
		}
	}
	return nil
}

func dedupActions(in []string) []string {
	m := map[string]bool{}
	out := []string{}
	for _, a := range in {
		if !m[a] {
			m[a] = true
			out = append(out, a)
		}
	}
	sort.Strings(out)
	return out
}

func diffActions(before, after []string) (added, removed []string) {
	b := map[string]bool{}
	for _, a := range before {
		b[a] = true
	}
	af := map[string]bool{}
	for _, a := range after {
		af[a] = true
	}
	added, removed = []string{}, []string{}
	for _, a := range after {
		if !b[a] {
			added = append(added, a)
		}
	}
	for _, a := range before {
		if !af[a] {
			removed = append(removed, a)
		}
	}
	return added, removed
}

func roleURN(tenant, id uuid.UUID) string {
	return fmt.Sprintf("wr:%s:rbac:role/%s", tenant, id)
}
