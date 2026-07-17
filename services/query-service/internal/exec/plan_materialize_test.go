package exec

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/datasets"
)

// The physical URIs a resolver hands back for materialization (QRY-FR-005).
var claimsURIs = []string{
	"s3://windrose-warehouse/bronze.t/ds_x/data/00000-0-abc.parquet",
}

// Macro path: a {{dataset('name')}} reference whose resolved Meta carries
// SourceURIs produces a plan.Materialization for the engine.
func TestBuildPlanMaterializesFromMacro(t *testing.T) {
	f := newFixture(t)
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "Claims", Version: 0,
		URN:           "wr:" + f.tenant.String() + ":dataset:dataset/claims",
		PhysicalIdent: `"main"."claims"`,
		Namespace:     "main",
		SizeBytes:     4044,
		RowCount:      14,
		SourceURIs:    claimsURIs,
		SourceFormat:  "parquet",
	}, true)

	plan, err := f.broker.buildPlan(context.Background(),
		f.runReq(`SELECT claim_type, count(*) AS n FROM {{dataset('Claims')}} GROUP BY 1`).PlanRequest)
	require.NoError(t, err)
	require.Len(t, plan.Materializations, 1)
	assert.Equal(t, `"main"."claims"`, plan.Materializations[0].Ident)
	assert.Equal(t, claimsURIs, plan.Materializations[0].URIs)
	assert.Equal(t, "parquet", plan.Materializations[0].Format)
	// The macro rewrote the ref to the engine-quoted ident.
	assert.Contains(t, plan.ExecSQL, `"main"."claims"`)
}

// Semantic path: compiled chart SQL references the physical table directly
// (no macro). With DUCKDB_AUTOMATERIALIZE_SCHEMAS including the schema, the
// table auto-resolves — the guard admits it and a materialization is planned.
func TestBuildPlanAutoMaterializeSemantic(t *testing.T) {
	f := newFixture(t)
	f.broker.AutoMaterializeSchemas = map[string]bool{"main": true}
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "claims", Version: 0,
		URN:           "wr:" + f.tenant.String() + ":dataset:dataset/claims",
		PhysicalIdent: `"main"."claims"`,
		Namespace:     "main",
		RowCount:      14,
		SourceURIs:    claimsURIs,
		SourceFormat:  "parquet",
	}, true)

	plan, err := f.broker.buildPlan(context.Background(),
		f.runReq(`SELECT claim_type, count(*) AS n FROM "main"."claims" GROUP BY 1`).PlanRequest)
	require.NoError(t, err, "auto-resolved schema must pass the tenant guard")
	require.Len(t, plan.Materializations, 1)
	assert.Equal(t, `"main"."claims"`, plan.Materializations[0].Ident)
	assert.Equal(t, claimsURIs, plan.Materializations[0].URIs)
}

// Inert by default: without the env allowlist, the same direct-physical SQL is
// rejected by the tenant guard (prod behavior unchanged; QRY-FR-021, BR-2).
func TestBuildPlanSemanticRejectedWhenDisabled(t *testing.T) {
	f := newFixture(t)
	// AutoMaterializeSchemas left empty (default).
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "claims", Version: 0, PhysicalIdent: `"main"."claims"`,
		Namespace: "main", SourceURIs: claimsURIs, SourceFormat: "parquet",
	}, true)

	_, err := f.broker.buildPlan(context.Background(),
		f.runReq(`SELECT claim_type FROM "main"."claims"`).PlanRequest)
	require.Error(t, err, "unknown physical schema must be rejected when auto-materialize is off")
}
