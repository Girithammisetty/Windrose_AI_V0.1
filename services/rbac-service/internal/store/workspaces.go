package store

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/domain"
	"github.com/windrose-ai/rbac-service/internal/events"
)

const workspaceCols = `id, tenant_id, name, description, public, created_by, archived_at, created_at, updated_at`

func scanWorkspace(row pgx.Row) (domain.Workspace, error) {
	var w domain.Workspace
	err := row.Scan(&w.ID, &w.TenantID, &w.Name, &w.Description, &w.Public, &w.CreatedBy, &w.ArchivedAt, &w.CreatedAt, &w.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return w, ErrNotFound
	}
	return w, err
}

// CreateWorkspace implements RBC-FR-001 (per-tenant unique name, case-insensitive).
func (s *Store) CreateWorkspace(ctx context.Context, op Op, name, description string, public bool) (domain.Workspace, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return domain.Workspace{}, &ValidationError{Code: CodeValidationFailed, Message: "name is required", Details: map[string]string{"name": "required"}}
	}
	var w domain.Workspace
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		var err error
		w, err = scanWorkspace(tx.QueryRow(ctx, `
			INSERT INTO workspaces (id, tenant_id, name, description, public, created_by)
			VALUES ($1,$2,$3,$4,$5,$6) RETURNING `+workspaceCols,
			NewID(), op.Tenant, name, description, public, op.Actor.ID))
		if err != nil {
			if isUniqueViolation(err) {
				return &ConflictError{Code: CodeConflict, Message: fmt.Sprintf("workspace name %q already exists in tenant", name)}
			}
			return err
		}
		if public {
			if err := markDirtyAllTenantUsers(ctx, tx, op.Tenant, "workspace.created(public)"); err != nil {
				return err
			}
		}
		return op.emit(ctx, tx, events.EvWorkspaceCreated, workspaceURN(op.Tenant, w.ID),
			map[string]any{"name": w.Name, "public": w.Public})
	})
	return w, err
}

// GetWorkspace applies the visibility rule (RBC-FR-002) unless admin.
func (s *Store) GetWorkspace(ctx context.Context, tenant uuid.UUID, id uuid.UUID, userID string, admin bool) (domain.Workspace, error) {
	var w domain.Workspace
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		w, err = scanWorkspace(tx.QueryRow(ctx, `SELECT `+workspaceCols+` FROM workspaces WHERE id = $1`, id))
		if err != nil {
			return err
		}
		if admin || w.Public {
			return nil
		}
		visible, err := userInLinkedGroup(ctx, tx, id, userID)
		if err != nil {
			return err
		}
		if !visible {
			return ErrNotFound // invisible == nonexistent (MASTER-FR-003)
		}
		return nil
	})
	return w, err
}

