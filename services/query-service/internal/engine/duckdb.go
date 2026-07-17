package engine

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"math/big"
	"strings"
	"sync"

	"github.com/marcboeker/go-duckdb/v2"
)

// httpfs INSTALL writes to the shared on-disk extension directory; running it
// concurrently from many fresh workers (e.g. a dashboard batch fanning out N
// chart resolves at once) races and intermittently fails. INSTALL persists
// process-wide, so serialize it to exactly one successful run; per-worker LOAD
// (cheap, from the installed extension) stays concurrent.
var (
	httpfsMu        sync.Mutex
	httpfsInstalled bool
)

func ensureHTTPFSInstalled(ctx context.Context, db *sql.DB) error {
	httpfsMu.Lock()
	defer httpfsMu.Unlock()
	if httpfsInstalled {
		return nil
	}
	if _, err := db.ExecContext(ctx, "INSTALL httpfs"); err != nil {
		return err
	}
	httpfsInstalled = true
	return nil
}

// DuckDB is the real in-service engine for small/interactive execution
// (QRY-FR-040). Worker isolation per BR-7: every execution gets a dedicated
// single-connection worker with its own memory cap, recycled (closed) after
// the query — a poisoned query cannot affect another tenant's execution.
type DuckDB struct {
	// Path is the database file all workers attach to ("" = private
	// in-memory catalog per worker; tests seed via Boot).
	Path string
	// ReadOnly opens workers with access_mode=read_only (defense in depth
	// under the AST classifier; requires Path).
	ReadOnly bool
	// MemoryLimit is the per-worker cap (BR-7), default "2GB". Server-owned
	// constant — never derived from user input.
	MemoryLimit string
	// Threads per worker, default 2.
	Threads int
	// Boot optionally prepares each fresh worker (test seeding).
	Boot func(ctx context.Context, db *sql.DB) error

	// S3* configure the httpfs reader used to materialize dataset URIs
	// (QRY-FR-005). They are server-owned config, never user input. When
	// unset, materialization of s3:// URIs will fail — but the whole path is
	// inert unless a Query carries Tables, so prod behavior is unchanged when
	// auto-materialization is disabled.
	S3Endpoint  string // host:port, e.g. localhost:9000 (no scheme)
	S3Region    string // e.g. us-east-1
	S3AccessKey string
	S3SecretKey string
	// S3UseSSL selects https for the endpoint (default false for local MinIO).
	S3UseSSL bool
}

func (d *DuckDB) Name() string { return NameDuckDB }

// Healthy: DuckDB is in-process; it is available whenever the service is.
func (d *DuckDB) Healthy(ctx context.Context) bool {
	return true
}

func (d *DuckDB) dsn() string {
	if d.Path == "" {
		return ""
	}
	if d.ReadOnly {
		return d.Path + "?access_mode=read_only"
	}
	return d.Path
}

