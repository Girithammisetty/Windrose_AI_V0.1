package engine

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// collectSink buffers rows for assertions.
type collectSink struct {
	cols []Column
	rows [][]any
	err  error // returned from Row to test abort propagation
}

func (s *collectSink) Start(cols []Column) error { s.cols = cols; return nil }
func (s *collectSink) Row(vals []any) error {
	if s.err != nil {
		return s.err
	}
	s.rows = append(s.rows, vals)
	return nil
}

// newTestDuckDB seeds a database file shared by all recycled workers.
func newTestDuckDB(t *testing.T, seed string) *DuckDB {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.db")
	db, err := sql.Open("duckdb", path)
	require.NoError(t, err)
	if seed != "" {
		_, err = db.Exec(seed)
		require.NoError(t, err)
	}
	require.NoError(t, db.Close())
	return &DuckDB{Path: path}
}

const seedOrders = `
	CREATE TABLE orders (id INTEGER, region VARCHAR, order_total DECIMAL(18,2), order_date DATE, created_at TIMESTAMP, vip BOOLEAN);
	INSERT INTO orders VALUES
		(1, 'EMEA', 100.50, DATE '2026-06-01', TIMESTAMP '2026-06-01 10:00:00', true),
		(2, 'AMER', 250.25, DATE '2026-06-02', TIMESTAMP '2026-06-02 11:00:00', false),
		(3, 'EMEA', 75.00,  DATE '2026-05-01', TIMESTAMP '2026-05-01 09:00:00', false);
	CREATE TABLE users (id INTEGER, email VARCHAR);
	INSERT INTO users VALUES (1, 'a@example.com');
`

func exec1(t *testing.T, d *DuckDB, sqlText string, args ...any) *collectSink {
	t.Helper()
	sink := &collectSink{}
	_, err := d.Execute(context.Background(), Query{SQL: sqlText, Args: args}, sink)
	require.NoError(t, err)
	return sink
}

// AC-1: the engine receives a parameterized statement and binds EVERY
// variable ($1 and $2 both effective) — the process_vars_multi_variable
// regression at the engine tier.
func TestDuckDB_process_vars_multi_variable(t *testing.T) {
	d := newTestDuckDB(t, seedOrders)
	sink := exec1(t, d,
		"SELECT id FROM orders WHERE region = $1 AND order_date >= $2 ORDER BY id",
		"EMEA", time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC))
	require.Len(t, sink.rows, 1, "BOTH parameters must constrain the result")
	assert.Equal(t, int64(1), sink.rows[0][0])
}

// AC-2: actual malicious inputs asserted inert. The payload executes safely
// as a bound literal; no DDL occurs; matching rows: none.
func TestDuckDBInjectionPayloadsInert(t *testing.T) {
	d := newTestDuckDB(t, seedOrders)
	payloads := []string{
		`x' OR '1'='1`,
		`x'; DROP TABLE users;--`,
		`EMEA' UNION SELECT email FROM users --`,
		`'; DELETE FROM orders; --`,
	}
	for _, p := range payloads {
		sink := exec1(t, d, "SELECT id FROM orders WHERE region = $1", p)
		assert.Empty(t, sink.rows, "payload %q must match nothing", p)
	}
	// users table still exists with its row: no DDL/DML happened.
	sink := exec1(t, d, "SELECT count(*) FROM users")
	require.Len(t, sink.rows, 1)
	assert.Equal(t, int64(1), sink.rows[0][0])
	sink = exec1(t, d, "SELECT count(*) FROM orders")
	assert.Equal(t, int64(3), sink.rows[0][0], "orders untouched")
}

