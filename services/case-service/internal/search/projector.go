package search

import (
	"context"
	"errors"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/store"
)

// CaseReader is the slice of the store the projector needs (satisfied by
// *store.PG). Kept as an interface so the projector has no hard store
// dependency and stays unit-testable.
type CaseReader interface {
	GetCase(ctx context.Context, tenant, id uuid.UUID) (*domain.Case, error)
	CaseCommentText(ctx context.Context, tenant, id uuid.UUID) (string, error)
	AllCaseIDs(ctx context.Context, tenant uuid.UUID) ([]uuid.UUID, error)
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
// (CASE-FR-043, admin-only).
func (p *Projector) Reindex(ctx context.Context, tenant uuid.UUID) (int, error) {
	ids, err := p.Store.AllCaseIDs(ctx, tenant)
	if err != nil {
		return 0, err
	}
	docs := make([]Doc, 0, len(ids))
	for _, id := range ids {
		c, err := p.Store.GetCase(ctx, tenant, id)
		if err != nil {
			continue
		}
		text, _ := p.Store.CaseCommentText(ctx, tenant, id)
		docs = append(docs, DocFromCase(c, text))
	}
	if err := p.Search.Reindex(ctx, tenant, docs); err != nil {
		return 0, err
	}
	return len(docs), nil
}
