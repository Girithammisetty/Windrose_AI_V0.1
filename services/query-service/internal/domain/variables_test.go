package domain

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func raw(s string) json.RawMessage { return json.RawMessage(s) }

func decl(name string, typ VariableType) VariableDecl {
	return VariableDecl{Name: name, Type: typ}
}

func TestBindValuesTypeMatrix(t *testing.T) {
	f := false
	decls := []VariableDecl{
		decl("s", VarString),
		decl("i", VarInteger),
		decl("d", VarDecimal),
		decl("b", VarBoolean),
		decl("dt", VarDate),
		decl("ts", VarTimestamp),
		decl("sl", VarStringList),
		decl("il", VarIntegerList),
		{Name: "opt", Type: VarString, Required: &f},
	}
	vals := map[string]json.RawMessage{
		"s":  raw(`"hello"`),
		"i":  raw(`42`),
		"d":  raw(`12.50`),
		"b":  raw(`true`),
		"dt": raw(`"2026-06-01"`),
		"ts": raw(`"2026-06-01T12:30:00Z"`),
		"sl": raw(`["a","b"]`),
		"il": raw(`[1,2,3]`),
	}
	bound, err := BindValues(decls, vals)
	require.NoError(t, err)
	assert.Equal(t, "hello", bound["s"].Value)
	assert.Equal(t, int64(42), bound["i"].Value)
	assert.Equal(t, "12.50", bound["d"].Value, "decimals stay lossless strings")
	assert.Equal(t, true, bound["b"].Value)
	assert.Equal(t, time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC), bound["dt"].Value)
	assert.Equal(t, time.Date(2026, 6, 1, 12, 30, 0, 0, time.UTC), bound["ts"].Value)
	assert.Equal(t, []any{"a", "b"}, bound["sl"].List)
	assert.Equal(t, []any{int64(1), int64(2), int64(3)}, bound["il"].List)
	_, ok := bound["opt"]
	assert.False(t, ok, "optional without default and unsupplied stays unbound")
}

// BR-3: strict coercion.
func TestBindValuesStrictCoercion(t *testing.T) {
	cases := []struct {
		name string
		decl VariableDecl
		val  string
	}{
		{"non-ISO date", decl("v", VarDate), `"2026-6-1"`},
		{"date with time", decl("v", VarDate), `"2026-06-01T00:00:00Z"`},
		{"numeric string is not integer", decl("v", VarInteger), `"42"`},
		{"float is not integer", decl("v", VarInteger), `4.2`},
		{"bool string is not boolean", decl("v", VarBoolean), `"true"`},
		{"number is not string", decl("v", VarString), `42`},
		{"non-numeric decimal string", decl("v", VarDecimal), `"12,50"`},
		{"scalar for list", decl("v", VarStringList), `"a"`},
		{"mixed list", decl("v", VarIntegerList), `[1,"2"]`},
		{"garbage timestamp", decl("v", VarTimestamp), `"yesterday"`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := BindValues([]VariableDecl{tc.decl}, map[string]json.RawMessage{"v": raw(tc.val)})
			require.Error(t, err)
			de, _ := AsError(err)
			assert.Equal(t, CodeVariableInvalid, de.Code)
		})
	}
}

// Injection payloads are just data at this layer: they coerce as strings
// and are never rejected nor mutated (AC-2 pre-bind stage).
func TestBindValuesInjectionPayloadsAreData(t *testing.T) {
	payloads := []string{
		`x' OR '1'='1`,
		`x'; DROP TABLE users;--`,
		`"; DELETE FROM t; --`,
	}
	for _, p := range payloads {
		b, _ := json.Marshal(p)
		bound, err := BindValues([]VariableDecl{decl("v", VarString)}, map[string]json.RawMessage{"v": b})
		require.NoError(t, err)
		assert.Equal(t, p, bound["v"].Value, "payload must round-trip untouched")
	}
}

// AC-4: missing required AND unknown extra reported together.
func TestBindValuesMissingAndUnknownTogether(t *testing.T) {
	decls := []VariableDecl{decl("region", VarString)}
	_, err := BindValues(decls, map[string]json.RawMessage{"regoin": raw(`"EMEA"`)})
	require.Error(t, err)
	de, _ := AsError(err)
	require.Equal(t, CodeVariableInvalid, de.Code)
	problems := de.Details.([]VariableProblem)
	require.Len(t, problems, 2)
	byName := map[string]string{}
	for _, p := range problems {
		byName[p.Name] = p.Problem
	}
	assert.Contains(t, byName["regoin"], "not declared")
	assert.Contains(t, byName["region"], "required")
}

func TestBindValuesDefaultsAndAllowedValues(t *testing.T) {
	f := false
	decls := []VariableDecl{
		{Name: "since", Type: VarDate, Required: &f, Default: raw(`"2026-01-01"`)},
		{Name: "region", Type: VarString, AllowedValues: []json.RawMessage{raw(`"EMEA"`), raw(`"AMER"`)}},
	}
	bound, err := BindValues(decls, map[string]json.RawMessage{"region": raw(`"EMEA"`)})
	require.NoError(t, err)
	assert.Equal(t, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), bound["since"].Value, "default applied")

	_, err = BindValues(decls, map[string]json.RawMessage{"region": raw(`"APAC"`)})
	require.Error(t, err, "allowed_values enforced pre-bind (BR-3)")
}

func TestBindValuesMinMax(t *testing.T) {
	lo, hi := 1.0, 100.0
	decls := []VariableDecl{{Name: "n", Type: VarInteger, Min: &lo, Max: &hi}}
	_, err := BindValues(decls, map[string]json.RawMessage{"n": raw(`50`)})
	require.NoError(t, err)
	_, err = BindValues(decls, map[string]json.RawMessage{"n": raw(`0`)})
	require.Error(t, err)
	_, err = BindValues(decls, map[string]json.RawMessage{"n": raw(`101`)})
	require.Error(t, err)
}

func TestValidateDecls(t *testing.T) {
	require.NoError(t, ValidateDecls([]VariableDecl{decl("a", VarString), decl("b", VarInteger)}))

	for _, bad := range [][]VariableDecl{
		{decl("1bad", VarString)},                            // invalid name
		{decl("a", VarString), decl("a", VarString)},         // duplicate
		{decl("a", VariableType("uuid"))},                    // unknown type
		{{Name: "a", Type: VarDate, Default: raw(`"nope"`)}}, // bad default
	} {
		require.Error(t, ValidateDecls(bad))
	}
}

func TestBigIntegerBoundary(t *testing.T) {
	bound, err := BindValues([]VariableDecl{decl("v", VarInteger)},
		map[string]json.RawMessage{"v": raw(`9007199254740993`)}) // 2^53+1: float64 would corrupt this
	require.NoError(t, err)
	assert.Equal(t, int64(9007199254740993), bound["v"].Value, "int64 precision preserved via json.Number")
}
