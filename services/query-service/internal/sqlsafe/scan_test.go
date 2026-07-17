package sqlsafe

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/domain"
)

func TestScanPlaceholders(t *testing.T) {
	tests := []struct {
		name string
		sql  string
		want []string
	}{
		{"simple", "SELECT * FROM t WHERE a = :region", []string{"region"}},
		{"multiple", "SELECT * FROM t WHERE a = :region AND b >= :since", []string{"region", "since"}},
		{"repeated dedup", "SELECT :x, :x, :y", []string{"x", "y"}},
		{"inside string ignored", "SELECT ':nope' , :real FROM t", []string{"real"}},
		{"inside line comment ignored", "SELECT :a -- :nope\nFROM t", []string{"a"}},
		{"inside block comment ignored", "SELECT /* :nope */ :a FROM t", []string{"a"}},
		{"nested block comment", "SELECT /* outer /* :inner */ still */ :a", []string{"a"}},
		{"cast not placeholder", "SELECT a::text, :b FROM t", []string{"b"}},
		{"double-quoted ident ignored", `SELECT ":nope", :a FROM t`, []string{"a"}},
		{"escaped quote in string", "SELECT 'it''s :not', :a", []string{"a"}},
		{"dollar-quoted ignored", "SELECT $$ :nope $$, :a", []string{"a"}},
		{"array slice untouched", "SELECT arr[1:2], :a FROM t", []string{"a"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := PlaceholderNames(tc.sql)
			require.NoError(t, err)
			assert.Equal(t, tc.want, got)
		})
	}
}

// The legacy {var} syntax is rejected at save time with a migration hint
// (QRY-FR-002).
func TestScanRejectsLegacyVarSyntax(t *testing.T) {
	_, err := PlaceholderNames("SELECT * FROM t WHERE region = {region}")
	require.Error(t, err)
	de, ok := domain.AsError(err)
	require.True(t, ok)
	assert.Equal(t, domain.CodeValidationFailed, de.Code)
	details, ok := de.Details.(map[string]string)
	require.True(t, ok)
	assert.Contains(t, details["hint"], ":region")
}

// Raw positional parameters are rejected: the service owns the positional
// numbering space (QRY-FR-003).
func TestScanRejectsPositionalParams(t *testing.T) {
	for _, sql := range []string{"SELECT * FROM t WHERE a = $1", "SELECT * FROM t WHERE a = ?"} {
		_, err := PlaceholderNames(sql)
		require.Error(t, err, sql)
	}
}

func TestScanDatasetRefs(t *testing.T) {
	refs, err := DatasetRefs("SELECT * FROM {{dataset('Orders')}} o JOIN {{ dataset('Customers', version=7) }} c ON o.cid=c.id")
	require.NoError(t, err)
	require.Len(t, refs, 2)
	assert.Equal(t, domain.DatasetRef{Name: "Orders"}, refs[0])
	assert.Equal(t, domain.DatasetRef{Name: "Customers", Version: 7}, refs[1])
}

func TestScanDatasetRefMalformed(t *testing.T) {
	for _, sql := range []string{
		"SELECT * FROM {{dataset(Orders)}}",
		"SELECT * FROM {{dataset('Orders', version=x)}}",
		"SELECT * FROM {{table('Orders')}}",
		"SELECT * FROM {{dataset('Orders')",
	} {
		_, err := DatasetRefs(sql)
		require.Error(t, err, sql)
	}
}

func TestRewriteBindsEveryOccurrence(t *testing.T) {
	bindings := map[string]domain.BoundValue{
		"region": {Name: "region", Type: domain.VarString, Value: "EMEA"},
		"since":  {Name: "since", Type: domain.VarString, Value: "2026-01-01"},
	}
	rw, err := Rewrite("SELECT * FROM t WHERE r = :region AND s >= :since AND r2 = :region", bindings, nil)
	require.NoError(t, err)
	assert.Equal(t, "SELECT * FROM t WHERE r = $1 AND s >= $2 AND r2 = $1", rw.SQL)
	assert.Equal(t, []any{"EMEA", "2026-01-01"}, rw.Args)
	assert.Equal(t, []string{"region", "since"}, rw.ParamNames)
}

