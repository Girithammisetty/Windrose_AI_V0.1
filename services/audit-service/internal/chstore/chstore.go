// Package chstore is audit-service's real ClickHouse adapter (AUD-FR-010, §4):
// the append-only audit_events store. It speaks the native ClickHouse protocol
// (deploy: localhost:9010) via clickhouse-go/v2 — there is no in-memory mode in
// the runtime path. It owns the DDL, batch ingest, the search/dual-attribution
// queries and the ordered chain scan the verifier replays.
package chstore

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/domain"
)

// Store wraps a ClickHouse connection.
type Store struct {
	conn  driver.Conn
	table string
}

// Config configures the ClickHouse connection.
type Config struct {
	Addr     string // host:port native protocol, e.g. localhost:9010
	Database string
	Username string
	Password string
	Table    string // default audit_events
}

// Open dials ClickHouse and returns a Store. It does not run DDL; call Migrate.
func Open(ctx context.Context, cfg Config) (*Store, error) {
	if cfg.Table == "" {
		cfg.Table = "audit_events"
	}
	conn, err := clickhouse.Open(&clickhouse.Options{
		Addr: []string{cfg.Addr},
		Auth: clickhouse.Auth{
			Database: cfg.Database,
			Username: cfg.Username,
			Password: cfg.Password,
		},
		DialTimeout:     5 * time.Second,
		MaxOpenConns:    10,
		MaxIdleConns:    5,
		ConnMaxLifetime: time.Hour,
	})
	if err != nil {
		return nil, err
	}
	if err := conn.Ping(ctx); err != nil {
		return nil, fmt.Errorf("clickhouse ping: %w", err)
	}
	return &Store{conn: conn, table: cfg.Table}, nil
}

// Ping checks connectivity (readyz).
func (s *Store) Ping(ctx context.Context) error { return s.conn.Ping(ctx) }

// Close releases the connection.
func (s *Store) Close() error { return s.conn.Close() }

// Migrate creates the append-only audit_events table (idempotent). Single-node
// ReplacingMergeTree(ingested_at) keyed on (tenant_id, occurred_at, event_id):
// replays converge to one row per (tenant,event) at merge time; queries use
// FINAL for exactness (AUD-FR-004). Partitioned by month, 7-year TTL
// (AUD-FR-010/011). chain_date carries the ingest-day the chain seq belongs to
// (BR-2/BR-3).
func (s *Store) Migrate(ctx context.Context) error {
	ddl := fmt.Sprintf(`CREATE TABLE IF NOT EXISTS %s (
  event_id UUID,
  event_type LowCardinality(String),
  source_topic LowCardinality(String),
  tenant_id UUID,
  actor_type Enum8('user'=1,'service'=2,'agent'=3),
  actor_id String,
  via_agent_id String,
  via_agent_version String,
  obo_user_id String,
  resource_urn String,
  resource_service LowCardinality(String),
  resource_type LowCardinality(String),
  action LowCardinality(String),
  occurred_at DateTime64(3, 'UTC'),
  ingested_at DateTime64(3, 'UTC'),
  trace_id String,
  payload_digest FixedString(64),
  payload_json String CODEC(ZSTD(3)),
  payload_ref String,
  chain_date Date,
  chain_seq UInt64,
  chain_hash FixedString(64),
  INDEX ix_urn resource_urn TYPE tokenbf_v1(8192,3,0) GRANULARITY 4,
  INDEX ix_actor actor_id TYPE bloom_filter GRANULARITY 4,
  INDEX ix_agent via_agent_id TYPE bloom_filter GRANULARITY 4,
  INDEX ix_trace trace_id TYPE bloom_filter GRANULARITY 4
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (tenant_id, occurred_at, event_id)
TTL toDateTime(occurred_at) + INTERVAL 7 YEAR`, s.table)
	return s.conn.Exec(ctx, ddl)
}

// Insert appends one record (real batch insert). Idempotency is provided by the
// Redis dedup pre-filter upstream plus ReplacingMergeTree convergence.
func (s *Store) Insert(ctx context.Context, r domain.Record) error {
	return s.InsertBatch(ctx, []domain.Record{r})
}

// InsertBatch appends many records in one native batch.
func (s *Store) InsertBatch(ctx context.Context, recs []domain.Record) error {
	if len(recs) == 0 {
		return nil
	}
	batch, err := s.conn.PrepareBatch(ctx, "INSERT INTO "+s.table)
	if err != nil {
		return err
	}
	for _, r := range recs {
		cd, _ := time.Parse("2006-01-02", r.ChainDate)
		if err := batch.Append(
			r.EventID, r.EventType, r.SourceTopic, r.TenantID, r.ActorType, r.ActorID,
			r.ViaAgentID, r.ViaAgentVersion, r.OboUserID, r.ResourceURN, r.ResourceService,
			r.ResourceType, r.Action, r.OccurredAt.UTC(), r.IngestedAt.UTC(), r.TraceID,
			r.PayloadDigest, r.PayloadJSON, r.PayloadRef, cd, r.ChainSeq, r.ChainHash,
		); err != nil {
			return err
		}
	}
	return batch.Send()
}

