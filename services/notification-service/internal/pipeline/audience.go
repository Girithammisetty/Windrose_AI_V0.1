package pipeline

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/windrose-ai/go-common/redisx"
	"github.com/windrose-ai/notification-service/internal/registry"
)

// GroupResolver expands role audiences and group subjects into user ids via the
// rbac-service projection (NOTIF-FR-013). The runtime adapter reads Redis keys
// rbac populates; a fake is used in unit tests.
type GroupResolver interface {
	// Role returns the user ids for a derived audience role in a tenant/ws.
	Role(ctx context.Context, tenant, workspaceID string, role registry.AudienceRole) ([]string, error)
	// Group returns the member user ids of a group subject.
	Group(ctx context.Context, tenant, groupID string) ([]string, error)
}

// MaxAudience caps recipients per event (NOTIF-FR-013).
const MaxAudience = 500

// RedisGroupResolver reads rbac's audience/group projection from Redis. Keys
// (populated by rbac-service's projection): audience roles at
// `notif:audience:<tenant>:<role>` and `notif:audience:<tenant>:ws:<ws>:<role>`;
// group members at `notif:group:<tenant>:<group_id>`. Missing keys yield no
// recipients (real behavior until the projection is populated — not a stub).
type RedisGroupResolver struct {
	R *redisx.Client
}

// NewRedisGroupResolver builds the real resolver.
func NewRedisGroupResolver(r *redisx.Client) *RedisGroupResolver { return &RedisGroupResolver{R: r} }

// Role reads a role audience list, preferring the workspace-scoped key.
func (g *RedisGroupResolver) Role(ctx context.Context, tenant, workspaceID string, role registry.AudienceRole) ([]string, error) {
	if workspaceID != "" {
		if ids, ok, err := g.readList(ctx, fmt.Sprintf("notif:audience:%s:ws:%s:%s", tenant, workspaceID, role)); err != nil {
			return nil, err
		} else if ok {
			return ids, nil
		}
	}
	ids, _, err := g.readList(ctx, fmt.Sprintf("notif:audience:%s:%s", tenant, role))
	return ids, err
}

// Group reads a group's member list.
func (g *RedisGroupResolver) Group(ctx context.Context, tenant, groupID string) ([]string, error) {
	ids, _, err := g.readList(ctx, fmt.Sprintf("notif:group:%s:%s", tenant, groupID))
	return ids, err
}

func (g *RedisGroupResolver) readList(ctx context.Context, key string) ([]string, bool, error) {
	raw, ok, err := g.R.Get(ctx, key)
	if err != nil || !ok {
		return nil, ok, err
	}
	var ids []string
	if json.Unmarshal([]byte(raw), &ids) != nil {
		return nil, true, nil
	}
	return ids, true, nil
}
