package projection

import "fmt"

// Redis key scheme per RBC-FR-040 (values are JSON strings carrying
// {"v": <version>, "computed_at": <ts>, ...}):
//
//	perm:{tenant}:{user}:actions   -> allowed tenant-scoped actions
//	perm:{tenant}:{user}:ws:{ws}   -> allowed workspace-scoped actions (absent => not assigned)
//	perm:{tenant}:{user}:res:{h}   -> grant level for explicitly-granted resources
//	perm:{tenant}:{user}:flags     -> {admin, ws_admin}
//	perm:{tenant}:{user}:index     -> bookkeeping: subsidiary keys, for stale-key GC
//	perm:{tenant}:archived_ws      -> tenant-level archived workspace ids
//	perm:{tenant}:meta             -> tenant flags (autonomous agents enablement)
//	perm:catalog:actions           -> action -> workspace_scoped (global)
//
// Invalidation channel: perm.invalidate, payload {"tenant": t, "users": [...]}.

const (
	// InvalidateChannel is the Redis pub/sub channel OPA sidecars subscribe to.
	InvalidateChannel = "perm.invalidate"
	// CatalogKey holds the global action catalog.
	CatalogKey = "perm:catalog:actions"
)

func KeyActions(tenant, user string) string { return fmt.Sprintf("perm:%s:%s:actions", tenant, user) }
func KeyWorkspace(tenant, user, ws string) string {
	return fmt.Sprintf("perm:%s:%s:ws:%s", tenant, user, ws)
}
func KeyResource(tenant, user, urnHash string) string {
	return fmt.Sprintf("perm:%s:%s:res:%s", tenant, user, urnHash)
}
func KeyFlags(tenant, user string) string { return fmt.Sprintf("perm:%s:%s:flags", tenant, user) }
func KeyIndex(tenant, user string) string { return fmt.Sprintf("perm:%s:%s:index", tenant, user) }
func KeyArchivedWs(tenant string) string  { return fmt.Sprintf("perm:%s:archived_ws", tenant) }
func KeyTenantMeta(tenant string) string  { return fmt.Sprintf("perm:%s:meta", tenant) }