// process_vars_multi_variable (AC-1): the V1 defect substituted only the
// FIRST variable, leaving later {var} placeholders raw in shipped SQL. Here
// every variable must be bound; none may survive as raw text.
func TestRewrite_process_vars_multi_variable(t *testing.T) {
	bindings := map[string]domain.BoundValue{
		"a": {Name: "a", Type: domain.VarString, Value: "v1"},
		"b": {Name: "b", Type: domain.VarString, Value: "v2"},
		"c": {Name: "c", Type: domain.VarString, Value: "v3"},
	}
	rw, err := Rewrite("SELECT :a, :b, :c", bindings, nil)
	require.NoError(t, err)
	assert.Equal(t, "SELECT $1, $2, $3", rw.SQL)
	assert.Len(t, rw.Args, 3, "ALL variables must be bound, not just the first")
	assert.NotContains(t, rw.SQL, ":a")
	assert.NotContains(t, rw.SQL, ":b")
	assert.NotContains(t, rw.SQL, ":c")
}

// No string splicing, ever (QRY-FR-003/BR-1): malicious values must never
// appear in the SQL text — they travel only in Args.
func TestRewriteNeverSplicesValues(t *testing.T) {
	payloads := []string{
		`x' OR '1'='1`,
		`x'; DROP TABLE users;--`,
		`$1); DELETE FROM t; --`,
		`{{dataset('Orders')}}`,
		`:other`,
		`--`,
		`'; SHUTDOWN; --`,
	}
	for _, p := range payloads {
		bindings := map[string]domain.BoundValue{
			"v": {Name: "v", Type: domain.VarString, Value: p},
		}
		rw, err := Rewrite("SELECT * FROM t WHERE c = :v", bindings, nil)
		require.NoError(t, err)
		assert.Equal(t, "SELECT * FROM t WHERE c = $1", rw.SQL,
			"SQL text must be identical regardless of the value %q", p)
		assert.Equal(t, []any{p}, rw.Args, "value must round-trip as data")
	}
}

func TestRewriteListExpansion(t *testing.T) {
	bindings := map[string]domain.BoundValue{
		"ids":    {Name: "ids", Type: domain.VarIntegerList, IsList: true, List: []any{int64(1), int64(2), int64(3)}},
		"region": {Name: "region", Type: domain.VarString, Value: "EMEA"},
	}
	rw, err := Rewrite("SELECT * FROM t WHERE id IN :ids AND r = :region AND id2 IN :ids", bindings, nil)
	require.NoError(t, err)
	assert.Equal(t, "SELECT * FROM t WHERE id IN ($1,$2,$3) AND r = $4 AND id2 IN ($1,$2,$3)", rw.SQL)
	assert.Equal(t, []any{int64(1), int64(2), int64(3), "EMEA"}, rw.Args)
}

func TestRewriteEmptyListMatchesNothing(t *testing.T) {
	bindings := map[string]domain.BoundValue{
		"ids": {Name: "ids", Type: domain.VarIntegerList, IsList: true, List: []any{}},
	}
	rw, err := Rewrite("SELECT * FROM t WHERE id IN :ids", bindings, nil)
	require.NoError(t, err)
	assert.Equal(t, "SELECT * FROM t WHERE id IN (NULL)", rw.SQL)
	assert.Empty(t, rw.Args)
}

func TestRewriteUnboundPlaceholder(t *testing.T) {
	_, err := Rewrite("SELECT :missing", map[string]domain.BoundValue{}, nil)
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeVariableInvalid, de.Code)
}

func TestRewriteDatasetIdentifiers(t *testing.T) {
	idents := map[string]string{"Orders@0": `"bronze_t42"."orders_v3"`}
	rw, err := Rewrite("SELECT count(*) FROM {{dataset('Orders')}}", nil, idents)
	require.NoError(t, err)
	assert.Equal(t, `SELECT count(*) FROM "bronze_t42"."orders_v3"`, rw.SQL)
}

func TestRewriteUnresolvedDatasetRef(t *testing.T) {
	_, err := Rewrite("SELECT 1 FROM {{dataset('Nope')}}", nil, map[string]string{})
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeDatasetNotFound, de.Code)
}

// Static check companion (QRY-FR-003): fuzz-style corpus proving the
// rewrite output never contains the raw value bytes for any declared type.
func TestRewriteFuzzCorpusValuesInert(t *testing.T) {
	corpus := []string{
		`'; DROP TABLE users; --`,
		`" OR ""=""`,
		`\'; EXEC xp_cmdshell('rm -rf /'); --`,
		"Robert'); DROP TABLE Students;--",
		`0x27 UNION SELECT password FROM users`,
		"a\x00b",
		strings.Repeat("'", 1000),
	}
	for _, p := range corpus {
		bindings := map[string]domain.BoundValue{"v": {Name: "v", Type: domain.VarString, Value: p}}
		rw, err := Rewrite("SELECT * FROM t WHERE c = :v", bindings, nil)
		require.NoError(t, err)
		require.Equal(t, "SELECT * FROM t WHERE c = $1", rw.SQL)
	}
}
