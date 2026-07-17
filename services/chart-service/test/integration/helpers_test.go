package integration

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"
)

func endpointOf(h *harness) string { return h.redisAddr }

// opaReachable does a lightweight liveness probe against the OPA sidecar.
func opaReachable(ctx context.Context, opaURL string) bool {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, opaURL+"/health", nil)
	if err != nil {
		return false
	}
	client := &http.Client{Timeout: time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	_ = resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// seedAdminProjection writes the permissions_flat projection keys that grant a
// tenant-admin allow for every listed action, using the rbac key scheme the
// go-common loader reads. The catalog marks each action workspace_scoped (as
// chart-service registers them); the admin-flag short-circuit in
// windrose_authz_input.rego then allows any known action in a valid workspace
// context.
func seedAdminProjection(t *testing.T, h *harness, tenant, user, wsID string, actions []string) {
	t.Helper()
	ctx := context.Background()
	cat := map[string]bool{}
	for _, a := range actions {
		cat[a] = true // true = workspace_scoped
	}
	catalog, _ := json.Marshal(map[string]any{"actions": cat})
	flags, _ := json.Marshal(map[string]any{"admin": true, "ws_admin": []string{wsID}})
	_ = h.redis.R.Set(ctx, "perm:catalog:actions", catalog, 0).Err()
	_ = h.redis.R.Set(ctx, "perm:"+tenant+":"+user+":flags", flags, 0).Err()
}