// Execute runs one prepared, parameterized statement on a fresh worker.
// Values arrive exclusively through driver-level bindings (QRY-FR-003):
// q.SQL contains only $n placeholders and q.Args the values.
func (d *DuckDB) Execute(ctx context.Context, q Query, sink Sink) (Stats, error) {
	db, err := sql.Open("duckdb", d.dsn())
	if err != nil {
		return Stats{}, fmt.Errorf("duckdb open: %w", err)
	}
	// One in-flight query per worker; worker recycled after use (BR-7).
	db.SetMaxOpenConns(1)
	defer db.Close()

	limit := d.MemoryLimit
	if limit == "" {
		limit = "2GB"
	}
	threads := d.Threads
	if threads <= 0 {
		threads = 2
	}
	if _, err := db.ExecContext(ctx, fmt.Sprintf("SET memory_limit = '%s'; SET threads = %d;", limit, threads)); err != nil {
		return Stats{}, fmt.Errorf("duckdb worker setup: %w", err)
	}

	// Materialize referenced datasets into this worker's private catalog
	// before the user SQL runs (QRY-FR-005). Only reached when the plan
	// carried Tables, so the httpfs/S3 setup is entirely inert for queries
	// that read physical tables directly.
	if len(q.Tables) > 0 {
		if err := d.materialize(ctx, db, q.Tables); err != nil {
			return Stats{}, err
		}
	}

	if d.Boot != nil {
		if err := d.Boot(ctx, db); err != nil {
			return Stats{}, fmt.Errorf("duckdb worker boot: %w", err)
		}
	}

	// Prepared statement: placeholders stay placeholders all the way to the
	// engine (AC-1 asserts this via the engine spy in unit tests).
	stmt, err := db.PrepareContext(ctx, q.SQL)
	if err != nil {
		return Stats{}, fmt.Errorf("duckdb prepare: %w", err)
	}
	defer stmt.Close()
	rows, err := stmt.QueryContext(ctx, q.Args...)
	if err != nil {
		if ctx.Err() != nil {
			return Stats{}, ctx.Err()
		}
		return Stats{}, fmt.Errorf("duckdb query: %w", err)
	}
	defer rows.Close()

	colTypes, err := rows.ColumnTypes()
	if err != nil {
		return Stats{}, err
	}
	cols := make([]Column, len(colTypes))
	for i, ct := range colTypes {
		cols[i] = Column{Name: ct.Name(), Type: logicalType(ct.DatabaseTypeName())}
	}
	if err := sink.Start(cols); err != nil {
		return Stats{}, err
	}

	var stats Stats
	vals := make([]any, len(cols))
	ptrs := make([]any, len(cols))
	for i := range vals {
		ptrs[i] = &vals[i]
	}
	for rows.Next() {
		if err := rows.Scan(ptrs...); err != nil {
			return stats, err
		}
		row := make([]any, len(vals))
		for i, v := range vals {
			row[i] = canonicalValue(v)
		}
		if err := sink.Row(row); err != nil {
			return stats, err
		}
		stats.Rows++
	}
	if err := rows.Err(); err != nil {
		if ctx.Err() != nil {
			return stats, ctx.Err()
		}
		return stats, err
	}
	return stats, nil
}

// materialize loads httpfs, configures the S3 reader, and (re)creates each
// requested dataset as a table in the worker's private catalog (QRY-FR-005).
// Runs at most once per worker (one Execute == one worker), so INSTALL/LOAD
// httpfs happens exactly once per query. Idents are engine-quoted by the
// resolver (BR-1); URIs are single-quote-escaped defensively even though they
// too are server-supplied.
func (d *DuckDB) materialize(ctx context.Context, db *sql.DB, tables []TableSource) error {
	ssl := "false"
	if d.S3UseSSL {
		ssl = "true"
	}
	// INSTALL once process-wide (serialized) to avoid the concurrent extension-dir
	// race; LOAD is per-worker and safe to run concurrently.
	if err := ensureHTTPFSInstalled(ctx, db); err != nil {
		return fmt.Errorf("duckdb install httpfs: %w", err)
	}
	setup := []string{
		"LOAD httpfs",
		fmt.Sprintf("SET s3_endpoint=%s", sqlString(d.S3Endpoint)),
		"SET s3_use_ssl=" + ssl,
		"SET s3_url_style='path'",
		fmt.Sprintf("SET s3_region=%s", sqlString(d.S3Region)),
		fmt.Sprintf("SET s3_access_key_id=%s", sqlString(d.S3AccessKey)),
		fmt.Sprintf("SET s3_secret_access_key=%s", sqlString(d.S3SecretKey)),
	}
	for _, s := range setup {
		if _, err := db.ExecContext(ctx, s); err != nil {
			return fmt.Errorf("duckdb httpfs setup (%s): %w", firstToken(s), err)
		}
	}
	for _, t := range tables {
		if len(t.URIs) == 0 {
			continue
		}
		if schema := schemaOf(t.Ident); schema != "" {
			if _, err := db.ExecContext(ctx, "CREATE SCHEMA IF NOT EXISTS "+schema); err != nil {
				return fmt.Errorf("duckdb create schema for %s: %w", t.Ident, err)
			}
		}
		quoted := make([]string, len(t.URIs))
		for i, u := range t.URIs {
			quoted[i] = sqlString(u)
		}
		list := "[" + strings.Join(quoted, ", ") + "]"
		stmt := fmt.Sprintf("CREATE OR REPLACE TABLE %s AS SELECT * FROM read_parquet(%s)", t.Ident, list)
		if _, err := db.ExecContext(ctx, stmt); err != nil {
			return fmt.Errorf("duckdb materialize %s: %w", t.Ident, err)
		}
	}
	return nil
}

