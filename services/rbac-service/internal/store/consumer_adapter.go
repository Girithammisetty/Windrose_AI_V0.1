package store

import (
	"context"

	"github.com/google/uuid"

	"github.com/datacern-ai/rbac-service/internal/events"
)

// ConsumerAdapter implements events.ConsumerStore over the Store.
// DropProjection is wired to the Redis writer's DropUser in the server.
type ConsumerAdapter struct {
	S              *Store
	DropProjection func(ctx context.Context, tenant, user string) error
}

var _ interface {
	SeedTenantFromEvent(ctx context.Context, tenant uuid.UUID, actorID, traceID string) error
} = (*ConsumerAdapter)(nil)

func (a *ConsumerAdapter) SeedTenantFromEvent(ctx context.Context, tenant uuid.UUID, actorID, traceID string) error {
	return a.S.SeedTenant(ctx, Op{
		Tenant:  tenant,
		Actor:   events.Actor{Type: "service", ID: actorID},
		TraceID: traceID,
	})
}

func (a *ConsumerAdapter) CreateImplicitOwnerGrantFromEvent(ctx context.Context, tenant uuid.UUID, workspaceID uuid.UUID, resourceURN, creatorUserID, traceID string) error {
	return a.S.CreateImplicitOwnerGrant(ctx, Op{
		Tenant:  tenant,
		Actor:   events.Actor{Type: "user", ID: creatorUserID},
		TraceID: traceID,
	}, workspaceID, resourceURN, creatorUserID)
}

func (a *ConsumerAdapter) MarkDirty(ctx context.Context, tenant uuid.UUID, users []string, reason string) error {
	return a.S.MarkDirty(ctx, tenant, users, reason)
}

func (a *ConsumerAdapter) UpsertAssignmentGrantFromEvent(ctx context.Context, tenant uuid.UUID, workspaceID uuid.UUID, resourceURN, assigneeID, traceID string) ([]string, error) {
	return a.S.UpsertAssignmentGrant(ctx, Op{
		Tenant:  tenant,
		Actor:   events.Actor{Type: "service", ID: "case-service"},
		TraceID: traceID,
	}, workspaceID, resourceURN, assigneeID)
}

func (a *ConsumerAdapter) RemoveAssignmentGrantFromEvent(ctx context.Context, tenant uuid.UUID, resourceURN, assigneeID, traceID string) ([]string, error) {
	return a.S.RemoveAssignmentGrant(ctx, Op{
		Tenant:  tenant,
		Actor:   events.Actor{Type: "service", ID: "case-service"},
		TraceID: traceID,
	}, resourceURN, assigneeID)
}

func (a *ConsumerAdapter) GrantOwnerAdminFromEvent(ctx context.Context, tenant uuid.UUID, userID, actorID, traceID string) error {
	_, err := a.S.GrantOwnerAdmin(ctx, Op{
		Tenant:  tenant,
		Actor:   events.Actor{Type: "service", ID: actorID},
		TraceID: traceID,
	}, userID)
	return err
}

func (a *ConsumerAdapter) AssignUserToGroupsFromEvent(ctx context.Context, tenant uuid.UUID, userID string, groups []string, actorID, traceID string) error {
	return a.S.AssignUserToGroups(ctx, Op{
		Tenant:  tenant,
		Actor:   events.Actor{Type: "service", ID: actorID},
		TraceID: traceID,
	}, userID, groups)
}

func (a *ConsumerAdapter) RemoveUserProjection(ctx context.Context, tenant uuid.UUID, userID string) error {
	if a.DropProjection == nil {
		return nil
	}
	return a.DropProjection(ctx, tenant.String(), userID)
}
