package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/datacern-ai/rbac-service/internal/domain"
	"github.com/datacern-ai/rbac-service/internal/events"
)

const grantCols = `id, tenant_id, workspace_id, resource_urn, subject_type,
	COALESCE(subject_group_id::text, subject_user_id), level, implicit, created_at, updated_at`

func scanGrant(row pgx.Row) (domain.ContentGrant, error) {
	var g domain.ContentGrant
	err := row.Scan(&g.ID, &g.TenantID, &g.WorkspaceID, &g.ResourceURN, &g.SubjectType, &g.SubjectID, &g.Level, &g.Implicit, &g.CreatedAt, &g.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return g, ErrNotFound
	}
	return g, err
}

// CreateGrantParams describes a content grant (RBC-FR-030).
type CreateGrantParams struct {
	WorkspaceID uuid.UUID
	ResourceURN string
	SubjectType domain.SubjectType
	SubjectID   string
	Level       domain.GrantLevel
	Implicit    bool
	// MaxLevel caps the grantable level; editors sharing may only grant
	// viewer (level->verb model, RBC-FR-030). Empty = uncapped.
	MaxLevel domain.GrantLevel
}

// CreateGrant enforces the group-in-workspace integrity rule (RBC-FR-031 —
// the validation V1 shipped commented out): a group grant's group must be
// linked to the grant's workspace, else 422 GROUP_NOT_IN_WORKSPACE.
func (s *Store) CreateGrant(ctx context.Context, op Op, p CreateGrantParams) (domain.ContentGrant, error) {
	if !p.Level.Valid() {
		return domain.ContentGrant{}, &ValidationError{Code: CodeValidationFailed, Message: "level must be viewer|editor|owner"}
	}
	if _, err := domain.ParseURN(p.ResourceURN); err != nil {
		return domain.ContentGrant{}, &ValidationError{Code: CodeValidationFailed, Message: err.Error()}
	}
	if p.MaxLevel != "" && !domain.LevelAtLeast(p.MaxLevel, p.Level) {
		return domain.ContentGrant{}, &ValidationError{Code: CodeValidationFailed,
			Message: fmt.Sprintf("caller may grant at most %s", p.MaxLevel)}
	}
	var g domain.ContentGrant
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		w, err := scanWorkspace(tx.QueryRow(ctx, `SELECT `+workspaceCols+` FROM workspaces WHERE id = $1`, p.WorkspaceID))
		if err != nil {
			return err
		}
		if w.Archived() {
			return &ConflictError{Code: CodeWorkspaceArchived, Message: "workspace is archived"}
		}

		var groupID *uuid.UUID
		var userID *string
		switch p.SubjectType {
		case domain.SubjectGroup:
			gid, err := uuid.Parse(p.SubjectID)
			if err != nil {
				return &ValidationError{Code: CodeValidationFailed, Message: "subject.id must be a group uuid"}
			}
			grp, err := s.getGroupTx(ctx, tx, gid)
			if err != nil {
				return err
			}
			if grp.Type != domain.GroupTypeContent {
				return &ValidationError{Code: CodeValidationFailed, Message: "grants target content groups only"}
			}
			var linked bool
			if err := tx.QueryRow(ctx, `
				SELECT EXISTS (SELECT 1 FROM workspace_groups WHERE workspace_id=$1 AND group_id=$2)`,
				p.WorkspaceID, gid).Scan(&linked); err != nil {
				return err
			}
			if !linked {
				return &ValidationError{Code: CodeGroupNotInWorkspace,
					Message: "group is not linked to the grant's workspace"}
			}
			groupID = &gid
		case domain.SubjectUser:
			if p.SubjectID == "" {
				return &ValidationError{Code: CodeValidationFailed, Message: "subject.id is required"}
			}
			userID = &p.SubjectID
		default:
			return &ValidationError{Code: CodeValidationFailed, Message: "subject.type must be user|group"}
		}

		g, err = scanGrant(tx.QueryRow(ctx, `
			INSERT INTO content_grants (id, tenant_id, workspace_id, resource_urn, subject_type, subject_group_id, subject_user_id, level, implicit)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING `+grantCols,
			NewID(), op.Tenant, p.WorkspaceID, p.ResourceURN, p.SubjectType, groupID, userID, p.Level, p.Implicit))
		if err != nil {
			if isUniqueViolation(err) {
				return &ConflictError{Code: CodeConflict, Message: "grant already exists for this subject and resource"}
			}
			return err
		}
		if groupID != nil {
			if err := markDirtyGroupMembers(ctx, tx, op.Tenant, *groupID, "grant.created"); err != nil {
				return err
			}
		} else {
			if err := markDirtyUsers(ctx, tx, op.Tenant, []string{*userID}, "grant.created"); err != nil {
				return err
			}
		}
		return op.emit(ctx, tx, events.EvGrantCreated, p.ResourceURN, map[string]any{
			"grant_id": g.ID.String(), "workspace_id": p.WorkspaceID.String(),
			"subject_type": string(p.SubjectType), "subject_id": p.SubjectID,
			"level": string(p.Level), "implicit": p.Implicit,
		})
	})
	return g, err
}

