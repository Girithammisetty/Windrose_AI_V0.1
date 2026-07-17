package engine

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/domain"
)

func healthyAll() RouteInput {
	return RouteInput{
		DialectPortable:  true,
		DuckDBHealthy:    true,
		TrinoHealthy:     true,
		WarehouseHealthy: true,
	}
}

// The §4.3 decision table, top-down (AC-5 routing legs included).
func TestRouteDecisionTable(t *testing.T) {
	cases := []struct {
		name       string
		mutate     func(*RouteInput)
		wantEngine string
		wantRule   string
	}{
		{"tenant policy warehouse_primary", func(in *RouteInput) {
			in.WarehousePrimary = true
			in.EstimatedScanBytes = 1 // even tiny queries obey tenant policy
		}, NameWarehouse, "tenant_policy"},
		{"small interactive → duckdb (400MB scan / 3GB data)", func(in *RouteInput) {
			in.EstimatedScanBytes = 400 << 20
			in.TotalDatasetBytes = 3 << 30
		}, NameDuckDB, "small_interactive"},
		{"scan above 500MB → trino", func(in *RouteInput) {
			in.EstimatedScanBytes = 501 << 20
			in.TotalDatasetBytes = 1 << 30
		}, NameTrino, "default_large"},
		{"20GB scan → trino (AC-5)", func(in *RouteInput) {
			in.EstimatedScanBytes = 20 << 30
			in.TotalDatasetBytes = 20 << 30
		}, NameTrino, "default_large"},
		{"dataset total above 5GB → trino even with small scan", func(in *RouteInput) {
			in.EstimatedScanBytes = 100 << 20
			in.TotalDatasetBytes = 6 << 30
		}, NameTrino, "default_large"},
		{"dialect not portable → trino", func(in *RouteInput) {
			in.EstimatedScanBytes = 1
			in.DialectPortable = false
		}, NameTrino, "default_large"},
		{"trino down → warehouse fallback (BR-13)", func(in *RouteInput) {
			in.EstimatedScanBytes = 20 << 30
			in.TrinoHealthy = false
		}, NameWarehouse, "engine_fallback"},
		{"trino down, duckdb-eligible unaffected (BR-13)", func(in *RouteInput) {
			in.EstimatedScanBytes = 1 << 20
			in.TrinoHealthy = false
		}, NameDuckDB, "small_interactive"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			in := healthyAll()
			tc.mutate(&in)
			d, err := Route(in)
			require.NoError(t, err)
			assert.Equal(t, tc.wantEngine, d.Engine)
			assert.Equal(t, tc.wantRule, d.Reason.Rule, "routing reason recorded (QRY-FR-040)")
		})
	}
}

func TestRouteFallbackCarriesWarning(t *testing.T) {
	in := healthyAll()
	in.EstimatedScanBytes = 20 << 30
	in.TrinoHealthy = false
	d, err := Route(in)
	require.NoError(t, err)
	assert.Contains(t, d.Warnings, WarnEngineFallback)
}

func TestRouteAllEnginesDown(t *testing.T) {
	in := healthyAll()
	in.EstimatedScanBytes = 20 << 30
	in.TrinoHealthy = false
	in.WarehouseHealthy = false
	_, err := Route(in)
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeEngineUnavailable, de.Code)
	assert.Equal(t, 503, de.HTTP)
}

// engine_hint may promote but never force DuckDB above thresholds
// (QRY-FR-040, §4.3).
func TestRouteHints(t *testing.T) {
	// promote duckdb → trino
	in := healthyAll()
	in.EstimatedScanBytes = 1
	in.EngineHint = NameTrino
	d, err := Route(in)
	require.NoError(t, err)
	assert.Equal(t, NameTrino, d.Engine)
	assert.Equal(t, "engine_hint", d.Reason.Rule)

	// promote → warehouse
	in.EngineHint = NameWarehouse
	d, err = Route(in)
	require.NoError(t, err)
	assert.Equal(t, NameWarehouse, d.Engine)

	// duckdb hint above thresholds is ignored with HINT_OVERRIDDEN
	in = healthyAll()
	in.EstimatedScanBytes = 20 << 30
	in.EngineHint = NameDuckDB
	d, err = Route(in)
	require.NoError(t, err)
	assert.Equal(t, NameTrino, d.Engine)
	assert.Contains(t, d.Warnings, WarnHintOverridden)

	// unknown hint
	in.EngineHint = "quantum"
	_, err = Route(in)
	require.Error(t, err)
}