const selectCols = `event_id, event_type, source_topic, tenant_id, toString(actor_type), actor_id,
 via_agent_id, via_agent_version, obo_user_id, resource_urn, resource_service, resource_type,
 action, occurred_at, ingested_at, trace_id, payload_digest, payload_json, payload_ref,
 toString(chain_date), chain_seq, chain_hash`

// chTime formats a time as a ms-precision ClickHouse DateTime64(3) literal so
// range params keep sub-second precision through the driver.
func chTime(t time.Time) string { return t.UTC().Format("2006-01-02 15:04:05.000") }

func (s *Store) scanRows(rows driver.Rows) ([]domain.Record, error) {
	defer rows.Close()
	var out []domain.Record
	for rows.Next() {
		var r domain.Record
		var chainDate string
		if err := rows.Scan(
			&r.EventID, &r.EventType, &r.SourceTopic, &r.TenantID, &r.ActorType, &r.ActorID,
			&r.ViaAgentID, &r.ViaAgentVersion, &r.OboUserID, &r.ResourceURN, &r.ResourceService,
			&r.ResourceType, &r.Action, &r.OccurredAt, &r.IngestedAt, &r.TraceID,
			&r.PayloadDigest, &r.PayloadJSON, &r.PayloadRef, &chainDate, &r.ChainSeq, &r.ChainHash,
		); err != nil {
			return nil, err
		}
		r.ChainDate = chainDate
		out = append(out, r)
	}
	return out, rows.Err()
}

