package search

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"

	"github.com/datacern-ai/case-service/internal/domain"
	"github.com/datacern-ai/case-service/internal/store"
)

// reindexPageSize bounds how many cases are read from Postgres and bulk-sent
// to OpenSearch per round trip during a full-tenant reindex (B5, scalability
// audit): memory and request size stay O(reindexPageSize) regardless of how
// many cases a tenant has.
const reindexPageSize = 500

// CaseReader is the slice of the store the projector needs (satisfied by
// *store.PG). Kept as an interface so the projector has no hard store
// dependency and stays unit-testable.
type CaseReader interface {
	GetCase(ctx context.Context, tenant, id uuid.UUID) (*domain.Case, error)
	CaseCommentText(ctx context.Context, tenant, id uuid.UUID) (string, error)
	AllCaseIDs(ctx context.Context, tenant uuid.UUID) ([]uuid.UUID, error)
	CasesPage(ctx context.Context, tenant uuid.UUID, afterCreatedAt time.Time, afterID uuid.UUID, limit int) ([]*domain.Case, error)
	CaseCommentTextBatch(ctx context.Context, tenant uuid.UUID, caseIDs []uuid.UUID) (map[uuid.UUID]string, error)
}

// Projector rebuilds the OpenSearch projection from the Postgres source of
// truth in response to case events (CASE-FR-041). It is idempotent: re-reading
// Postgres and upserting with external versioning discards stale writes.
type Projector struct {
	Store  CaseReader
	Search *Client
}

// ProjectCase re-reads a case from Postgres and upserts its search doc.
// A deleted/absent case is a no-op (nothing to index).
func (p *Projector) ProjectCase(ctx context.Context, tenant, id uuid.UUID) error {
	c, err := p.Store.GetCase(ctx, tenant, id)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			return nil // case genuinely gone; nothing to index (reindex GCs any stale doc)
		}
		// Transient read failure (DB blip, RLS ctx, timeout): do NOT swallow —
		// return the error so the consumer retries. Swallowing here silently
		// drops the projection under burst load and requires a manual reindex.
		return err
	}
	text, err := p.Store.CaseCommentText(ctx, tenant, id)
	if err != nil {
		text = ""
	}
	return p.Search.IndexDoc(ctx, tenant, DocFromCase(c, text))
}

// Reindex rebuilds a tenant's whole index from Postgres and swaps the alias
// (CASE-FR-043, admin-only). Reads and bulk-writes one bounded page at a time
// (B5, scalability audit) instead of loading every case id, then GETting and
// PUTting them one at a time: a multi-million-case tenant no longer holds the
// whole rebuilt index in memory, nor makes 2N Postgres round trips + N
// OpenSearch PUTs.
func (p *Projector) Reindex(ctx context.Context, tenant uuid.UUID) (int, error) {
	idx, err := p.Search.CreateReindexGeneration(ctx, tenant)
	if err != nil {
		return 0, err
	}
	var (
		afterCreatedAt time.Time
		afterID        uuid.UUID
		total          int
	)
	for {
		cases, err := p.Store.CasesPage(ctx, tenant, afterCreatedAt, afterID, reindexPageSize)
		if err != nil {
			return total, err
		}
		if len(cases) == 0 {
			break
		}
		ids := make([]uuid.UUID, len(cases))
		for i, c := range cases {
			ids[i] = c.ID
		}
		// Best-effort, matching the prior per-case behaviour: a comment-read
		// failure leaves comment_text empty rather than failing the reindex.
		texts, err := p.Store.CaseCommentTextBatch(ctx, tenant, ids)
		if err != nil {
			texts = map[uuid.UUID]string{}
		}
		docs := make([]Doc, len(cases))
		for i, c := range cases {
			docs[i] = DocFromCase(c, texts[c.ID])
		}
		if err := p.Search.BulkIndexInto(ctx, idx, docs); err != nil {
			return total, err
		}
		total += len(docs)
		last := cases[len(cases)-1]
		afterCreatedAt, afterID = last.CreatedAt, last.ID
		if len(cases) < reindexPageSize {
			break
		}
	}
	if err := p.Search.SwapReindexAlias(ctx, tenant, idx); err != nil {
		return total, err
	}
	return total, nil
}
