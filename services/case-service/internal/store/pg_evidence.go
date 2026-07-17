package store

import (
	"context"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/case-service/internal/domain"
)

const evidenceCols = `id, tenant_id, workspace_id, case_id, filename, content_type, size_bytes, storage_key, uploaded_by, created_at`

func scanEvidence(row pgx.Row) (*domain.CaseEvidence, error) {
	var e domain.CaseEvidence
	if err := row.Scan(&e.ID, &e.TenantID, &e.WorkspaceID, &e.CaseID, &e.Filename,
		&e.ContentType, &e.SizeBytes, &e.StorageKey, &e.UploadedBy, &e.CreatedAt); err != nil {
		return nil, err
	}
	return &e, nil
}

// InsertEvidence records an evidence pointer row (task #77). The bytes are
// already written to object storage under e.StorageKey by the caller.
func (s *PG) InsertEvidence(ctx context.Context, op domain.Op, e *domain.CaseEvidence) error {
	return s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO case_evidence
			  (id, tenant_id, workspace_id, case_id, filename, content_type, size_bytes, storage_key, uploaded_by)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
			e.ID, e.TenantID, e.WorkspaceID, e.CaseID, e.Filename, e.ContentType, e.SizeBytes, e.StorageKey, e.UploadedBy)
		return err
	})
}

// ListEvidence returns a case's non-deleted evidence, newest first.
func (s *PG) ListEvidence(ctx context.Context, tenant, caseID uuid.UUID) ([]*domain.CaseEvidence, error) {
	out := []*domain.CaseEvidence{}
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT `+evidenceCols+`
			FROM case_evidence WHERE case_id=$1 AND deleted_at IS NULL ORDER BY created_at DESC`, caseID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			e, err := scanEvidence(rows)
			if err != nil {
				return err
			}
			out = append(out, e)
		}
		return rows.Err()
	})
	return out, err
}

// GetEvidence returns one evidence row (for download: resolves the storage key).
func (s *PG) GetEvidence(ctx context.Context, tenant, id uuid.UUID) (*domain.CaseEvidence, error) {
	var e *domain.CaseEvidence
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		e, err = scanEvidence(tx.QueryRow(ctx, `SELECT `+evidenceCols+`
			FROM case_evidence WHERE id=$1 AND deleted_at IS NULL`, id))
		return err
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return e, err
}
