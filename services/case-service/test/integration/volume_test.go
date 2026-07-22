package integration

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// caseVolumeRowCount is the seed size for TestVolumeReindexAtScale (WS5, BRD
// 58: "a volume load test at 1M rows for WS4 items"). Defaults to a size that
// completes in a reasonable soak-test window on a laptop; override with
// CASE_VOLUME_ROWS=1000000 to run the literal 1M-row scale the BRD names (not
// the CI default -- see docs/brd/58_production_hardening_BRD.md's WS5 log
// entry for the measured runtime at each size).
func caseVolumeRowCount() int {
	if v := os.Getenv("CASE_VOLUME_ROWS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return 100_000
}

// seedCasesBulk COPYs n live cases straight into Postgres via the admin
// (RLS-bypassing) pool -- deliberately skipping CreateCases's business rules
// (the 10,000-open-case-per-workspace limit, BR-13, dedup locking, outbox
// writes) because this is fixture seeding for a volume/load test of the READ
// side (B5's reindex), not a re-verification of the create-case business
// rules already covered by case_test.go and reindex_bulk_test.go. Splits
// across enough workspaces to respect realistic per-workspace scale even
// though the limit itself isn't enforced here.
func seedCasesBulk(t *testing.T, tenant uuid.UUID, n int) []uuid.UUID {
	t.Helper()
	const perWorkspace = 5000
	now := time.Now().UTC()
	rows := make([][]any, 0, n)
	workspaces := make([]uuid.UUID, 0, n/perWorkspace+1)
	var ws uuid.UUID
	for i := 0; i < n; i++ {
		if i%perWorkspace == 0 {
			ws = uuid.New()
			workspaces = append(workspaces, ws)
		}
		rows = append(rows, []any{
			uuid.New(), tenant, ws, int64(i + 1), int16(3) /* unassigned */, "medium",
			nil, nil, "seed", "urn:dataset:seed", "", fmt.Sprintf("row-%d", i), nil,
			[]byte("{}"), false, []string{}, "", now.Add(24 * time.Hour), "", []byte("{}"),
			nil, "", nil, nil, "", nil, 0, false, 1, now, now, nil,
		})
	}
	cols := []string{
		"id", "tenant_id", "workspace_id", "case_number", "status", "severity",
		"assigned_to_id", "assigned_to_at", "created_by_id", "dataset_urn", "dataset_version",
		"row_pk", "dedup_key", "display_projection", "projection_truncated", "source_query_urns",
		"dashboard_urn", "due_date", "description", "custom_fields", "disposition_id",
		"resolution_note", "resolved_at", "closed_at", "snapshot_ref", "recurrence_of",
		"reassign_count", "row_unavailable", "case_version", "created_at", "updated_at", "deleted_at",
	}
	copied, err := h.adminPool.CopyFrom(context.Background(), pgx.Identifier{"cases"}, cols, pgx.CopyFromRows(rows))
	require.NoError(t, err)
	require.EqualValues(t, n, copied)
	return workspaces
}

// TestVolumeReindexAtScale is WS5's volume/load test target for B5 (BRD 58,
// scalability audit): case-service's full-tenant reindex must complete in
// bounded memory/time regardless of tenant size, not just at the ~1000-case
// scale reindex_bulk_test.go already covers. See caseVolumeRowCount for how to
// run this at the BRD's literal "1M rows" via CASE_VOLUME_ROWS=1000000.
func TestVolumeReindexAtScale(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	a := h.newActor(t)
	n := caseVolumeRowCount()

	seedStart := time.Now()
	seedCasesBulk(t, a.tenant, n)
	t.Logf("seeded %d cases in %v", n, time.Since(seedStart))

	reindexStart := time.Now()
	r := h.do(t, "POST", "/api/v1/admin/reindex", a.tok, nil, nil)
	reindexElapsed := time.Since(reindexStart)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.EqualValues(t, n, dataMap(r)["reindexed"])
	t.Logf("reindexed %d cases in %v (%.0f cases/sec)", n, reindexElapsed, float64(n)/reindexElapsed.Seconds())

	require.NoError(t, h.search.Refresh(ctx, a.tenant))
	assert.Equal(t, n, openSearchCount(t, "cases-"+a.tenant.String()),
		"the tenant's OpenSearch alias must hold exactly the reindexed doc count at volume")
}
