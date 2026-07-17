package sqlsafe

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/domain"
)

func requireStatementNotAllowed(t *testing.T, sql string) {
	t.Helper()
	_, err := Classify(sql)
	require.Error(t, err, "must reject: %s", sql)
	de, ok := domain.AsError(err)
	require.True(t, ok, "unexpected error type for %q: %v", sql, err)
	assert.Equal(t, domain.CodeStatementNotAllowed, de.Code, "sql: %s → %s", sql, de.Message)
	assert.Equal(t, 403, de.HTTP)
}

func TestClassifyAllowsSelects(t *testing.T) {
	allowed := []string{
		"SELECT 1",
		"SELECT a, b FROM s.t WHERE a = $1 GROUP BY 1 HAVING count(*) > 2 ORDER BY 1 LIMIT 5",
		"WITH x AS (SELECT 1 AS n) SELECT * FROM x",
		"WITH RECURSIVE r AS (SELECT 1 n UNION ALL SELECT n+1 FROM r WHERE n < 5) SELECT * FROM r",
		"SELECT * FROM s.a UNION SELECT * FROM s.b",
		"SELECT * FROM s.a INTERSECT SELECT * FROM s.b",
		"(SELECT 1) EXCEPT (SELECT 2)",
		"SELECT (SELECT max(x) FROM s.t2) FROM s.t1",
		"SELECT * FROM s.t -- trailing comment",
		"/* leading comment */ SELECT 1",
		"SELECT 1;", // single trailing semicolon is still one statement
	}
	for _, sql := range allowed {
		_, err := Classify(sql)
		require.NoError(t, err, "must allow: %s", sql)
	}
}

// AC-3 + the regex-bypass corpus: everything V1's `delete|insert|update`
// regex missed (or mis-blocked) must be decided correctly by the AST.
func TestClassifyRejectsWriteStatements(t *testing.T) {
	rejected := []string{
		// plain DML/DDL
		"DELETE FROM t",
		"INSERT INTO t VALUES (1)",
		"UPDATE t SET a = 1",
		"MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET a=1",
		"CREATE TABLE t (a int)",
		"DROP TABLE t",
		"ALTER TABLE t ADD COLUMN b int",
		"GRANT SELECT ON t TO PUBLIC",
		"CALL do_thing()",
		"SET search_path = public",
		"TRUNCATE t",
		"COPY t FROM '/etc/passwd'",
		// EXPLAIN and EXPLAIN ANALYZE (the latter executes!)
		"EXPLAIN SELECT 1",
		"EXPLAIN ANALYZE SELECT * FROM t",
		// case obfuscation — trivially bypassed V1's regex, inert here
		"dElEtE FROM t",
		"DeLeTe/**/FROM t",
		// comment tricks
		"DELETE /* just reading, promise */ FROM t",
		"--\nDROP TABLE t",
		// multi-statement batches
		"SELECT 1; DELETE FROM t",
		"select 1;delete from t;",
		"SELECT 1; SELECT 2",
		// CTE-wrapped DML: parses as a SelectStmt at the top — the walk
		// must find the DeleteStmt/InsertStmt inside.
		"WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
		"WITH i AS (INSERT INTO t VALUES (1) RETURNING id) SELECT * FROM i",
		"WITH u AS (UPDATE t SET a=1 RETURNING *) SELECT count(*) FROM u",
		// SELECT ... INTO creates a table
		"SELECT * INTO new_table FROM t",
		"SELECT a INTO TEMP tmp_t FROM t",
		// locking clauses take locks
		"SELECT * FROM t FOR UPDATE",
		"SELECT * FROM t FOR SHARE",
		// transaction control
		"BEGIN",
		"COMMIT",
		// CREATE TABLE AS
		"CREATE TABLE snap AS SELECT * FROM t",
	}
	for _, sql := range rejected {
		requireStatementNotAllowed(t, sql)
	}
}

func TestClassifyParseErrorIs422(t *testing.T) {
	_, err := Classify("SELEC 1")
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeValidationFailed, de.Code)
	assert.Equal(t, 422, de.HTTP)

	_, err = Classify("   ")
	require.Error(t, err)
}