// CreateImplicitOwnerGrant materializes the creator's implicit owner grant on
// a *.created event (RBC-FR-032). Idempotent: an existing grant wins.
func (s *Store) CreateImplicitOwnerGrant(ctx context.Context, op Op, workspaceID uuid.UUID, resourceURN, creatorUserID string) error {
	_, err := s.CreateGrant(ctx, op, CreateGrantParams{
		WorkspaceID: workspaceID,
		ResourceURN: resourceURN,
		SubjectType: domain.SubjectUser,
		SubjectID:   creatorUserID,
		Level:       domain.LevelOwner,
		Implicit:    true,
	})
	var ce *ConflictError
	if errors.As(err, &ce) && ce.Code == CodeConflict {
		return nil
	}
	return err
}

// UpsertAssignmentGrant materializes the case-assignment implicit EDITOR grant
// (the missing link that made every approved case.apply_disposition execution
// fail tool-plane's obo-grant gate): the assignee of a work item holds an
// implicit editor grant on its URN for as long as they are assigned. In one
// transaction it revokes any OTHER user's implicit editor grant on the same
// resource (reassignment revokes the previous assignee) and upserts the new
// assignee's. Explicit human shares (implicit=false) and the creator's
// implicit OWNER grant (RBC-FR-032) are never touched. Returns every user
// whose projection changed so the caller can mark them dirty. Idempotent.
func (s *Store) UpsertAssignmentGrant(ctx context.Context, op Op, workspaceID uuid.UUID, resourceURN, assigneeID string) ([]string, error) {
	if _, err := domain.ParseURN(resourceURN); err != nil {
		return nil, &ValidationError{Code: CodeValidationFailed, Message: err.Error()}
	}
	affected := []string{assigneeID}
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			DELETE FROM content_grants
			WHERE resource_urn = $1 AND implicit AND subject_type = 'user'
			  AND level = 'editor' AND subject_user_id <> $2
			RETURNING subject_user_id`, resourceURN, assigneeID)
		if err != nil {
			return err
		}
		for rows.Next() {
			var u string
			if err := rows.Scan(&u); err != nil {
				rows.Close()
				return err
			}
			affected = append(affected, u)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO content_grants (id, tenant_id, workspace_id, resource_urn, subject_type, subject_user_id, level, implicit)
			VALUES ($1,$2,$3,$4,'user',$5,'editor',true)
			ON CONFLICT (workspace_id, resource_urn, subject_type, COALESCE(subject_group_id::text, subject_user_id))
			DO UPDATE SET level = 'editor', implicit = true, updated_at = now()`,
			NewID(), op.Tenant, workspaceID, resourceURN, assigneeID); err != nil {
			return err
		}
		if err := markDirtyUsers(ctx, tx, op.Tenant, affected, "grant.assignment"); err != nil {
			return err
		}
		return op.emit(ctx, tx, events.EvGrantCreated, resourceURN, map[string]any{
			"workspace_id": workspaceID.String(), "subject_type": "user",
			"subject_id": assigneeID, "level": "editor", "implicit": true,
		})
	})
	return affected, err
}

