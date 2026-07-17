package store

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// IdempotencyRecord is a stored POST response (MASTER-FR-025).
type IdempotencyRecord struct {
	Status   int
	Response []byte
}

// GetIdempotent returns a prior response for (tenant, key), if any.
func (s *PG) GetIdempotent(ctx context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error) {
	if key == "" {
		return nil, nil
	}
	var rec IdempotencyRecord
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT status, response FROM idempotency_keys WHERE tenant_id=$1 AND key=$2`,
			tenant, key).Scan(&rec.Status, &rec.Response)
	})
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &rec, nil
}

// PutIdempotent stores a POST response for replay (MASTER-FR-025). Duplicate
// keys are ignored (first writer wins).
func (s *PG) PutIdempotent(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error {
	if key == "" {
		return nil
	}
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO idempotency_keys (tenant_id, key, method, path, status, response)
			VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id, key) DO NOTHING`,
			tenant, key, method, path, status, response)
		return err
	})
}
