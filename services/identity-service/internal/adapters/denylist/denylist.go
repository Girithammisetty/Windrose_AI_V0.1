// Package denylist implements the API-key revocation denylist
// (IDN-FR-033: immediate revocation, <=5s propagation to the edge).
//
// Memory is the in-process implementation used by this service and its
// tests. Redis is the multi-replica adapter: it compiles against a minimal
// command interface (no client dependency) and is wired when a Redis client
// is available. TODO(identity): wire go-redis in main and integration-test.
package denylist

import (
	"context"
	"sync"
	"time"
)

// Memory is a concurrency-safe in-memory denylist (domain.Denylist).
type Memory struct {
	mu  sync.RWMutex
	set map[string]bool
}

func NewMemory() *Memory { return &Memory{set: map[string]bool{}} }

func (m *Memory) Revoke(id string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.set[id] = true
}

func (m *Memory) IsRevoked(id string) bool {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.set[id]
}

// RedisCmd is the minimal Redis surface the adapter needs; satisfied by
// go-redis's *redis.Client via a thin shim.
type RedisCmd interface {
	Set(ctx context.Context, key string, value any, ttl time.Duration) error
	Exists(ctx context.Context, key string) (bool, error)
}

// Redis is the distributed denylist (domain.Denylist). Entries carry a TTL of
// key-max-lifetime; the edge polls per request, giving <=5s propagation.
type Redis struct {
	Cmd    RedisCmd
	Prefix string
	TTL    time.Duration
}

func (r *Redis) Revoke(id string) {
	// Best-effort: the DB revoked_at flag remains the source of truth.
	_ = r.Cmd.Set(context.Background(), r.Prefix+id, "1", r.TTL)
}

func (r *Redis) IsRevoked(id string) bool {
	ok, err := r.Cmd.Exists(context.Background(), r.Prefix+id)
	if err != nil {
		return false // fail-open here; DB check still enforces revocation
	}
	return ok
}