// RemoveAssignmentGrant revokes the implicit editor assignment grant(s) on a
// resource when a case is unassigned. assigneeID narrows to one user; empty
// removes every implicit editor user grant on the URN. Returns affected users.
func (s *Store) RemoveAssignmentGrant(ctx context.Context, op Op, resourceURN, assigneeID string) ([]string, error) {
	var affected []string
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		q := `DELETE FROM content_grants
			WHERE resource_urn = $1 AND implicit AND subject_type = 'user' AND level = 'editor'`
		args := []any{resourceURN}
		if assigneeID != "" {
			q += ` AND subject_user_id = $2`
			args = append(args, assigneeID)
		}
		rows, err := tx.Query(ctx, q+` RETURNING subject_user_id`, args...)
		if err != nil {
			return err
		}
		for rows.Next() {
			var u string
			if err := rows.Scan(&u); err != nil {
				rows.Close()
				return err
			}
			affected = append(affected, u)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}
		if len(affected) == 0 {
			return nil
		}
		return markDirtyUsers(ctx, tx, op.Tenant, affected, "grant.assignment.removed")
	})
	return affected, err
}

// DeleteGrant enforces last-owner protection (RBC-FR-015): removing the last
// owner-level grantee of a resource is rejected 409 LAST_ADMIN unless a
// super-admin override with a reason is supplied (audited).
func (s *Store) DeleteGrant(ctx context.Context, op Op, id uuid.UUID, overrideReason string, superAdmin bool) error {
	return s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		g, err := scanGrant(tx.QueryRow(ctx, `SELECT `+grantCols+` FROM content_grants WHERE id = $1 FOR UPDATE`, id))
		if err != nil {
			return err
		}
		if g.Level == domain.LevelOwner {
			var owners int
			if err := tx.QueryRow(ctx, `
				SELECT count(*) FROM content_grants WHERE resource_urn = $1 AND level = 'owner'`,
				g.ResourceURN).Scan(&owners); err != nil {
				return err
			}
			if owners <= 1 {
				if !(superAdmin && overrideReason != "") {
					return &ConflictError{Code: CodeLastAdmin, Message: "cannot remove the last owner of a resource"}
				}
				if err := op.emit(ctx, tx, events.EvLastAdminOverridden, g.ResourceURN,
					map[string]any{"grant_id": id.String(), "reason": overrideReason}); err != nil {
					return err
				}
			}
		}
		if _, err := tx.Exec(ctx, `DELETE FROM content_grants WHERE id = $1`, id); err != nil {
			return err
		}
		if g.SubjectType == domain.SubjectGroup {
			gid, _ := uuid.Parse(g.SubjectID)
			if err := markDirtyGroupMembers(ctx, tx, op.Tenant, gid, "grant.deleted"); err != nil {
				return err
			}
		} else {
			if err := markDirtyUsers(ctx, tx, op.Tenant, []string{g.SubjectID}, "grant.deleted"); err != nil {
				return err
			}
		}
		return op.emit(ctx, tx, events.EvGrantDeleted, g.ResourceURN, map[string]any{
			"grant_id": id.String(), "subject_type": string(g.SubjectType),
			"subject_id": g.SubjectID, "level": string(g.Level),
		})
	})
}

// EffectiveAccessEntry is one row of GET /grants?resource_urn= (RBC-FR-034):
// direct + via-group + implicit access with provenance.
type EffectiveAccessEntry struct {
	SubjectType domain.SubjectType `json:"subject_type"`
	SubjectID   string             `json:"subject_id"`
	Level       domain.GrantLevel  `json:"level"`
	Provenance  string             `json:"provenance"` // direct | implicit_creator | via_group
	Via         string             `json:"via,omitempty"`
	GrantID     uuid.UUID          `json:"grant_id"`
	WorkspaceID uuid.UUID          `json:"workspace_id"`
}

