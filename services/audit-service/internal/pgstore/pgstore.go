// Package pgstore is audit-service's real Postgres metadata adapter: chain
// checkpoints, WORM export manifests, async jobs and DLQ redrive rows. Every
// tenant-scoped operation runs inside a transaction that first sets app.tenant_id
// so Postgres RLS enforces isolation below the application (MASTER-FR-001).
package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Store wraps a pgx pool.
type Store struct{ pool *pgxpool.Pool }

// New builds a Store.
func New(pool *pgxpool.Pool) *Store { return &Store{pool: pool} }

// Pool exposes the pool (readyz ping).
func (s *Store) Pool() *pgxpool.Pool { return s.pool }

// Ping checks connectivity.
func (s *Store) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

func (s *Store) withTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return fmt.Errorf("set tenant context: %w", err)
		}
		return fn(tx)
	})
}

// withPlatform runs fn under app.role=platform for cross-tenant maintenance
// (export scheduler, weekly self-verification).
func (s *Store) withPlatform(ctx context.Context, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.role', 'platform', true)`); err != nil {
			return fmt.Errorf("set platform context: %w", err)
		}
		return fn(tx)
	})
}

// ChainHead is a per-tenant-per-day chain checkpoint (AUD-FR-050).
type ChainHead struct {
	TenantID    uuid.UUID
	ChainDate   string
	HeadHash    string
	EventsCount uint64
	SealedAt    *time.Time
}

// GetChainHead reads the checkpoint for (tenant, date); nil when absent.
func (s *Store) GetChainHead(ctx context.Context, tenant uuid.UUID, date string) (*ChainHead, error) {
	var ch ChainHead
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx,
			`SELECT tenant_id, chain_date::text, head_hash, events_count, sealed_at
			   FROM chain_heads WHERE tenant_id=$1 AND chain_date=$2`, tenant, date).
			Scan(&ch.TenantID, &ch.ChainDate, &ch.HeadHash, &ch.EventsCount, &ch.SealedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &ch, nil
}

// UpsertChainHead advances the checkpoint to (headHash, eventsCount). Called on
// every ingested event so a rebalance/crash can recover seq + prev hash (BR-10).
func (s *Store) UpsertChainHead(ctx context.Context, tenant uuid.UUID, date, headHash string, count uint64) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`INSERT INTO chain_heads (tenant_id, chain_date, head_hash, events_count)
			 VALUES ($1,$2,$3,$4)
			 ON CONFLICT (tenant_id, chain_date)
			 DO UPDATE SET head_hash=EXCLUDED.head_hash, events_count=EXCLUDED.events_count, updated_at=now()`,
			tenant, date, headHash, count)
		return err
	})
}

// SealChainHead marks a day sealed (AUD-FR-021: manifest landed).
func (s *Store) SealChainHead(ctx context.Context, tenant uuid.UUID, date string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`UPDATE chain_heads SET sealed_at=now(), updated_at=now() WHERE tenant_id=$1 AND chain_date=$2`,
			tenant, date)
		return err
	})
}

// ListUnsealedDays returns (tenant, date) checkpoints with sealed_at IS NULL and
// date < today — the candidates for the daily WORM export (platform scan).
func (s *Store) ListUnsealedDays(ctx context.Context, before string) ([]ChainHead, error) {
	var out []ChainHead
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx,
			`SELECT tenant_id, chain_date::text, head_hash, events_count, sealed_at
			   FROM chain_heads WHERE sealed_at IS NULL AND chain_date < $1
			   ORDER BY chain_date ASC`, before)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var ch ChainHead
			if err := rows.Scan(&ch.TenantID, &ch.ChainDate, &ch.HeadHash, &ch.EventsCount, &ch.SealedAt); err != nil {
				return err
			}
			out = append(out, ch)
		}
		return rows.Err()
	})
	return out, err
}

// Manifest is a WORM export manifest row (AUD-FR-021).
type Manifest struct {
	ID              uuid.UUID
	TenantID        uuid.UUID
	ChainDate       string
	Revision        int
	URI             string
	ManifestSHA256  string
	ChainHead       string
	PrevManifestSHA string
	RowCount        uint64
	Status          string
	SealedAt        *time.Time
}

