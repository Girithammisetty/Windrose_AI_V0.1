package opaclient

import (
	"context"
	"encoding/json"

	"github.com/windrose-ai/go-common/redisx"
)

// SeedCatalogActions merges entries into the global rbac action-catalog
// projection (perm:catalog:actions, rbac RedisWriter.WriteCatalog format)
// ADDITIVELY: whatever a live rbac-service has already projected is preserved,
// and the key is written without a TTL (the catalog is durable, rbac owns it).
//
// Test harnesses and seed tooling MUST use this instead of a raw SET. The key
// is a single shared global: replacing it wipes action_known for every other
// service's actions, and attaching a TTL makes the whole catalog expire —
// either way EVERY guarded route in EVERY service 403s (even for admins) until
// rbac re-projects, which it only does at startup or on action registration.
func SeedCatalogActions(ctx context.Context, rc *redisx.Client, actions map[string]bool) error {
	doc := map[string]any{}
	if raw, ok, err := rc.Get(ctx, "perm:catalog:actions"); err != nil {
		return err
	} else if ok {
		if json.Unmarshal([]byte(raw), &doc) != nil {
			doc = map[string]any{}
		}
	}
	cur, _ := doc["actions"].(map[string]any)
	if cur == nil {
		cur = map[string]any{}
	}
	for a, ws := range actions {
		cur[a] = ws
	}
	doc["actions"] = cur
	raw, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	return rc.Set(ctx, "perm:catalog:actions", string(raw), 0)
}
