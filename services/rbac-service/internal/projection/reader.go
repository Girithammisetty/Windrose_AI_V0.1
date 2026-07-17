package projection

import (
	"context"
	"encoding/json"
	"sort"
	"strings"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// Reader is the projection view decision paths evaluate against
// (authz.Decide and, in production, the OPA sidecar's Redis reads).
// The `found` returns distinguish deny-by-default (key present, action
// absent) from a projection miss (key absent -> fallback per RBC-FR-045).
type Reader interface {
	// TenantActions returns the user's allowed tenant-scoped action set.
	TenantActions(ctx context.Context, tenant, user string) (actions []string, found bool, err error)
	// Workspace returns the user's workspace entry; found=false => not assigned or miss.
	Workspace(ctx context.Context, tenant, user, workspaceID string) (WorkspaceEntry, bool, error)
	// Resource returns the grant entry for a URN hash.
	Resource(ctx context.Context, tenant, user, urnHash string) (ResourceEntry, bool, error)
	// UserFlags returns the admin/ws_admin flags.
	UserFlags(ctx context.Context, tenant, user string) (Flags, bool, error)
	// ArchivedWorkspaces returns the tenant's archived workspace id set.
	ArchivedWorkspaces(ctx context.Context, tenant string) (map[string]bool, error)
	// ActionScoped resolves an action's workspace_scoped flag from the catalog.
	ActionScoped(ctx context.Context, action string) (scoped bool, known bool, err error)
	// AutonomousEnabled reports the tenant's autonomous-agent enablement flag.
	AutonomousEnabled(ctx context.Context, tenant string) (bool, error)
}

// RedisReader reads the materialized projection. OnNearExpiry, when set, is
// invoked for keys observed with < RefreshWindow TTL remaining
// (refresh-on-read, RBC-FR-047) — wired to dirty-marking in the server.
type RedisReader struct {
	rdb          redis.UniversalClient
	OnNearExpiry func(tenant, user string)
}

func NewRedisReader(rdb redis.UniversalClient) *RedisReader {
	return &RedisReader{rdb: rdb}
}

func (r *RedisReader) get(ctx context.Context, key string, out any) (bool, error) {
	raw, err := r.rdb.Get(ctx, key).Result()
	if err == redis.Nil {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if err := json.Unmarshal([]byte(raw), out); err != nil {
		return false, nil // corrupt entry: treat as miss, fallback repairs
	}
	return true, nil
}

func (r *RedisReader) maybeRefresh(ctx context.Context, key, tenant, user string) {
	if r.OnNearExpiry == nil {
		return
	}
	ttl, err := r.rdb.TTL(ctx, key).Result()
	if err == nil && ttl > 0 && ttl < RefreshWindow {
		r.OnNearExpiry(tenant, user)
	}
}

func (r *RedisReader) TenantActions(ctx context.Context, tenant, user string) ([]string, bool, error) {
	var v actionsValue
	found, err := r.get(ctx, KeyActions(tenant, user), &v)
	if !found || err != nil {
		return nil, false, err
	}
	r.maybeRefresh(ctx, KeyActions(tenant, user), tenant, user)
	return v.Actions, true, nil
}

func (r *RedisReader) Workspace(ctx context.Context, tenant, user, workspaceID string) (WorkspaceEntry, bool, error) {
	var v wsValue
	found, err := r.get(ctx, KeyWorkspace(tenant, user, workspaceID), &v)
	if !found || err != nil || v.Deleted {
		return WorkspaceEntry{}, false, err // tombstone reads as not-assigned
	}
	return WorkspaceEntry{Actions: v.Actions, Archived: v.Archived}, true, nil
}

func (r *RedisReader) Resource(ctx context.Context, tenant, user, urnHash string) (ResourceEntry, bool, error) {
	var v resValue
	found, err := r.get(ctx, KeyResource(tenant, user, urnHash), &v)
	if !found || err != nil || v.Deleted {
		return ResourceEntry{}, false, err // tombstone reads as no grant
	}
	return v.ResourceEntry, true, nil
}

func (r *RedisReader) UserFlags(ctx context.Context, tenant, user string) (Flags, bool, error) {
	var v flagsValue
	found, err := r.get(ctx, KeyFlags(tenant, user), &v)
	if !found || err != nil {
		return Flags{}, false, err
	}
	ws := make([]uuid.UUID, 0, len(v.WsAdmin))
	for _, s := range v.WsAdmin {
		if id, err := uuid.Parse(s); err == nil {
			ws = append(ws, id)
		}
	}
	return Flags{Admin: v.Admin, WsAdmin: ws, Roles: v.Roles}, true, nil
}

// EffectiveCapabilities returns the caller's OWN display view for the UI gate:
// the role display names, the union of allowed action names (tenant-scoped +
// every assigned workspace's actions), and the admin flag. This is a read of
// the materialized projection for one subject — NOT an authorization decision;
// the domain services still enforce every action (RBC-FR-040, MASTER-FR-002).
func (r *RedisReader) EffectiveCapabilities(ctx context.Context, tenant, user string) (roles []string, actions []string, admin bool, found bool, err error) {
	set := map[string]struct{}{}
	var fv flagsValue
	fOK, err := r.get(ctx, KeyFlags(tenant, user), &fv)
	if err != nil {
		return nil, nil, false, false, err
	}
	if fOK {
		found = true
		admin = fv.Admin
		roles = fv.Roles
	}
	if ta, ok, e := r.TenantActions(ctx, tenant, user); e != nil {
		return nil, nil, false, false, e
	} else if ok {
		found = true
		for _, a := range ta {
			set[a] = struct{}{}
		}
	}
	keys, e := r.indexKeys(ctx, tenant, user)
	if e != nil {
		return nil, nil, false, false, e
	}
	wsPrefix := "perm:" + tenant + ":" + user + ":ws:"
	for _, k := range keys {
		if !strings.HasPrefix(k, wsPrefix) {
			continue
		}
		var wv wsValue
		if ok, _ := r.get(ctx, k, &wv); ok && !wv.Deleted {
			found = true
			for _, a := range wv.Actions {
				set[a] = struct{}{}
			}
		}
	}
	actions = setToSorted(set)
	return roles, actions, admin, found, nil
}

func (r *RedisReader) indexKeys(ctx context.Context, tenant, user string) ([]string, error) {
	var iv indexValue
	found, err := r.get(ctx, KeyIndex(tenant, user), &iv)
	if err != nil || !found {
		return nil, err
	}
	return iv.Keys, nil
}

func setToSorted(set map[string]struct{}) []string {
	out := make([]string, 0, len(set))
	for a := range set {
		out = append(out, a)
	}
	sort.Strings(out)
	return out
}

func (r *RedisReader) ArchivedWorkspaces(ctx context.Context, tenant string) (map[string]bool, error) {
	var v archivedWsValue
	found, err := r.get(ctx, KeyArchivedWs(tenant), &v)
	if err != nil || !found {
		return map[string]bool{}, err
	}
	out := make(map[string]bool, len(v.Workspaces))
	for _, id := range v.Workspaces {
		out[id] = true
	}
	return out, nil
}

func (r *RedisReader) ActionScoped(ctx context.Context, action string) (bool, bool, error) {
	var v catalogValue
	found, err := r.get(ctx, CatalogKey, &v)
	if err != nil || !found {
		return false, false, err
	}
	scoped, known := v.Actions[action]
	return scoped, known, nil
}

func (r *RedisReader) AutonomousEnabled(ctx context.Context, tenant string) (bool, error) {
	var v tenantMetaValue
	found, err := r.get(ctx, KeyTenantMeta(tenant), &v)
	if err != nil || !found {
		return false, err
	}
	return v.AutonomousEnabled, nil
}

// FlatReader adapts an in-memory Flat (plus tenant/catalog context) to the
// Reader contract. It backs the SQL fallback path (/authz/check evaluates the
// freshly-loaded snapshot through the exact same decision code) and unit
// tests — the "in-memory policy fake" required by the repo conventions.
type FlatReader struct {
	Flats      map[string]Flat // user id -> flat
	Catalog    map[string]bool // action -> workspace_scoped
	Archived   map[string]bool // workspace id -> archived (tenant-level)
	Autonomous bool
}

func NewFlatReader(f Flat, catalog map[string]bool, archivedWs []uuid.UUID) *FlatReader {
	arch := map[string]bool{}
	for _, id := range archivedWs {
		arch[id.String()] = true
	}
	return &FlatReader{
		Flats:    map[string]Flat{f.UserID: f},
		Catalog:  catalog,
		Archived: arch,
	}
}

// flat resolves a user's Flat, enforcing tenant matching exactly like the
// Redis key scheme does (keys embed the tenant, so a wrong tenant is a miss).
func (m *FlatReader) flat(tenant, user string) (Flat, bool) {
	f, ok := m.Flats[user]
	if !ok {
		return Flat{}, false
	}
	if f.TenantID != uuid.Nil && f.TenantID.String() != tenant {
		return Flat{}, false
	}
	return f, true
}

func (m *FlatReader) TenantActions(_ context.Context, tenant, user string) ([]string, bool, error) {
	f, ok := m.flat(tenant, user)
	if !ok {
		return nil, false, nil
	}
	return f.TenantActions, true, nil
}

func (m *FlatReader) Workspace(_ context.Context, tenant, user, workspaceID string) (WorkspaceEntry, bool, error) {
	f, ok := m.flat(tenant, user)
	if !ok {
		return WorkspaceEntry{}, false, nil
	}
	id, err := uuid.Parse(workspaceID)
	if err != nil {
		return WorkspaceEntry{}, false, nil
	}
	e, ok := f.WorkspaceActions[id]
	return e, ok, nil
}

func (m *FlatReader) Resource(_ context.Context, tenant, user, urnHash string) (ResourceEntry, bool, error) {
	f, ok := m.flat(tenant, user)
	if !ok {
		return ResourceEntry{}, false, nil
	}
	e, ok := f.Resources[urnHash]
	return e, ok, nil
}

func (m *FlatReader) UserFlags(_ context.Context, tenant, user string) (Flags, bool, error) {
	f, ok := m.flat(tenant, user)
	if !ok {
		return Flags{}, false, nil
	}
	return f.Flags, true, nil
}

func (m *FlatReader) ArchivedWorkspaces(_ context.Context, _ string) (map[string]bool, error) {
	return m.Archived, nil
}

// EffectiveCapabilities mirrors RedisReader.EffectiveCapabilities against the
// in-memory Flat (SQL fallback path + unit tests).
func (m *FlatReader) EffectiveCapabilities(_ context.Context, tenant, user string) (roles []string, actions []string, admin bool, found bool, err error) {
	f, ok := m.flat(tenant, user)
	if !ok {
		return []string{}, []string{}, false, false, nil
	}
	set := map[string]struct{}{}
	for _, a := range f.TenantActions {
		set[a] = struct{}{}
	}
	for _, entry := range f.WorkspaceActions {
		for _, a := range entry.Actions {
			set[a] = struct{}{}
		}
	}
	return f.Flags.Roles, setToSorted(set), f.Flags.Admin, true, nil
}

func (m *FlatReader) ActionScoped(_ context.Context, action string) (bool, bool, error) {
	scoped, known := m.Catalog[action]
	return scoped, known, nil
}

func (m *FlatReader) AutonomousEnabled(_ context.Context, _ string) (bool, error) {
	return m.Autonomous, nil
}
