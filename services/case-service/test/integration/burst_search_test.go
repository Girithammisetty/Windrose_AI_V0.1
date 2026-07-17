package integration

import (
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestBurstProjectionWithinWindow creates N cases in a tight burst and asserts
// every one of them becomes searchable via the REAL OpenSearch projection
// within the ≤5s consistency window WITHOUT a manual reindex (CASE-FR-041).
//
// This guards the burst-projection gap: the async case.events.v1 → OpenSearch
// projector must keep up and never drop a create. Postgres stays the source of
// truth; the /admin/reindex endpoint remains only the recovery path, never a
// requirement of the steady-state happy path.
func TestBurstProjectionWithinWindow(t *testing.T) {
	h := requireHarness(t)
	if !h.kafka {
		t.Skip("Kafka (Redpanda) not reachable; projection is fed from case.events.v1")
	}
	const n = 8
	a := h.newActor(t)
	marker := "brst" + uuid.NewString()[:8]

	// Burst: fire N creates back-to-back with a shared search marker.
	ids := make(map[string]bool, n)
	for i := 0; i < n; i++ {
		c := h.createOne(t, a, "", time.Now().Add(24*time.Hour),
			map[string]any{"description": fmt.Sprintf("burst %s row %d", marker, i)})
		ids[c["id"].(string)] = true
	}
	require.Len(t, ids, n, "all N creates must produce distinct cases")

	// Poll the projection until all N are searchable, bounded by the 5s window
	// (small slack for HTTP + the 1s refresh_interval). No reindex is issued.
	deadline := time.Now().Add(6 * time.Second)
	var lastCount int
	for time.Now().Before(deadline) {
		r := h.do(t, "GET", "/api/v1/cases?q="+marker+"&size=50", a.tok, nil, nil)
		if r.status == http.StatusOK {
			if data, ok := r.body["data"].([]any); ok {
				lastCount = len(data)
				if lastCount >= n {
					break
				}
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	assert.Equalf(t, n, lastCount,
		"all %d burst-created cases must be searchable within the 5s window without a reindex (got %d)", n, lastCount)
}
