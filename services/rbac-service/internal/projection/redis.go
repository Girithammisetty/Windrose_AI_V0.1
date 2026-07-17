package projection

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// DefaultTTL implements RBC-FR-047: projection entries self-heal via a 24h TTL.
const DefaultTTL = 24 * time.Hour

// RefreshWindow: entries read with less than this TTL remaining trigger a
// re-warm (refresh-on-read, RBC-FR-047).
const RefreshWindow = time.Hour

// casSet writes ARGV[1] to KEYS[1] with TTL ARGV[3] only when the existing
// value's "v" is older than ARGV[2] — versioned last-writer-wins (RBC-FR-048).
var casSet = redis.NewScript(`
local existing = redis.call('GET', KEYS[1])
if existing then
  local ok, cur = pcall(cjson.decode, existing)
  if ok and cur and cur.v and tonumber(cur.v) >= tonumber(ARGV[2]) then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[3]))
return 1
`)

// NOTE: subsidiary keys (ws:{id}, res:{hash}) are removed by writing a
// version-carrying TOMBSTONE via casSet, never a raw DEL. A raw delete would
// leave no value for a subsequent stale writer's casSet to compare against, so
// an older snapshot could recreate a key a newer snapshot had removed —
// resurrecting a revoked grant or workspace assignment for up to the TTL. The
// tombstone keeps the version present, so casSet blocks the stale recreate
// (existing.v >= new.v) exactly as it does for in-place overwrites (RBC-FR-048).

type versioned struct {
	V          int64  `json:"v"`
	ComputedAt string `json:"computed_at"`
}

type actionsValue struct {
	versioned
	Actions []string `json:"actions"`
}

type wsValue struct {
	versioned
	Actions  []string `json:"actions"`
	Archived bool     `json:"archived"`
	Deleted  bool     `json:"deleted,omitempty"` // tombstone marker
}

type resValue struct {
	versioned
	ResourceEntry
	Deleted bool `json:"deleted,omitempty"` // tombstone marker
}

// tombstoneValue records a subsidiary key's removal at a version, so a stale
// writer cannot recreate it (see the casSet note above).
type tombstoneValue struct {
	versioned
	Deleted bool `json:"deleted"`
}

type flagsValue struct {
	versioned
	Admin   bool     `json:"admin"`
	WsAdmin []string `json:"ws_admin"`
	Roles   []string `json:"roles,omitempty"`
}

type indexValue struct {
	versioned
	Keys []string `json:"keys"`
}

type archivedWsValue struct {
	versioned
	Workspaces []string `json:"ws"`
}

type catalogValue struct {
	versioned
	Actions map[string]bool `json:"actions"` // action -> workspace_scoped
}

type tenantMetaValue struct {
	AutonomousEnabled bool `json:"autonomous_enabled"`
}

// RedisWriter materializes Flat projections into Redis and publishes
// perm.invalidate notifications (RBC-FR-040/042).
type RedisWriter struct {
	rdb redis.UniversalClient
	ttl time.Duration
}

func NewRedisWriter(rdb redis.UniversalClient, ttl time.Duration) *RedisWriter {
	if ttl <= 0 {
		ttl = DefaultTTL
	}
	return &RedisWriter{rdb: rdb, ttl: ttl}
}

// WriteUser writes every key for one user's Flat, then garbage-collects
// subsidiary keys (ws/res) that no longer apply, tracked via the index key.
// All writes are version-guarded, so concurrent recomputes converge on the
// newest snapshot (last-writer-wins).
//
// Besides the granular perm:* scheme (Go services), the SAME recompute
// materializes the pre-assembled Python single-key scheme (authz:proj:*, see
// pyprojection.go) so a role grant reaches every consumer through one path.
// The py keys join the subsidiary index, so they are tombstone-GC'd on
// revocation and dropped on user deletion exactly like ws/res keys.
func (w *RedisWriter) WriteUser(ctx context.Context, f Flat) error {
	tenant, user := f.TenantID.String(), f.UserID
	ver := versioned{V: f.Version, ComputedAt: f.ComputedAt.UTC().Format(time.RFC3339Nano)}
	ttlSec := int64(w.ttl / time.Second)

	pairs := map[string]any{}
	pairs[KeyActions(tenant, user)] = actionsValue{versioned: ver, Actions: emptyIfNil(f.TenantActions)}

	subsidiary := make([]string, 0, len(f.WorkspaceActions)+len(f.Resources))
	for wsID, entry := range f.WorkspaceActions {
		k := KeyWorkspace(tenant, user, wsID.String())
		pairs[k] = wsValue{versioned: ver, Actions: emptyIfNil(entry.Actions), Archived: entry.Archived}
		subsidiary = append(subsidiary, k)
	}
	for h, entry := range f.Resources {
		k := KeyResource(tenant, user, h)
		pairs[k] = resValue{versioned: ver, ResourceEntry: entry}
		subsidiary = append(subsidiary, k)
	}
	// Python single-key projection (authz:proj:*). The tenant autonomous flag
	// is mirrored from the same perm:{tenant}:meta key the Go decision path
	// reads, so both schemes stay in lockstep.
	for k, v := range BuildPyProjection(f, w.tenantAutonomous(ctx, tenant)) {
		pairs[k] = v
		subsidiary = append(subsidiary, k)
	}
	wsAdmin := make([]string, 0, len(f.Flags.WsAdmin))
	for _, id := range f.Flags.WsAdmin {
		wsAdmin = append(wsAdmin, id.String())
	}
	pairs[KeyFlags(tenant, user)] = flagsValue{versioned: ver, Admin: f.Flags.Admin, WsAdmin: wsAdmin, Roles: f.Flags.Roles}

	// Read the previous index to find keys to GC.
	oldKeys, err := w.readIndex(ctx, tenant, user)
	if err != nil {
		return err
	}

	for k, v := range pairs {
		if err := w.casWrite(ctx, k, v, f.Version, ttlSec); err != nil {
			return err
		}
	}

	newSet := map[string]bool{}
	for _, k := range subsidiary {
		newSet[k] = true
	}
	tomb := tombstoneValue{versioned: ver, Deleted: true}
	for _, k := range oldKeys {
		if !newSet[k] {
			// Version-guarded tombstone (not DEL) so a stale writer cannot
			// resurrect a subsidiary key this snapshot removed.
			if err := w.casWrite(ctx, k, tomb, f.Version, ttlSec); err != nil {
				return fmt.Errorf("projection gc %s: %w", k, err)
			}
		}
	}

	return w.casWrite(ctx, KeyIndex(tenant, user), indexValue{versioned: ver, Keys: subsidiary}, f.Version, ttlSec)
}

