package store

import (
	"context"

	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/rbac-service/internal/domain"
)

// RegisterActions upserts catalog entries (RBC-FR-022: idempotent
// registration API called by each service at deploy). The catalog is
// append-only per BR-5: workspace_scoped/description may be corrected, but
// entries are never hard-deleted here — deprecation flips a flag.
func (s *Store) RegisterActions(ctx context.Context, defs []domain.ActionDef) error {
	for _, d := range defs {
		svc, res, verb, err := domain.ParseAction(d.Action)
		if err != nil {
			return &ValidationError{Code: CodeValidationFailed, Message: err.Error()}
		}
		if svc != d.Service || res != d.Resource || verb != d.Verb {
			d.Service, d.Resource, d.Verb = svc, res, verb
		}
	}
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		for _, d := range defs {
			svc, res, verb, _ := domain.ParseAction(d.Action)
			if _, err := tx.Exec(ctx, `
				INSERT INTO actions (action, service, resource, verb, workspace_scoped, description)
				VALUES ($1,$2,$3,$4,$5,$6)
				ON CONFLICT (action) DO UPDATE
				SET workspace_scoped = EXCLUDED.workspace_scoped,
				    description = EXCLUDED.description,
				    deprecated = false,
				    updated_at = now()`,
				d.Action, svc, res, verb, d.WorkspaceScoped, d.Description); err != nil {
				return err
			}
		}
		return nil
	})
}

// DeprecateAction flips the deprecation flag (BR-5: two-release window during
// which the action still evaluates but usage is logged).
func (s *Store) DeprecateAction(ctx context.Context, action string) error {
	tag, err := s.pool.Exec(ctx, `UPDATE actions SET deprecated = true, updated_at = now() WHERE action = $1`, action)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// ListActions returns the catalog, cursor-paginated by action name.
func (s *Store) ListActions(ctx context.Context, cursor string, limit int) (Page[domain.ActionDef], error) {
	limit = ClampLimit(limit)
	var page Page[domain.ActionDef]
	rows, err := s.pool.Query(ctx, `
		SELECT action, service, resource, verb, workspace_scoped, description, deprecated
		FROM actions WHERE action > $1 ORDER BY action LIMIT $2`, cursor, limit+1)
	if err != nil {
		return page, err
	}
	defer rows.Close()
	for rows.Next() {
		var d domain.ActionDef
		if err := rows.Scan(&d.Action, &d.Service, &d.Resource, &d.Verb, &d.WorkspaceScoped, &d.Description, &d.Deprecated); err != nil {
			return page, err
		}
		page.Data = append(page.Data, d)
	}
	if err := rows.Err(); err != nil {
		return page, err
	}
	if len(page.Data) > limit {
		page.Data = page.Data[:limit]
		page.HasMore = true
		page.NextCursor = page.Data[limit-1].Action
	}
	return page, nil
}

// CatalogMap loads action -> workspace_scoped for the whole catalog.
func (s *Store) CatalogMap(ctx context.Context) (map[string]bool, error) {
	rows, err := s.pool.Query(ctx, `SELECT action, workspace_scoped FROM actions`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]bool{}
	for rows.Next() {
		var a string
		var scoped bool
		if err := rows.Scan(&a, &scoped); err != nil {
			return nil, err
		}
		out[a] = scoped
	}
	return out, rows.Err()
}