func TestClassifyCollectsTablesAndCTEs(t *testing.T) {
	cls, err := Classify(`WITH cte1 AS (SELECT * FROM "bronze_t42"."orders") SELECT * FROM cte1 JOIN silver_t42.customers c ON true`)
	require.NoError(t, err)
	assert.True(t, cls.CTENames["cte1"])
	var names []string
	for _, tr := range cls.Tables {
		names = append(names, tr.String())
	}
	assert.Contains(t, names, "bronze_t42.orders")
	assert.Contains(t, names, "silver_t42.customers")
	assert.Contains(t, names, "cte1")
}

func TestClassifyOuterLimitDetection(t *testing.T) {
	cls, err := Classify("SELECT * FROM s.t LIMIT 10")
	require.NoError(t, err)
	assert.True(t, cls.HasOuterLimit)

	cls, err = Classify("SELECT * FROM (SELECT * FROM s.t LIMIT 10) sub")
	require.NoError(t, err)
	assert.False(t, cls.HasOuterLimit, "inner LIMIT is not an outer LIMIT")

	cls, err = Classify("SELECT * FROM s.t FETCH FIRST 5 ROWS ONLY")
	require.NoError(t, err)
	assert.True(t, cls.HasOuterLimit)
}

func TestWrapWithLimit(t *testing.T) {
	wrapped := WrapWithLimit("SELECT * FROM s.t ORDER BY a;", 10000)
	cls, err := Classify(wrapped)
	require.NoError(t, err)
	assert.True(t, cls.HasOuterLimit)
	assert.Contains(t, wrapped, "LIMIT 10000")
}

func TestClassifyParamColumns(t *testing.T) {
	cls, err := Classify("SELECT * FROM s.t WHERE email = $1 AND region = $2 AND $3 < age")
	require.NoError(t, err)
	assert.Equal(t, "email", cls.ParamColumns[1])
	assert.Equal(t, "region", cls.ParamColumns[2])
	assert.Equal(t, "age", cls.ParamColumns[3])
}

func TestClassifyFingerprintStable(t *testing.T) {
	a, err := Classify("SELECT * FROM s.t WHERE a = $1")
	require.NoError(t, err)
	b, err := Classify("select *  from s.t where a = $1")
	require.NoError(t, err)
	assert.NotEmpty(t, a.Fingerprint)
	assert.Equal(t, a.Fingerprint, b.Fingerprint, "fingerprint normalizes whitespace/case")
}

// ---- Tenant namespace guard (QRY-FR-021, BR-2) -------------------------------

func guardCfg() GuardConfig {
	return GuardConfig{
		AllowedNamespaces: map[string]bool{"bronze_t42": true, "silver_t42": true},
		InfoSchemaTables:  DefaultInfoSchemaTables(),
	}
}

func TestGuardAllowsTenantNamespaces(t *testing.T) {
	cls, err := Classify("SELECT * FROM bronze_t42.orders JOIN silver_t42.customers ON true")
	require.NoError(t, err)
	require.NoError(t, Guard(cls, guardCfg()))
}

func TestGuardAllowsCTEReferences(t *testing.T) {
	cls, err := Classify("WITH x AS (SELECT * FROM bronze_t42.orders) SELECT * FROM x")
	require.NoError(t, err)
	require.NoError(t, Guard(cls, guardCfg()))
}

func TestGuardRejectsForeignNamespaces(t *testing.T) {
	for _, sql := range []string{
		"SELECT * FROM bronze_t99.orders",              // another tenant's namespace (BR-2)
		"SELECT * FROM orders",                         // unqualified physical table
		"SELECT * FROM pg_catalog.pg_tables",           // system catalog
		"SELECT * FROM pg_shadow",                      // unqualified system rel is unqualified → rejected
		"SELECT * FROM information_schema.role_grants", // outside the whitelist
	} {
		cls, err := Classify(sql)
		require.NoError(t, err, sql)
		err = Guard(cls, guardCfg())
		require.Error(t, err, sql)
		de, _ := domain.AsError(err)
		assert.Equal(t, domain.CodeStatementNotAllowed, de.Code, sql)
	}
}

func TestGuardAllowsInfoSchemaSubset(t *testing.T) {
	cls, err := Classify("SELECT table_name FROM information_schema.tables")
	require.NoError(t, err)
	require.NoError(t, Guard(cls, guardCfg()))
}
