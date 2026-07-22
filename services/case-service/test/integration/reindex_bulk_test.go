package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/datacern-ai/case-service/internal/domain"
)

// seedCasesDirect inserts n live cases straight through the real store
// (bypassing the HTTP create path, which would be needlessly slow for
// seeding a large fixture) so B5's reindex fix can be exercised against a
// tenant bigger than one projector page (500) without the test taking
// minutes. Every case shares one CreatedAt: CasesPage's tiebreaker (id) must
// still visit each row exactly once even when created_at is fully tied.
func seedCasesDirect(t *testing.T, a actorCtx, n int) {
	t.Helper()
	now := time.Now().UTC()
	cases := make([]*domain.Case, n)
	for i := range cases {
		cases[i] = &domain.Case{
			ID: domain.NewID(), TenantID: a.tenant, WorkspaceID: a.workspace,
			Status: domain.StatusUnassigned, Severity: "medium",
			CreatedByID: "seed", DatasetURN: "urn:dataset:seed", RowPK: fmt.Sprintf("row-%d", i),
			DisplayProjection: map[string]string{}, SourceQueryURNs: []string{}, CustomFields: map[string]any{},
			DueDate: now.Add(24 * time.Hour), CreatedAt: now, UpdatedAt: now, CaseVersion: 1,
		}
	}
	op := domain.Op{Tenant: a.tenant, WorkspaceID: a.workspace, Actor: domain.Actor{Type: domain.TypUser, ID: "seed"}}
	_, _, err := h.pg.CreateCases(context.Background(), op, cases, "", 0)
	require.NoError(t, err)
}

func openSearchCount(t *testing.T, alias string) int {
	t.Helper()
	resp, err := http.Get("http://localhost:9200/" + alias + "/_count")
	require.NoError(t, err)
	defer resp.Body.Close()
	var out struct {
		Count int `json:"count"`
	}
	require.NoError(t, json.NewDecoder(resp.Body).Decode(&out))
	return out.Count
}

// TestReindexBulkPagesLargeTenant proves the B5 fix end-to-end: a tenant with
// more cases than one projector page (reindexPageSize=500) reindexes
// correctly via paginated Postgres reads + OpenSearch _bulk writes, not the
// old per-case GetCase+PUT loop. Both the keyset pagination primitive
// (CasesPage) and the full /admin/reindex HTTP path are exercised against the
// REAL Postgres (RLS, migration 000007's index) and REAL OpenSearch cluster.
func TestReindexBulkPagesLargeTenant(t *testing.T) {
	h := requireHarness(t)
	ctx := context.Background()
	a := h.newActor(t)

	const total = 1247 // > 2 full pages (500) + a partial page, deliberately not round
	seedCasesDirect(t, a, total)

	// CasesPage keyset pagination itself, with a page size independent of the
	// projector's, proves it advances correctly and returns every row exactly
	// once -- including when created_at is fully tied across all rows.
	seen := map[uuid.UUID]bool{}
	var afterCreatedAt time.Time
	var afterID uuid.UUID
	for {
		page, err := h.pg.CasesPage(ctx, a.tenant, afterCreatedAt, afterID, 400)
		require.NoError(t, err)
		if len(page) == 0 {
			break
		}
		for _, c := range page {
			require.False(t, seen[c.ID], "CasesPage must not repeat a case across pages")
			seen[c.ID] = true
		}
		last := page[len(page)-1]
		afterCreatedAt, afterID = last.CreatedAt, last.ID
		if len(page) < 400 {
			break
		}
	}
	require.Len(t, seen, total, "keyset pagination must visit every live case exactly once")

	// CaseCommentTextBatch: a single round trip for a whole page of ids must
	// not error on cases that have zero comments (simply absent from the map).
	var anyIDs []uuid.UUID
	for id := range seen {
		anyIDs = append(anyIDs, id)
		if len(anyIDs) == 10 {
			break
		}
	}
	texts, err := h.pg.CaseCommentTextBatch(ctx, a.tenant, anyIDs)
	require.NoError(t, err)
	assert.Empty(t, texts, "seeded cases have no comments, so the batch map must be empty")

	// End-to-end: the real /admin/reindex HTTP path, spanning 3 projector
	// pages (500+500+247) and multiple _bulk requests to the real cluster.
	r := h.do(t, "POST", "/api/v1/admin/reindex", a.tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.EqualValues(t, total, dataMap(r)["reindexed"])

	require.NoError(t, h.search.Refresh(ctx, a.tenant))
	assert.Equal(t, total, openSearchCount(t, "cases-"+a.tenant.String()),
		"the tenant's OpenSearch alias must hold exactly the reindexed doc count")
}