// LatestManifest returns the highest-revision manifest for (tenant, date); nil
// when none.
func (s *Store) LatestManifest(ctx context.Context, tenant uuid.UUID, date string) (*Manifest, error) {
	var m Manifest
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx,
			`SELECT id, tenant_id, chain_date::text, revision, uri, manifest_sha256, chain_head,
			        prev_manifest_sha256, row_count, status, sealed_at
			   FROM export_manifests WHERE tenant_id=$1 AND chain_date=$2
			   ORDER BY revision DESC LIMIT 1`, tenant, date).
			Scan(&m.ID, &m.TenantID, &m.ChainDate, &m.Revision, &m.URI, &m.ManifestSHA256,
				&m.ChainHead, &m.PrevManifestSHA, &m.RowCount, &m.Status, &m.SealedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &m, nil
}

// InsertManifest records a sealed manifest (AUD-FR-021/022).
func (s *Store) InsertManifest(ctx context.Context, m Manifest) error {
	return s.withTenant(ctx, m.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`INSERT INTO export_manifests
			   (id, tenant_id, chain_date, revision, uri, manifest_sha256, chain_head,
			    prev_manifest_sha256, row_count, status, sealed_at)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'sealed',now())`,
			m.ID, m.TenantID, m.ChainDate, m.Revision, m.URI, m.ManifestSHA256,
			m.ChainHead, m.PrevManifestSHA, m.RowCount)
		return err
	})
}

// ListSealedManifests lists sealed batches for a tenant (optionally one date)
// for GET /exports (AUD-FR-023).
func (s *Store) ListSealedManifests(ctx context.Context, tenant uuid.UUID, date string) ([]Manifest, error) {
	var out []Manifest
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT id, tenant_id, chain_date::text, revision, uri, manifest_sha256, chain_head,
		             prev_manifest_sha256, row_count, status, sealed_at
		        FROM export_manifests WHERE tenant_id=$1 AND status='sealed'`
		args := []any{tenant}
		if date != "" {
			q += ` AND chain_date=$2`
			args = append(args, date)
		}
		q += ` ORDER BY chain_date DESC, revision DESC`
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var m Manifest
			if err := rows.Scan(&m.ID, &m.TenantID, &m.ChainDate, &m.Revision, &m.URI, &m.ManifestSHA256,
				&m.ChainHead, &m.PrevManifestSHA, &m.RowCount, &m.Status, &m.SealedAt); err != nil {
				return err
			}
			out = append(out, m)
		}
		return rows.Err()
	})
	return out, err
}

// Job is an async job record (AUD-FR-032/060/061).
type Job struct {
	ID           uuid.UUID
	TenantID     uuid.UUID
	Kind         string
	ParamsDigest string
	Status       string
	ResultURI    string
	Error        string
}

// CreateJob inserts a running job.
func (s *Store) CreateJob(ctx context.Context, j Job) error {
	return s.withTenant(ctx, j.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`INSERT INTO async_jobs (id, tenant_id, kind, params_digest, status)
			 VALUES ($1,$2,$3,$4,'running')`, j.ID, j.TenantID, j.Kind, j.ParamsDigest)
		return err
	})
}

// FinishJob sets terminal status + result/error.
func (s *Store) FinishJob(ctx context.Context, tenant, id uuid.UUID, status, resultURI, errMsg string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`UPDATE async_jobs SET status=$3, result_uri=$4, error=$5, updated_at=now()
			   WHERE tenant_id=$1 AND id=$2`, tenant, id, status, resultURI, errMsg)
		return err
	})
}

// GetJob reads a job by id; nil when absent (or cross-tenant → nil via RLS).
func (s *Store) GetJob(ctx context.Context, tenant, id uuid.UUID) (*Job, error) {
	var j Job
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx,
			`SELECT id, tenant_id, kind, params_digest, status, result_uri, error
			   FROM async_jobs WHERE tenant_id=$1 AND id=$2`, tenant, id).
			Scan(&j.ID, &j.TenantID, &j.Kind, &j.ParamsDigest, &j.Status, &j.ResultURI, &j.Error)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &j, nil
}

// RecordRedrive audits a DLQ redrive (AC-15).
func (s *Store) RecordRedrive(ctx context.Context, tenant uuid.UUID, topic string, count int, actor, reason string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`INSERT INTO dlq_redrives (id, tenant_id, topic, count, actor, reason)
			 VALUES ($1,$2,$3,$4,$5,$6)`, uuid.New(), tenant, topic, count, actor, reason)
		return err
	})
}