// sqlString renders a single-quoted SQL string literal with embedded quotes
// doubled.
func sqlString(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "''") + "'"
}

// firstToken returns the leading word of a statement (for error context).
func firstToken(s string) string {
	if i := strings.IndexByte(s, ' '); i > 0 {
		return s[:i]
	}
	return s
}

// schemaOf extracts the engine-quoted schema prefix from an engine-quoted
// identifier such as `"main"."claims"` (-> `"main"`). Returns "" for an
// unqualified identifier.
func schemaOf(ident string) string {
	if i := strings.LastIndex(ident, `".`); i >= 0 {
		return ident[:i+1]
	}
	return ""
}

// logicalType maps DuckDB type names to the platform's edge type vocabulary
// (QRY-FR-063).
func logicalType(dbType string) string {
	t := strings.ToUpper(dbType)
	switch {
	case strings.HasPrefix(t, "DECIMAL"), t == "NUMERIC":
		return "decimal"
	case t == "TINYINT", t == "SMALLINT", t == "INTEGER", t == "INT":
		return "integer"
	case t == "BIGINT", t == "HUGEINT", t == "UBIGINT":
		return "bigint"
	case t == "FLOAT", t == "REAL", t == "DOUBLE":
		return "float"
	case t == "BOOLEAN":
		return "boolean"
	case t == "DATE":
		return "date"
	case strings.HasPrefix(t, "TIMESTAMP"):
		return "timestamp"
	case t == "BLOB", t == "BYTEA":
		return "binary"
	case strings.HasSuffix(t, "[]") || strings.HasPrefix(t, "LIST"):
		return "list"
	case strings.HasPrefix(t, "STRUCT"), strings.HasPrefix(t, "MAP"):
		return "struct"
	default:
		return "string"
	}
}

// canonicalValue converts driver-specific values into the canonical set the
// results layer understands (bool, int64, float64, string, []byte,
// time.Time, []any, map[string]any, nil).
func canonicalValue(v any) any {
	switch t := v.(type) {
	case duckdb.Decimal:
		return decimalString(t)
	case *big.Int:
		if t.IsInt64() {
			return t.Int64()
		}
		return t.String()
	case big.Int:
		if t.IsInt64() {
			return t.Int64()
		}
		return t.String()
	case int:
		return int64(t)
	case int8:
		return int64(t)
	case int16:
		return int64(t)
	case int32:
		return int64(t)
	case uint8:
		return int64(t)
	case uint16:
		return int64(t)
	case uint32:
		return int64(t)
	case uint64:
		if t > math.MaxInt64 {
			return fmt.Sprintf("%d", t)
		}
		return int64(t)
	case float32:
		return float64(t)
	case []any:
		out := make([]any, len(t))
		for i, el := range t {
			out[i] = canonicalValue(el)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, el := range t {
			out[k] = canonicalValue(el)
		}
		return out
	default:
		return v
	}
}

// decimalString renders a DuckDB decimal losslessly (QRY-FR-063: decimals
// travel as strings, no float rounding).
func decimalString(d duckdb.Decimal) string {
	v := new(big.Int).Set(d.Value)
	neg := v.Sign() < 0
	if neg {
		v.Neg(v)
	}
	s := v.String()
	scale := int(d.Scale)
	if scale > 0 {
		for len(s) <= scale {
			s = "0" + s
		}
		s = s[:len(s)-scale] + "." + s[len(s)-scale:]
	}
	if neg {
		s = "-" + s
	}
	return s
}
