package store

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/events"
)

// Op carries the mutation context: tenant, actor attribution and trace id.
// Every mutating store method takes one so the outbox envelope and the dirty
// queue are written atomically with the mutation.
type Op struct {
	Tenant   uuid.UUID
	Actor    events.Actor
	ViaAgent *events.ViaAgent
	TraceID  string
}

// emit writes an event to the transactional outbox inside tx.
func (op Op) emit(ctx context.Context, tx pgx.Tx, eventType, resourceURN string, payload map[string]any) error {
	env := events.NewEnvelope(eventType, op.Tenant, op.Actor, resourceURN, op.TraceID, payload)
	env.ViaAgent = op.ViaAgent
	return InsertOutbox(ctx, tx, env)
}

// ---- Dirty marking (RBC-FR-042) --------------------------------------------
// All markDirty* helpers run inside the mutation transaction, giving the
// transactional-outbox guarantee: either the mutation, its event and its
// recompute marker all commit, or none do.

// markDirtyUsers marks explicit users dirty.
func markDirtyUsers(ctx context.Context, tx pgx.Tx, tenant uuid.UUID, users []string, reason string) error {
	if len(users) == 0 {
		return nil
	}
	_, err := tx.Exec(ctx, `
		INSERT INTO projection_dirty (tenant_id, user_id, reason)
		SELECT $1::uuid, u, $2 FROM unnest($3::text[]) AS u`, tenant, reason, users)
	return err
}

// markDirtyGroupMembers marks every member of a group dirty.
func markDirtyGroupMembers(ctx context.Context, tx pgx.Tx, tenant uuid.UUID, groupID uuid.UUID, reason string) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO projection_dirty (tenant_id, user_id, reason)
		SELECT $1::uuid, m.user_id, $2 FROM members m WHERE m.group_id = $3`, tenant, reason, groupID)
	return err
}

// markDirtyRoleUsers marks every member of every group bound to a role dirty.
func markDirtyRoleUsers(ctx context.Context, tx pgx.Tx, tenant uuid.UUID, roleID uuid.UUID, reason string) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO projection_dirty (tenant_id, user_id, reason)
		SELECT DISTINCT $1::uuid, m.user_id, $2
		FROM group_roles gr JOIN members m ON m.group_id = gr.group_id
		WHERE gr.role_id = $3`, tenant, reason, roleID)
	return err
}

// markDirtyAllTenantUsers marks every user known to rbac in the tenant dirty
// (BR-1: public-flag flips affect all tenant users). "Known" = any group
// member or direct user grantee; users with no rbac rows have empty
// projections anyway and self-heal via TTL/fallback.
func markDirtyAllTenantUsers(ctx context.Context, tx pgx.Tx, tenant uuid.UUID, reason string) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO projection_dirty (tenant_id, user_id, reason)
		SELECT DISTINCT $1::uuid, u.user_id, $2 FROM (
			SELECT user_id FROM members
			UNION
			SELECT subject_user_id FROM content_grants WHERE subject_user_id IS NOT NULL
		) u`, tenant, reason)
	return err
}

// markDirtyWorkspaceUsers marks users affected by a workspace change: members
// of linked content groups, plus everyone when the workspace is public.
func markDirtyWorkspaceUsers(ctx context.Context, tx pgx.Tx, tenant uuid.UUID, workspaceID uuid.UUID, wasOrIsPublic bool, reason string) error {
	if wasOrIsPublic {
		return markDirtyAllTenantUsers(ctx, tx, tenant, reason)
	}
	_, err := tx.Exec(ctx, `
		INSERT INTO projection_dirty (tenant_id, user_id, reason)
		SELECT DISTINCT $1::uuid, m.user_id, $2
		FROM workspace_groups wg JOIN members m ON m.group_id = wg.group_id
		WHERE wg.workspace_id = $3`, tenant, reason, workspaceID)
	return err
}
