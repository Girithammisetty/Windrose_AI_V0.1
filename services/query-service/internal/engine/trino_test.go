package engine

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestTrinoize(t *testing.T) {
	t.Run("simple positional", func(t *testing.T) {
		sqlText, args, err := trinoize(`SELECT * FROM t WHERE a = $1 AND b = $2`, []any{"x", 2})
		require.NoError(t, err)
		assert.Equal(t, `SELECT * FROM t WHERE a = ? AND b = ?`, sqlText)
		assert.Equal(t, []any{"x", 2}, args)
	})

	t.Run("repeated placeholder expands args (occurrence-based ? semantics)", func(t *testing.T) {
		// The resolver reuses the same $n for a repeated :name binding
		// (sqlsafe/rewrite.go) — Trino's ? has no notion of "the same
		// parameter twice", so the value must repeat once per occurrence.
		sqlText, args, err := trinoize(`WHERE start_date <= $1 AND end_date >= $1`, []any{"2026-01-01"})
		require.NoError(t, err)
		assert.Equal(t, `WHERE start_date <= ? AND end_date >= ?`, sqlText)
		assert.Equal(t, []any{"2026-01-01", "2026-01-01"}, args)
	})

	t.Run("no placeholders", func(t *testing.T) {
		sqlText, args, err := trinoize(`SELECT 1`, nil)
		require.NoError(t, err)
		assert.Equal(t, `SELECT 1`, sqlText)
		assert.Empty(t, args)
	})

	t.Run("out of range placeholder errors rather than silently truncating", func(t *testing.T) {
		_, _, err := trinoize(`SELECT * FROM t WHERE a = $2`, []any{"only one arg"})
		require.Error(t, err)
	})
}

func TestTrinoLogicalType(t *testing.T) {
	cases := map[string]string{
		"DECIMAL(10,2)":            "decimal",
		"TINYINT":                  "integer",
		"SMALLINT":                 "integer",
		"INTEGER":                  "integer",
		"BIGINT":                   "bigint",
		"REAL":                     "float",
		"DOUBLE":                   "float",
		"BOOLEAN":                  "boolean",
		"DATE":                     "date",
		"TIMESTAMP(3)":             "timestamp",
		"TIMESTAMP WITH TIME ZONE": "timestamp",
		"TIME":                     "timestamp",
		"VARBINARY":                "binary",
		"ARRAY":                    "list",
		"MAP":                      "struct",
		"ROW":                      "struct",
		"VARCHAR":                  "string",
		"UUID":                     "string",
	}
	for in, want := range cases {
		assert.Equal(t, want, trinoLogicalType(in), "type %s", in)
	}
}

func TestTrinoDSN(t *testing.T) {
	t.Run("empty endpoint errors", func(t *testing.T) {
		tr := &Trino{}
		_, err := tr.dsn()
		require.Error(t, err)
	})

	t.Run("sets user/catalog/source query params", func(t *testing.T) {
		tr := &Trino{Endpoint: "http://localhost:8080", User: "windrose", Catalog: "iceberg", Source: "query-service"}
		dsn, err := tr.dsn()
		require.NoError(t, err)
		assert.Contains(t, dsn, "windrose@localhost:8080")
		assert.Contains(t, dsn, "catalog=iceberg")
		assert.Contains(t, dsn, "source=query-service")
	})

	t.Run("defaults user when unset", func(t *testing.T) {
		tr := &Trino{Endpoint: "http://localhost:8080"}
		dsn, err := tr.dsn()
		require.NoError(t, err)
		assert.Contains(t, dsn, "windrose@localhost:8080")
	})
}

func TestTrinoHealthy(t *testing.T) {
	t.Run("unconfigured is unhealthy, no network call", func(t *testing.T) {
		tr := &Trino{}
		assert.False(t, tr.Healthy(context.Background()))
	})

	t.Run("probes /v1/info and caches within TTL", func(t *testing.T) {
		hits := 0
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			hits++
			assert.Equal(t, "/v1/info", r.URL.Path)
			w.WriteHeader(http.StatusOK)
		}))
		defer srv.Close()

		tr := &Trino{Endpoint: srv.URL, HealthCacheTTL: time.Hour}
		assert.True(t, tr.Healthy(context.Background()))
		assert.True(t, tr.Healthy(context.Background())) // cached, no second hit
		assert.Equal(t, 1, hits)
	})

	t.Run("non-200 is unhealthy", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusServiceUnavailable)
		}))
		defer srv.Close()

		tr := &Trino{Endpoint: srv.URL, HealthCacheTTL: time.Hour}
		assert.False(t, tr.Healthy(context.Background()))
	})
}

// TestTrinoLiveIcebergRead drives the real Trino cluster (task #66: direct
// Iceberg-REST read, no per-execution materialization — unlike DuckDB, Query
// carries no Tables here) against the docker-compose trino+iceberg-rest+minio
// stack. Gated behind QUERY_TRINO_E2E=1 so `go test ./...` stays green with
// no infra:
//
//	QUERY_TRINO_E2E=1 go test ./internal/engine/ -run TestTrinoLiveIcebergRead -v
func TestTrinoLiveIcebergRead(t *testing.T) {
	if os.Getenv("QUERY_TRINO_E2E") != "1" {
		t.Skip("set QUERY_TRINO_E2E=1 to run the live Trino/Iceberg proof")
	}
	tr := &Trino{
		Endpoint: env("TRINO_ENDPOINT", "http://localhost:8080"),
		Catalog:  env("TRINO_CATALOG", "iceberg"),
	}
	require.Eventually(t, func() bool {
		return tr.Healthy(context.Background())
	}, 60*time.Second, 2*time.Second, "trino did not become healthy")

	sink := &collectSink{}
	_, err := tr.Execute(context.Background(), Query{SQL: `SELECT 1 AS n WHERE 1 = $1`, Args: []any{1}}, sink)
	require.NoError(t, err)
	require.Len(t, sink.rows, 1)
	assert.EqualValues(t, 1, sink.rows[0][0])
}
