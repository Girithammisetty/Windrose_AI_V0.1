package engine

import (
	"context"
	"os"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestDuckDBMaterializeMinIO drives the real materialization path (QRY-FR-005):
// DuckDB.Execute is given a Query with Tables, so it INSTALL/LOADs httpfs,
// configures the S3 reader, CREATE OR REPLACE TABLEs the dataset from the real
// claims parquet in MinIO, then runs the semantic-style chart SQL over the
// materialized table. It asserts the three claim_type groups (auto/property/
// health) totalling 14 rows.
//
// Gated behind QUERY_MINIO_E2E=1 so the default `go test ./...` stays green
// with no infra; run with the env set for the live proof:
//
//	QUERY_MINIO_E2E=1 CGO_ENABLED=1 go test ./internal/engine/ \
//	    -run TestDuckDBMaterializeMinIO -v
func TestDuckDBMaterializeMinIO(t *testing.T) {
	if os.Getenv("QUERY_MINIO_E2E") != "1" {
		t.Skip("set QUERY_MINIO_E2E=1 to run the live MinIO materialization proof")
	}
	uri := env("QUERY_MINIO_URI",
		"s3://windrose-warehouse/bronze.019f5035-3029-725d-a26a-96ff36fd8be5/ds_019f5035-ea52-71dc-89e7-a3a1bd200361/data/00000-0-3caea68c-3f02-4394-8ba7-f97fa492add7.parquet")

	d := &DuckDB{
		S3Endpoint:  env("S3_ENDPOINT", "localhost:9000"),
		S3Region:    env("AWS_REGION", "us-east-1"),
		S3AccessKey: env("AWS_ACCESS_KEY_ID", "windrose"),
		S3SecretKey: env("AWS_SECRET_ACCESS_KEY", "windrose_dev"),
	}

	sink := &collectSink{}
	_, err := d.Execute(context.Background(), Query{
		SQL: `SELECT claim_type, count(*) AS n FROM "main"."claims" GROUP BY 1 ORDER BY 1`,
		Tables: []TableSource{{
			Ident:  `"main"."claims"`,
			URIs:   []string{uri},
			Format: "parquet",
		}},
	}, sink)
	require.NoError(t, err)

	got := map[string]int64{}
	var total int64
	for _, r := range sink.rows {
		ct, _ := r[0].(string)
		n, _ := r[1].(int64)
		got[ct] = n
		total += n
		t.Logf("group %-10s n=%d", ct, n)
	}
	t.Logf("TOTAL rows=%d groups=%d", total, len(sink.rows))

	require.Len(t, sink.rows, 3, "expected 3 claim_type groups")
	assert.Equal(t, int64(14), total, "expected 14 total rows")
	assert.Equal(t, int64(9), got["auto"])
	assert.Equal(t, int64(3), got["property"])
	assert.Equal(t, int64(2), got["health"])
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
