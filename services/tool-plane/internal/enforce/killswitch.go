// Package enforce is the mcp-gateway per-call enforcement pipeline (BRD §3):
// authN → kill/enablement gate → OPA → rate limit → schema validation → tier gate
// → invoke → audit, strictly ordered and deny-by-default (BR-1). Its components
// are REAL: kill state in Redis with pub/sub fan-out, token buckets in Redis, the
// OPA sidecar client, JSON-Schema validation, and a real HTTP backend client.
package enforce

import (
	"context"
	"log/slog"
	"sync"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/redisx"
	"github.com/windrose-ai/tool-plane/internal/domain"
)

// killChannel is the Redis pub/sub channel announcing kill-state changes.
const killChannel = "tp:kill"

// killSetKey is the Redis set of active kill tuples (authoritative snapshot).
const killSetKey = "tp:kill:set"

// KillLoader loads active kills from the durable store (Postgres-backed).
type KillLoader interface {
	ActiveKills(ctx context.Context) ([]*domain.KillSwitch, error)
}

// KillRegistry maintains a local snapshot of active kill tuples, refreshed from
// Redis on every pub/sub notification so a kill propagates to 100% of gateway
// replicas in ≤5s (TPL-FR-052/AC-5). Kill state is durable in Postgres and
// mirrored to a Redis set; the local map is a hot-path cache.
type KillRegistry struct {
	r   *redisx.Client
	mu  sync.RWMutex
	set map[string]bool
}

// NewKillRegistry builds a registry over a Redis client.
func NewKillRegistry(r *redisx.Client) *KillRegistry {
	return &KillRegistry{r: r, set: map[string]bool{}}
}

// killMembers returns the Redis set members for a kill switch (one tuple).
func killMembers(k *domain.KillSwitch) []string {
	switch k.Scope {
	case domain.KillScopeTool:
		return []string{"tool|" + k.ToolID}
	case domain.KillScopeToolVersion:
		return []string{"toolver|" + k.ToolID + "|" + k.Version}
	case domain.KillScopeToolTenant:
		if k.TenantID != nil {
			return []string{"tooltenant|" + k.ToolID + "|" + k.TenantID.String()}
		}
	}
	return nil
}

// SyncFromStore reloads the durable kill set into Redis and the local cache.
// Called at boot so restarts recover kill state (TPL-FR-052).
func (kr *KillRegistry) SyncFromStore(ctx context.Context, l KillLoader) error {
	kills, err := l.ActiveKills(ctx)
	if err != nil {
		return err
	}
	members := map[string]bool{}
	pipe := kr.r.R.TxPipeline()
	pipe.Del(ctx, killSetKey)
	for _, k := range kills {
		for _, m := range killMembers(k) {
			members[m] = true
			pipe.SAdd(ctx, killSetKey, m)
		}
	}
	if _, err := pipe.Exec(ctx); err != nil {
		return err
	}
	kr.mu.Lock()
	kr.set = members
	kr.mu.Unlock()
	return nil
}

// refresh reloads the local cache from the Redis set.
func (kr *KillRegistry) refresh(ctx context.Context) error {
	members, err := kr.r.R.SMembers(ctx, killSetKey).Result()
	if err != nil {
		return err
	}
	set := make(map[string]bool, len(members))
	for _, m := range members {
		set[m] = true
	}
	kr.mu.Lock()
	kr.set = set
	kr.mu.Unlock()
	return nil
}

// Announce adds/removes a kill tuple in Redis and publishes the change so all
// replicas refresh (≤5s SLO — pub/sub is sub-second). active=false removes it.
func (kr *KillRegistry) Announce(ctx context.Context, k *domain.KillSwitch, active bool) error {
	for _, m := range killMembers(k) {
		if active {
			if err := kr.r.R.SAdd(ctx, killSetKey, m).Err(); err != nil {
				return err
			}
		} else {
			if err := kr.r.R.SRem(ctx, killSetKey, m).Err(); err != nil {
				return err
			}
		}
	}
	// Refresh our own cache immediately; notify peers.
	if err := kr.refresh(ctx); err != nil {
		return err
	}
	return kr.r.Publish(ctx, killChannel, "changed")
}

// Watch subscribes to kill notifications and refreshes the local cache on each
// one, until ctx is cancelled (run as a goroutine per gateway replica).
func (kr *KillRegistry) Watch(ctx context.Context) {
	sub := kr.r.R.Subscribe(ctx, killChannel)
	defer func() { _ = sub.Close() }()
	// Prime the cache once.
	if err := kr.refresh(ctx); err != nil {
		slog.Warn("kill registry initial refresh failed", "err", err)
	}
	ch := sub.Channel()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ch:
			if err := kr.refresh(ctx); err != nil {
				slog.Warn("kill registry refresh failed", "err", err)
			}
		}
	}
}

// IsKilled reports whether a call to (tenant, tool, version) is killed by any
// scope: whole tool, this tool-version, or this tool for this tenant (BR-9 is
// enforced by calling this at step 2 and again pre-dispatch).
func (kr *KillRegistry) IsKilled(tenant uuid.UUID, toolID, version string) (bool, string) {
	kr.mu.RLock()
	defer kr.mu.RUnlock()
	if kr.set["tool|"+toolID] {
		return true, "tool"
	}
	if kr.set["toolver|"+toolID+"|"+version] {
		return true, "tool_version"
	}
	if kr.set["tooltenant|"+toolID+"|"+tenant.String()] {
		return true, "tool_tenant"
	}
	return false, ""
}
