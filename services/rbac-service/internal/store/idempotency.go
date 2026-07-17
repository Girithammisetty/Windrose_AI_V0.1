package store

import (
	"context"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// IdempotencyRecord is a stored POST response for Idempotency-Key replay
// (MASTER-FR-025; duplicate keys within 24h replay the original response).
type IdempotencyRecord struct {
	Status   int
	Response []byte
}

// GetIdempotency returns the stored response for a key, if fresh (<24h).
func (s *Store) GetIdempotency(ctx context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error) {
	var rec IdempotencyRecord
	err := s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT status, response FROM idempotency_keys
			WHERE tenant_id = $1 AND key = $2 AND created_at > now() - interval '24 hours'`,
			tenant, key).Scan(&rec.Status, &rec.Response)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &rec, nil
}

// PutIdempotency stores a response; concurrent duplicates keep the first.
func (s *Store) PutIdempotency(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error {
	return s.WithTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO idempotency_keys (tenant_id, key, method, path, status, response)
			VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (tenant_id, key) DO NOTHING`,
			tenant, key, method, path, status, response)
		return err
	})
}