func userInLinkedGroup(ctx context.Context, tx pgx.Tx, workspaceID uuid.UUID, userID string) (bool, error) {
	var visible bool
	err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1 FROM workspace_groups wg
			JOIN members m ON m.group_id = wg.group_id
			WHERE wg.workspace_id = $1 AND m.user_id = $2
			  AND (m.expires_at IS NULL OR m.expires_at > now())
		)`, workspaceID, userID).Scan(&visible)
	return visible, err
}

// ArchivedFilter selects archived visibility on listings (RBC-FR-004).
type ArchivedFilter string

const (
	ArchivedExclude ArchivedFilter = ""     // default: exclude archived
	ArchivedOnly    ArchivedFilter = "only" // archived only
	ArchivedWith    ArchivedFilter = "with" // include both
)

// ListWorkspaces returns workspaces visible to the user (RBC-FR-002, BR-12),
// cursor-paginated by uuidv7 id.
func (s *Store) ListWorkspaces(ctx context.Context, tenant uuid.UUID, userID string, admin bool, archived ArchivedFilter, cursor string, limit int) (Page[domain.Workspace], error) {
	limit = ClampLimit(limit)
	var page Page[domain.Workspace]

	where := []string{"true"}
	args := []any{}
	n := 1
	switch archived {
	case ArchivedOnly:
		where = append(where, "archived_at IS NOT NULL")
	case ArchivedWith:
	default:
		where = append(where, "archived_at IS NULL")
	}
	if cursor != "" {
		cid, err := uuid.Parse(cursor)
		if err != nil {
			return page, &ValidationError{Code: CodeValidationFailed, Message: "invalid cursor"}
		}
		where = append(where, fmt.Sprintf("id > $%d", n))
		args = append(args, cid)
		n++
	}
	if !admin {
		where = append(where, fmt.Sprintf(`(public OR EXISTS (
			SELECT 1 FROM workspace_groups wg
			JOIN members m ON m.group_id = wg.group_id
			WHERE wg.workspace_id = workspaces.id AND m.user_id = $%d
			  AND (m.expires_at IS NULL OR m.expires_at > now())))`, n))
		args = append(args, userID)
		n++
	}
	args = append(args, limit+1)
	q := `SELECT ` + workspaceCols + ` FROM workspaces WHERE ` + strings.Join(where, " AND ") +
		fmt.Sprintf(` ORDER BY id LIMIT $%d`, n)

	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			w, err := scanWorkspace(rows)
			if err != nil {
				return err
			}
			page.Data = append(page.Data, w)
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

// UpdateWorkspaceParams: nil pointer = leave unchanged.
type UpdateWorkspaceParams struct {
	Name        *string
	Description *string
	Public      *bool
}

// UpdateWorkspace mutates name/description/public. Archived workspaces reject
// writes (RBC-FR-004). Public flips bulk-dirty the tenant (BR-1).
func (s *Store) UpdateWorkspace(ctx context.Context, op Op, id uuid.UUID, p UpdateWorkspaceParams) (domain.Workspace, error) {
	var w domain.Workspace
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		cur, err := scanWorkspace(tx.QueryRow(ctx, `SELECT `+workspaceCols+` FROM workspaces WHERE id = $1 FOR UPDATE`, id))
		if err != nil {
			return err
		}
		if cur.Archived() {
			return &ConflictError{Code: CodeWorkspaceArchived, Message: "workspace is archived"}
		}
		name, desc, pub := cur.Name, cur.Description, cur.Public
		if p.Name != nil {
			name = strings.TrimSpace(*p.Name)
			if name == "" {
				return &ValidationError{Code: CodeValidationFailed, Message: "name must not be empty", Details: map[string]string{"name": "required"}}
			}
		}
		if p.Description != nil {
			desc = *p.Description
		}
		if p.Public != nil {
			pub = *p.Public
		}
		w, err = scanWorkspace(tx.QueryRow(ctx, `
			UPDATE workspaces SET name=$2, description=$3, public=$4, updated_at=now()
			WHERE id=$1 RETURNING `+workspaceCols, id, name, desc, pub))
		if err != nil {
			if isUniqueViolation(err) {
				return &ConflictError{Code: CodeConflict, Message: fmt.Sprintf("workspace name %q already exists in tenant", name)}
			}
			return err
		}
		publicChanged := cur.Public != w.Public
		if err := markDirtyWorkspaceUsers(ctx, tx, op.Tenant, id, publicChanged || w.Public, "workspace.updated"); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvWorkspaceUpdated, workspaceURN(op.Tenant, id),
			map[string]any{"name": w.Name, "public": w.Public, "public_changed": publicChanged})
	})
	return w, err
}

// ArchiveWorkspace implements RBC-FR-004; idempotent-safe (already archived => conflict).
func (s *Store) ArchiveWorkspace(ctx context.Context, op Op, id uuid.UUID) (domain.Workspace, error) {
	return s.setArchived(ctx, op, id, true)
}

// RestoreWorkspace reverses archive.
func (s *Store) RestoreWorkspace(ctx context.Context, op Op, id uuid.UUID) (domain.Workspace, error) {
	return s.setArchived(ctx, op, id, false)
}

func (s *Store) setArchived(ctx context.Context, op Op, id uuid.UUID, archive bool) (domain.Workspace, error) {
	var w domain.Workspace
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		cur, err := scanWorkspace(tx.QueryRow(ctx, `SELECT `+workspaceCols+` FROM workspaces WHERE id = $1 FOR UPDATE`, id))
		if err != nil {
			return err
		}
		if archive && cur.Archived() {
			return &ConflictError{Code: CodeWorkspaceArchived, Message: "workspace is already archived"}
		}
		if !archive && !cur.Archived() {
			return &ConflictError{Code: CodeConflict, Message: "workspace is not archived"}
		}
		var at *time.Time
		evType := events.EvWorkspaceRestored
		if archive {
			t := nowUTC()
			at = &t
			evType = events.EvWorkspaceArchived
		}
		w, err = scanWorkspace(tx.QueryRow(ctx, `
			UPDATE workspaces SET archived_at=$2, updated_at=now() WHERE id=$1 RETURNING `+workspaceCols, id, at))
		if err != nil {
			return err
		}
		if err := markDirtyWorkspaceUsers(ctx, tx, op.Tenant, id, w.Public, evType); err != nil {
			return err
		}
		return op.emit(ctx, tx, evType, workspaceURN(op.Tenant, id), map[string]any{"name": w.Name})
	})
	return w, err
}

// LinkContentGroup links a content group to a workspace (RBC-FR-012).
// Idempotent: an existing link is a no-op success.
func (s *Store) LinkContentGroup(ctx context.Context, op Op, workspaceID, groupID uuid.UUID) error {
	return s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		w, err := scanWorkspace(tx.QueryRow(ctx, `SELECT `+workspaceCols+` FROM workspaces WHERE id = $1`, workspaceID))
		if err != nil {
			return err
		}
		if w.Archived() {
			return &ConflictError{Code: CodeWorkspaceArchived, Message: "workspace is archived"}
		}
		g, err := s.getGroupTx(ctx, tx, groupID)
		if err != nil {
			return err
		}
		if g.Type != domain.GroupTypeContent {
			return &ValidationError{Code: CodeValidationFailed, Message: "only content groups can be linked to workspaces"}
		}
		tag, err := tx.Exec(ctx, `
			INSERT INTO workspace_groups (id, tenant_id, workspace_id, group_id)
			VALUES ($1,$2,$3,$4) ON CONFLICT (workspace_id, group_id) DO NOTHING`,
			NewID(), op.Tenant, workspaceID, groupID)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return nil // already linked — idempotent
		}
		if err := markDirtyGroupMembers(ctx, tx, op.Tenant, groupID, "workspace.link"); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvWorkspaceUpdated, workspaceURN(op.Tenant, workspaceID),
			map[string]any{"linked_group_id": groupID.String(), "link": "added"})
	})
}

// UnlinkContentGroup removes a workspace link and cascades that workspace's
// grants held by the group (keeps grant integrity, RBC-FR-031).
func (s *Store) UnlinkContentGroup(ctx context.Context, op Op, workspaceID, groupID uuid.UUID) error {
	return s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		w, err := scanWorkspace(tx.QueryRow(ctx, `SELECT `+workspaceCols+` FROM workspaces WHERE id = $1`, workspaceID))
		if err != nil {
			return err
		}
		if w.Archived() {
			return &ConflictError{Code: CodeWorkspaceArchived, Message: "workspace is archived"}
		}
		tag, err := tx.Exec(ctx, `DELETE FROM workspace_groups WHERE workspace_id=$1 AND group_id=$2`, workspaceID, groupID)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return ErrNotFound
		}
		// Grants for this group in this workspace lose their integrity basis.
		if _, err := tx.Exec(ctx, `DELETE FROM content_grants WHERE workspace_id=$1 AND subject_group_id=$2`, workspaceID, groupID); err != nil {
			return err
		}
		if err := markDirtyGroupMembers(ctx, tx, op.Tenant, groupID, "workspace.unlink"); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvWorkspaceUpdated, workspaceURN(op.Tenant, workspaceID),
			map[string]any{"linked_group_id": groupID.String(), "link": "removed"})
	})
}

// ArchivedWorkspaceIDs lists all archived workspaces in a tenant.
func (s *Store) ArchivedWorkspaceIDs(ctx context.Context, tenant uuid.UUID) ([]uuid.UUID, error) {
	var ids []uuid.UUID
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT id FROM workspaces WHERE archived_at IS NOT NULL ORDER BY id`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id uuid.UUID
			if err := rows.Scan(&id); err != nil {
				return err
			}
			ids = append(ids, id)
		}
		return rows.Err()
	})
	return ids, err
}

func workspaceURN(tenant, id uuid.UUID) string {
	return fmt.Sprintf("wr:%s:rbac:workspace/%s", tenant, id)
}