// tenantAutonomous reads the tenant's autonomous-agent enablement flag from
// perm:{tenant}:meta (the same key RedisReader.AutonomousEnabled consults).
// Absent/corrupt reads as false — fail closed.
func (w *RedisWriter) tenantAutonomous(ctx context.Context, tenant string) bool {
	raw, err := w.rdb.Get(ctx, KeyTenantMeta(tenant)).Result()
	if err != nil {
		return false
	}
	var v tenantMetaValue
	if json.Unmarshal([]byte(raw), &v) != nil {
		return false
	}
	return v.AutonomousEnabled
}

// DropUser removes all projection keys for a user (user.deleted handling).
func (w *RedisWriter) DropUser(ctx context.Context, tenant, user string) error {
	keys, err := w.readIndex(ctx, tenant, user)
	if err != nil {
		return err
	}
	keys = append(keys, KeyActions(tenant, user), KeyFlags(tenant, user), KeyIndex(tenant, user))
	return w.rdb.Del(ctx, keys...).Err()
}

// WriteArchivedWorkspaces maintains the tenant-level archived-workspace set
// consulted by the admin path (BR-7: admin does not bypass the archived-write
// block).
func (w *RedisWriter) WriteArchivedWorkspaces(ctx context.Context, tenant string, wsIDs []string, version int64) error {
	v := archivedWsValue{
		versioned:  versioned{V: version, ComputedAt: time.Now().UTC().Format(time.RFC3339Nano)},
		Workspaces: emptyIfNil(wsIDs),
	}
	return w.casWrite(ctx, KeyArchivedWs(tenant), v, version, int64(w.ttl/time.Second))
}

// WriteCatalog publishes the global action catalog (action -> workspace_scoped).
// No TTL: the catalog is static, code-defined data re-registered at deploy.
func (w *RedisWriter) WriteCatalog(ctx context.Context, actions map[string]bool, version int64) error {
	v := catalogValue{
		versioned: versioned{V: version, ComputedAt: time.Now().UTC().Format(time.RFC3339Nano)},
		Actions:   actions,
	}
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return w.rdb.Set(ctx, CatalogKey, raw, 0).Err()
}

// WriteTenantMeta sets tenant-level flags (autonomous agents enablement).
func (w *RedisWriter) WriteTenantMeta(ctx context.Context, tenant string, autonomousEnabled bool) error {
	raw, err := json.Marshal(tenantMetaValue{AutonomousEnabled: autonomousEnabled})
	if err != nil {
		return err
	}
	return w.rdb.Set(ctx, KeyTenantMeta(tenant), raw, 0).Err()
}

// InvalidateMessage is the perm.invalidate pub/sub payload (RBC-FR-042).
type InvalidateMessage struct {
	Tenant string   `json:"tenant"`
	Users  []string `json:"users"`
}

// PublishInvalidate notifies OPA sidecar caches that users' keys changed.
func (w *RedisWriter) PublishInvalidate(ctx context.Context, tenant string, users []string) error {
	raw, err := json.Marshal(InvalidateMessage{Tenant: tenant, Users: users})
	if err != nil {
		return err
	}
	return w.rdb.Publish(ctx, InvalidateChannel, raw).Err()
}

func (w *RedisWriter) casWrite(ctx context.Context, key string, v any, version, ttlSec int64) error {
	raw, err := json.Marshal(v)
	if err != nil {
		return fmt.Errorf("projection marshal %s: %w", key, err)
	}
	if err := casSet.Run(ctx, w.rdb, []string{key}, raw, version, ttlSec).Err(); err != nil {
		return fmt.Errorf("projection write %s: %w", key, err)
	}
	return nil
}

func (w *RedisWriter) readIndex(ctx context.Context, tenant, user string) ([]string, error) {
	raw, err := w.rdb.Get(ctx, KeyIndex(tenant, user)).Result()
	if err == redis.Nil {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("projection read index: %w", err)
	}
	var idx indexValue
	if err := json.Unmarshal([]byte(raw), &idx); err != nil {
		return nil, nil // corrupt index: treat as absent, keys self-heal via TTL
	}
	return idx.Keys, nil
}

func emptyIfNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}
