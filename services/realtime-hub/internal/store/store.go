// Package store is realtime-hub's minimal Postgres layer (BRD 20 §4): the
// stream_tickets audit copy (tenant-scoped, RLS) and the routing_rules
// config table. The hot paths (ticket verify, connection counters, replay) all
// live in Redis; Postgres holds only the durable audit/config rows.
package store

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/postgres" // driver
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/windrose-ai/realtime-hub/migrations"
)

// Migrate applies the embedded forward-only migrations (MASTER-FR-060).
func Migrate(databaseURL string) error {
	src, err := iofs.New(migrations.FS, ".")
	if err != nil {
		return fmt.Errorf("migration source: %w", err)
	}
	m, err := migrate.NewWithSourceInstance("iofs", src, databaseURL)
	if err != nil {
		return fmt.Errorf("migrate init: %w", err)
	}
	defer m.Close()
	if err := m.Up(); err != nil && !errors.Is(err, migrate.ErrNoChange) {
		return fmt.Errorf("migrate up: %w", err)
	}
	return nil
}

// PG is the Postgres-backed store.
type PG struct{ pool *pgxpool.Pool }

// NewPG wraps a pool.
func NewPG(pool *pgxpool.Pool) *PG { return &PG{pool: pool} }

// TicketAudit is the durable audit copy of a minted stream ticket (§4).
type TicketAudit struct {
	ID        uuid.UUID
	Tenant    uuid.UUID
	Subject   string
	Topics    []string
	IPHash    string
	ExpiresAt time.Time
}

// InsertTicketAudit writes the audit row inside a tenant-scoped transaction so
// RLS applies (MASTER-FR-001). Best-effort: the Redis ticket is authoritative.
func (s *PG) InsertTicketAudit(ctx context.Context, t TicketAudit) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, "SELECT set_config('app.tenant_id', $1, true)", t.Tenant.String()); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx,
		`INSERT INTO stream_tickets (id, tenant_id, subject, topics, ip_hash, expires_at)
		 VALUES ($1,$2,$3,$4,$5,$6)`,
		t.ID, t.Tenant, t.Subject, t.Topics, t.IPHash, t.ExpiresAt); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// PurgeExpiredTickets deletes audit rows past their TTL (hourly job, §4). Runs
// without a tenant scope, so it uses a superuser/owner connection outside RLS.
func (s *PG) PurgeExpiredTickets(ctx context.Context) (int64, error) {
	tag, err := s.pool.Exec(ctx, `DELETE FROM stream_tickets WHERE expires_at < now() - interval '1 hour'`)
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}

// SeedRoutingRules upserts the code-seeded routing table for ops visibility
// (RTH-FR-020). Code remains the source of truth; the table lets ops toggle
// `enabled` without a deploy.
func (s *PG) SeedRoutingRules(ctx context.Context, rules map[string]string) error {
	for eventType, template := range rules {
		if _, err := s.pool.Exec(ctx,
			`INSERT INTO routing_rules (id, event_type, topic_template)
			 VALUES ($1,$2,$3)
			 ON CONFLICT (event_type) DO UPDATE SET topic_template = EXCLUDED.topic_template, updated_at = now()`,
			uuid.New(), eventType, template); err != nil {
			return err
		}
	}
	return nil
}

// LoadDisabledRules returns the set of routing rule names ops have disabled.
func (s *PG) LoadDisabledRules(ctx context.Context) (map[string]bool, error) {
	rows, err := s.pool.Query(ctx, `SELECT event_type FROM routing_rules WHERE enabled = false`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]bool{}
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			return nil, err
		}
		out[name] = true
	}
	return out, rows.Err()
}