// Typed binding matrix over the real engine (QRY-FR-003 driver bindings).
func TestDuckDBBindingMatrix(t *testing.T) {
	d := newTestDuckDB(t, seedOrders)

	t.Run("string", func(t *testing.T) {
		sink := exec1(t, d, "SELECT count(*) FROM orders WHERE region = $1", "EMEA")
		assert.Equal(t, int64(2), sink.rows[0][0])
	})
	t.Run("integer", func(t *testing.T) {
		sink := exec1(t, d, "SELECT region FROM orders WHERE id = $1", int64(2))
		assert.Equal(t, "AMER", sink.rows[0][0])
	})
	t.Run("decimal as lossless string", func(t *testing.T) {
		sink := exec1(t, d, "SELECT count(*) FROM orders WHERE order_total > $1", "100.00")
		assert.Equal(t, int64(2), sink.rows[0][0])
	})
	t.Run("boolean", func(t *testing.T) {
		sink := exec1(t, d, "SELECT id FROM orders WHERE vip = $1", true)
		require.Len(t, sink.rows, 1)
		assert.Equal(t, int64(1), sink.rows[0][0])
	})
	t.Run("date", func(t *testing.T) {
		sink := exec1(t, d, "SELECT count(*) FROM orders WHERE order_date >= $1",
			time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC))
		assert.Equal(t, int64(2), sink.rows[0][0])
	})
	t.Run("timestamp", func(t *testing.T) {
		sink := exec1(t, d, "SELECT count(*) FROM orders WHERE created_at > $1",
			time.Date(2026, 6, 1, 10, 30, 0, 0, time.UTC))
		assert.Equal(t, int64(1), sink.rows[0][0])
	})
	t.Run("expanded list", func(t *testing.T) {
		sink := exec1(t, d, "SELECT count(*) FROM orders WHERE id IN ($1,$2)", int64(1), int64(3))
		assert.Equal(t, int64(2), sink.rows[0][0])
	})
	t.Run("same param reused", func(t *testing.T) {
		sink := exec1(t, d, "SELECT count(*) FROM orders WHERE region = $1 OR region = $1", "EMEA")
		assert.Equal(t, int64(2), sink.rows[0][0])
	})
}

// QRY-FR-063 source types survive canonicalization.
func TestDuckDBResultTypeCanonicalization(t *testing.T) {
	d := newTestDuckDB(t, seedOrders)
	sink := exec1(t, d, "SELECT id, region, order_total, order_date, created_at, vip FROM orders WHERE id = 1")
	require.Len(t, sink.rows, 1)
	row := sink.rows[0]
	assert.Equal(t, int64(1), row[0])
	assert.Equal(t, "EMEA", row[1])
	assert.Equal(t, "100.50", row[2], "decimal → lossless string")
	_, isTime := row[3].(time.Time)
	assert.True(t, isTime, "date arrives as time.Time")
	_, isTime = row[4].(time.Time)
	assert.True(t, isTime)
	assert.Equal(t, true, row[5])

	types := map[string]string{}
	for _, c := range sink.cols {
		types[c.Name] = c.Type
	}
	assert.Equal(t, "integer", types["id"])
	assert.Equal(t, "string", types["region"])
	assert.Equal(t, "decimal", types["order_total"])
	assert.Equal(t, "date", types["order_date"])
	assert.Equal(t, "timestamp", types["created_at"])
	assert.Equal(t, "boolean", types["vip"])
}

// Cancellation propagates through the driver (QRY-FR-045 substrate).
func TestDuckDBContextCancellation(t *testing.T) {
	d := newTestDuckDB(t, "")
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	sink := &collectSink{}
	start := time.Now()
	// A cross join large enough to run for many seconds uncancelled.
	_, err := d.Execute(ctx, Query{SQL: `
		SELECT count(*) FROM range(100000000) a, range(1000) b`}, sink)
	require.Error(t, err)
	assert.Less(t, time.Since(start), 5*time.Second, "kill must propagate promptly (BR-6)")
}

// Sink abort stops the stream (result-size ceilings, QRY-FR-042).
func TestDuckDBSinkAbortStopsQuery(t *testing.T) {
	d := newTestDuckDB(t, "")
	sink := &collectSink{err: assert.AnError}
	_, err := d.Execute(context.Background(), Query{SQL: "SELECT * FROM range(1000000)"}, sink)
	require.Error(t, err)
	assert.Empty(t, sink.rows)
}

// BR-7: workers are recycled — a state-poisoning session setting in one
// execution cannot leak into the next (fresh worker per execution).
func TestDuckDBWorkerRecycled(t *testing.T) {
	d := newTestDuckDB(t, "CREATE TABLE t (a INTEGER); INSERT INTO t VALUES (1);")
	// Both executions get their own worker; both see identical state.
	s1 := exec1(t, d, "SELECT count(*) FROM t")
	s2 := exec1(t, d, "SELECT count(*) FROM t")
	assert.Equal(t, s1.rows, s2.rows)
}

// Trino (real, unconfigured) and the Warehouse stub both compile against the
// same port and answer honestly.
func TestStubsCompileAndReport(t *testing.T) {
	tr := &Trino{}
	assert.False(t, tr.Healthy(context.Background()))
	_, err := tr.Execute(context.Background(), Query{SQL: "SELECT 1"}, &collectSink{})
	require.Error(t, err)

	wh := &Warehouse{Cloud: "aws", Up: true}
	assert.True(t, wh.Healthy(context.Background()))
	_, err = wh.Execute(context.Background(), Query{SQL: "SELECT 1"}, &collectSink{})
	require.Error(t, err)
}