// EffectiveAccess lists who can access a resource and why.
func (s *Store) EffectiveAccess(ctx context.Context, tenant uuid.UUID, resourceURN string) ([]EffectiveAccessEntry, error) {
	var out []EffectiveAccessEntry
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT g.id, g.workspace_id, g.subject_type,
			       COALESCE(g.subject_group_id::text, g.subject_user_id), g.level, g.implicit,
			       COALESCE(gr.name, '')
			FROM content_grants g
			LEFT JOIN groups gr ON gr.id = g.subject_group_id
			WHERE g.resource_urn = $1 ORDER BY g.id`, resourceURN)
		if err != nil {
			return err
		}
		defer rows.Close()
		type grantRow struct {
			entry     EffectiveAccessEntry
			groupName string
		}
		var groupRows []grantRow
		for rows.Next() {
			var e EffectiveAccessEntry
			var implicit bool
			var groupName string
			if err := rows.Scan(&e.GrantID, &e.WorkspaceID, &e.SubjectType, &e.SubjectID, &e.Level, &implicit, &groupName); err != nil {
				return err
			}
			switch {
			case implicit:
				e.Provenance = "implicit_creator"
			default:
				e.Provenance = "direct"
			}
			out = append(out, e)
			if e.SubjectType == domain.SubjectGroup {
				groupRows = append(groupRows, grantRow{entry: e, groupName: groupName})
			}
		}
		if err := rows.Err(); err != nil {
			return err
		}
		// Expand group grants to member users with via_group provenance.
		for _, gr := range groupRows {
			mrows, err := tx.Query(ctx, `
				SELECT user_id FROM members WHERE group_id = $1
				AND (expires_at IS NULL OR expires_at > now()) ORDER BY user_id`, gr.entry.SubjectID)
			if err != nil {
				return err
			}
			for mrows.Next() {
				var uid string
				if err := mrows.Scan(&uid); err != nil {
					mrows.Close()
					return err
				}
				out = append(out, EffectiveAccessEntry{
					SubjectType: domain.SubjectUser, SubjectID: uid, Level: gr.entry.Level,
					Provenance: "via_group", Via: gr.groupName,
					GrantID: gr.entry.GrantID, WorkspaceID: gr.entry.WorkspaceID,
				})
			}
			if err := mrows.Err(); err != nil {
				mrows.Close()
				return err
			}
			mrows.Close()
		}
		return nil
	})
	return out, err
}

// GetGrant fetches one grant.
func (s *Store) GetGrant(ctx context.Context, tenant uuid.UUID, id uuid.UUID) (domain.ContentGrant, error) {
	var g domain.ContentGrant
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		g, err = scanGrant(tx.QueryRow(ctx, `SELECT `+grantCols+` FROM content_grants WHERE id = $1`, id))
		return err
	})
	return g, err
}

// OrphanGrantCount counts group grants whose group link no longer exists —
// the nightly consistency sweep's detection query (RBC-FR-031, AC-10). With
// FK cascades in place this must always return 0.
func (s *Store) OrphanGrantCount(ctx context.Context, tenant uuid.UUID) (int, error) {
	var n int
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT count(*) FROM content_grants g
			WHERE g.subject_group_id IS NOT NULL
			AND NOT EXISTS (
				SELECT 1 FROM workspace_groups wg
				WHERE wg.group_id = g.subject_group_id AND wg.workspace_id = g.workspace_id
			)`).Scan(&n)
	})
	return n, err
}

// SweepOrphanGrants repairs integrity violations (nightly sweep: deletes
// group grants whose workspace link disappeared) and returns the repair count.
func (s *Store) SweepOrphanGrants(ctx context.Context, op Op) (int64, error) {
	var n int64
	err := s.WithTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
			DELETE FROM content_grants g
			WHERE g.subject_group_id IS NOT NULL
			AND NOT EXISTS (
				SELECT 1 FROM workspace_groups wg
				WHERE wg.group_id = g.subject_group_id AND wg.workspace_id = g.workspace_id
			)`)
		if err != nil {
			return err
		}
		n = tag.RowsAffected()
		if n > 0 {
			return markDirtyAllTenantUsers(ctx, tx, op.Tenant, "grant.sweep")
		}
		return nil
	})
	return n, err
}