// Search runs a filtered, tenant-scoped, cursor-paginated query sorted
// -occurred_at (AUD-FR-030/031). The tenant predicate is always injected from
// the caller's verified identity — never request input (MASTER-FR-001/002, BR-4).
func (s *Store) Search(ctx context.Context, f domain.SearchFilter) ([]domain.Record, error) {
	var where []string
	var args []any
	where = append(where, "tenant_id = ?")
	args = append(args, f.TenantID)
	// Bind the range as ms-precision strings: passing a time.Time param for a
	// DateTime64(3) comparison is truncated to whole seconds by the driver,
	// which would drop sub-second events at the boundary.
	where = append(where, "occurred_at >= ?", "occurred_at <= ?")
	args = append(args, chTime(f.From), chTime(f.To))

	// Dual attribution (AUD-FR-031): OBO rows for (actor_id=Y via via_agent_id=X)
	// optionally unioned with the agent's autonomous rows.
	if f.ViaAgentID != "" && f.ActorID != "" {
		if f.IncludeAuto {
			where = append(where, "((toString(actor_type)='user' AND actor_id = ? AND via_agent_id = ?) OR (toString(actor_type)='agent' AND actor_id = ?))")
			args = append(args, f.ActorID, f.ViaAgentID, f.ViaAgentID)
		} else {
			where = append(where, "toString(actor_type)='user'", "actor_id = ?", "via_agent_id = ?")
			args = append(args, f.ActorID, f.ViaAgentID)
		}
	} else {
		if f.ActorID != "" {
			where = append(where, "actor_id = ?")
			args = append(args, f.ActorID)
		}
		if f.ViaAgentID != "" {
			where = append(where, "via_agent_id = ?")
			args = append(args, f.ViaAgentID)
		}
	}
	if f.ActorType != "" {
		where = append(where, "toString(actor_type) = ?")
		args = append(args, f.ActorType)
	}
	if f.ResourcePrefix != "" {
		where = append(where, "startsWith(resource_urn, ?)")
		args = append(args, f.ResourcePrefix)
	} else if f.ResourceURN != "" {
		where = append(where, "resource_urn = ?")
		args = append(args, f.ResourceURN)
	}
	if f.Action != "" {
		where = append(where, "action = ?")
		args = append(args, f.Action)
	}
	if f.EventType != "" {
		where = append(where, "event_type = ?")
		args = append(args, f.EventType)
	}
	if f.TraceID != "" {
		where = append(where, "trace_id = ?")
		args = append(args, f.TraceID)
	}
	if f.AfterOccurred != nil && f.AfterEventID != nil {
		// Descending keyset cursor.
		where = append(where, "(occurred_at < ? OR (occurred_at = ? AND event_id < ?))")
		ct := chTime(*f.AfterOccurred)
		args = append(args, ct, ct, *f.AfterEventID)
	}
	limit := f.Limit
	if limit <= 0 {
		limit = 50
	}
	q := fmt.Sprintf("SELECT %s FROM %s FINAL WHERE %s ORDER BY occurred_at DESC, event_id DESC LIMIT %d",
		selectCols, s.table, strings.Join(where, " AND "), limit+1)
	rows, err := s.conn.Query(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	return s.scanRows(rows)
}

// GetEvent returns a single record by (tenant, event_id) or nil when absent.
func (s *Store) GetEvent(ctx context.Context, tenant, eventID uuid.UUID) (*domain.Record, error) {
	q := fmt.Sprintf("SELECT %s FROM %s FINAL WHERE tenant_id = ? AND event_id = ? LIMIT 1", selectCols, s.table)
	rows, err := s.conn.Query(ctx, q, tenant, eventID)
	if err != nil {
		return nil, err
	}
	recs, err := s.scanRows(rows)
	if err != nil {
		return nil, err
	}
	if len(recs) == 0 {
		return nil, nil
	}
	return &recs[0], nil
}

// ChainScan returns all rows for (tenant, chain_date) ordered by chain_seq —
// the input the verifier replays (AUD-FR-051). FINAL so any tampered replacement
// row (higher ingested_at) is the one surfaced, letting verification catch it.
func (s *Store) ChainScan(ctx context.Context, tenant uuid.UUID, chainDate string) ([]domain.Record, error) {
	q := fmt.Sprintf("SELECT %s FROM %s FINAL WHERE tenant_id = ? AND chain_date = ? ORDER BY chain_seq ASC", selectCols, s.table)
	rows, err := s.conn.Query(ctx, q, tenant, chainDate)
	if err != nil {
		return nil, err
	}
	return s.scanRows(rows)
}

// ChainTip returns the highest chain_seq and its chain_hash for (tenant,
// chain_date) — the authoritative recovery anchor. found=false when the day has
// no rows yet. Because ClickHouse is the durable audit store, seeding the live
// chain counter from the tip guarantees a retried event that never landed (a
// phantom seq in Redis) cannot leave a permanent gap: the tip only advances on a
// real stored row.
func (s *Store) ChainTip(ctx context.Context, tenant uuid.UUID, chainDate string) (uint64, string, bool, error) {
	q := fmt.Sprintf(`SELECT chain_seq, chain_hash FROM %s FINAL
	  WHERE tenant_id = ? AND chain_date = ? ORDER BY chain_seq DESC LIMIT 1`, s.table)
	var seq uint64
	var hash string
	err := s.conn.QueryRow(ctx, q, tenant, chainDate).Scan(&seq, &hash)
	if err != nil {
		if isNoRows(err) {
			return 0, "", false, nil
		}
		return 0, "", false, err
	}
	return seq, hash, true, nil
}

func isNoRows(err error) bool {
	return err != nil && err.Error() == "sql: no rows in result set"
}

// CountForDay returns the number of distinct events for (tenant, chain_date).
func (s *Store) CountForDay(ctx context.Context, tenant uuid.UUID, chainDate string) (uint64, error) {
	q := fmt.Sprintf("SELECT count() FROM (SELECT event_id FROM %s FINAL WHERE tenant_id = ? AND chain_date = ?)", s.table)
	var n uint64
	if err := s.conn.QueryRow(ctx, q, tenant, chainDate).Scan(&n); err != nil {
		return 0, err
	}
	return n, nil
}

// RawSelect runs a tenant-scoped SELECT with a caller-built WHERE clause and
// deterministic ordering (compliance packs, AUD-FR-060/061). The caller is
// responsible for injecting the tenant predicate into where.
func (s *Store) RawSelect(ctx context.Context, where, orderBy string, args ...any) ([]domain.Record, error) {
	if orderBy == "" {
		orderBy = "occurred_at ASC, event_id ASC"
	}
	q := fmt.Sprintf("SELECT %s FROM %s FINAL WHERE %s ORDER BY %s", selectCols, s.table, where, orderBy)
	rows, err := s.conn.Query(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	return s.scanRows(rows)
}

// RawExec runs an arbitrary statement (test-only tamper injection helper lives
// in _test files; this is used by DDL/TTL admin paths).
func (s *Store) RawExec(ctx context.Context, stmt string, args ...any) error {
	return s.conn.Exec(ctx, stmt, args...)
}

// Conn exposes the underlying connection for advanced queries (compliance
// packs). Callers must keep queries tenant-scoped.
func (s *Store) Conn() driver.Conn { return s.conn }

// Table returns the audit table name.
func (s *Store) Table() string { return s.table }
