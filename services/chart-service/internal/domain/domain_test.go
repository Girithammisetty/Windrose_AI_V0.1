package domain

import (
	"encoding/json"
	"testing"

	"github.com/google/uuid"
)

// TestAC07_CatalogHas30Types: GET /chart-types returns exactly 30 types, each
// with a JSON Schema whose required fields match CHART-FR-012.
func TestAC07_CatalogHas30Types(t *testing.T) {
	cat := Catalog()
	if len(cat) != 30 {
		t.Fatalf("want 30 chart types, got %d", len(cat))
	}
	byFamily := map[string]int{}
	for _, ct := range cat {
		byFamily[ct.Family]++
		if ct.JSONSchema == nil {
			t.Fatalf("%s has no JSON schema", ct.Name)
		}
		// required fields present in schema.
		if len(ct.Required) > 0 {
			req, ok := ct.JSONSchema["required"].([]string)
			if !ok || len(req) == 0 {
				t.Fatalf("%s schema missing required fields", ct.Name)
			}
		}
	}
	// Family cardinalities per CHART-FR-012.
	want := map[string]int{FamilyAxis: 10, FamilyYOnly: 4, FamilyHeatmap: 5, FamilyNetwork: 4, FamilyGrid: 2, FamilyMetric: 5}
	for fam, n := range want {
		if byFamily[fam] != n {
			t.Errorf("family %s: want %d got %d", fam, n, byFamily[fam])
		}
	}
}

// TestAC05_RejectsBadAggregation: agg_fn:"median" → VALIDATION_FAILED naming
// the field and the allowed set.
func TestAC05_RejectsBadAggregation(t *testing.T) {
	cfg := json.RawMessage(`{"x":{"dimension":"region"},"y":[{"measure":"revenue","agg_fn":"median"}]}`)
	err := ValidateConfig("vertical_bar_chart", cfg, nil)
	if err == nil {
		t.Fatal("expected validation error")
	}
	de, ok := AsError(err)
	if !ok || de.Code != CodeValidation {
		t.Fatalf("want VALIDATION_FAILED, got %v", err)
	}
	details, _ := de.Details.([]FieldDetail)
	found := false
	for _, d := range details {
		if d.Field == "config.y[0].agg_fn" && d.Code == CodeInvalidAggregation {
			found = true
			if len(d.Allowed) != 6 {
				t.Errorf("allowed set should list 6 fns, got %v", d.Allowed)
			}
		}
	}
	if !found {
		t.Fatalf("missing per-field detail; got %+v", details)
	}
}

func TestValidConfigAcceptsWhitelistedAgg(t *testing.T) {
	cfg := json.RawMessage(`{"x":{"dimension":"region"},"y":[{"measure":"revenue","agg_fn":"sum"}]}`)
	if err := ValidateConfig("vertical_bar_chart", cfg, nil); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

// TestHeatmapConfigParses: heatmap-family "y" is a dimension ref, not a
// measure array — a config matching CHART-FR-012's documented shape must
// parse and validate cleanly instead of the spurious "config is not valid
// JSON" 422 that ParseConfig's family-blind unmarshal used to raise (it tried
// to decode the {"dimension":...} object into the array-typed Y field).
func TestHeatmapConfigParses(t *testing.T) {
	cfg := json.RawMessage(`{"x":{"dimension":"claim_type"},"y":{"dimension":"vendor"},"dataseries":{"dimension":"severity"}}`)
	if err := ValidateConfig("heatmap_chart", cfg, nil); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	parsed, err := ParseConfig(FamilyHeatmap, cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if parsed.X == nil || parsed.X.Dimension != "claim_type" {
		t.Fatalf("want x=claim_type, got %+v", parsed.X)
	}
	if parsed.YDim == nil || parsed.YDim.Dimension != "vendor" {
		t.Fatalf("want y (dimension)=vendor, got %+v", parsed.YDim)
	}
	if parsed.Dataseries == nil || parsed.Dataseries.Dimension != "severity" {
		t.Fatalf("want dataseries=severity, got %+v", parsed.Dataseries)
	}
	if len(parsed.Y) != 0 {
		t.Fatalf("heatmap's Y (measure array) must stay empty; got %+v", parsed.Y)
	}
}

// TestHeatmapConfigMissingYRejected: "y" absent (or not a dimension object)
// is a REQUIRED validation failure, not a parse-time 422 — covers every
// heatmap-family sibling type sharing this generic config shape.
func TestHeatmapConfigMissingYRejected(t *testing.T) {
	for _, chartType := range []string{"heatmap_chart", "sunburst_chart", "tree_map_chart", "sankey_chart", "chord_chart"} {
		cfg := json.RawMessage(`{"x":{"dimension":"claim_type"},"dataseries":{"dimension":"severity"}}`)
		err := ValidateConfig(chartType, cfg, nil)
		de, ok := AsError(err)
		if !ok || de.Code != CodeValidation {
			t.Fatalf("%s: want VALIDATION_FAILED, got %v", chartType, err)
		}
		details, _ := de.Details.([]FieldDetail)
		found := false
		for _, d := range details {
			if d.Field == "config.y" && d.Code == "REQUIRED" {
				found = true
			}
		}
		if !found {
			t.Fatalf("%s: missing config.y REQUIRED detail; got %+v", chartType, details)
		}
	}
}

// TestHeatmapShapeUsesRealDimensionNames: Shape's column naming must use the
// config's real x/y dimension names (cfg.YDim), not the dead placeholder that
// used to leave "y" hardcoded regardless of the config.
func TestHeatmapShapeUsesRealDimensionNames(t *testing.T) {
	cfg := ChartConfig{
		X:    &DimensionRef{Dimension: "claim_type"},
		YDim: &DimensionRef{Dimension: "vendor"},
	}
	chart := &Chart{ChartType: "heatmap_chart"}
	res := Shape(chart, cfg, nil, [][]any{{"auto", "ACME", 3}}, true)
	if len(res.Columns) != 3 || res.Columns[0] != "claim_type" || res.Columns[1] != "vendor" || res.Columns[2] != "value" {
		t.Fatalf("want [claim_type vendor value], got %v", res.Columns)
	}
}

func TestUnknownChartType(t *testing.T) {
	if err := ValidateConfig("no_such_chart", json.RawMessage(`{}`), nil); err == nil {
		t.Fatal("expected unknown chart type error")
	}
}

func TestUnknownDimensionDetected(t *testing.T) {
	known := map[string]bool{"region": true, "revenue": true}
	cfg := json.RawMessage(`{"x":{"dimension":"regin"},"y":[{"measure":"revenue","agg_fn":"sum"}]}`)
	err := ValidateConfig("vertical_bar_chart", cfg, known)
	de, ok := AsError(err)
	if !ok {
		t.Fatalf("want error, got %v", err)
	}
	details, _ := de.Details.([]FieldDetail)
	if len(details) == 0 || details[0].Code != CodeUnknownDimension {
		t.Fatalf("want UNKNOWN_DIMENSION, got %+v", details)
	}
}

// TestAC13_DeterministicSampling: raw mode over-cap returns ≤10,000 points with
// truncated:true, and identical input yields the identical sample.
func TestAC13_DeterministicSampling(t *testing.T) {
	rows := make([][]any, 50000)
	for i := range rows {
		rows[i] = []any{float64(i), float64(i * 2)}
	}
	chart := &Chart{ID: uuid.New(), ChartType: "scatter_plot", ChartVersion: 3}
	cfg := ChartConfig{X: &DimensionRef{Dimension: "x"}, Y: []MeasureRef{{Measure: "y"}}}
	a := Shape(chart, cfg, []ExecColumn{{Name: "x"}, {Name: "y"}}, rows, false)
	b := Shape(chart, cfg, []ExecColumn{{Name: "x"}, {Name: "y"}}, rows, false)
	if !a.Truncated || len(a.Rows) != RawRowCap {
		t.Fatalf("want truncated 10000 rows, got truncated=%v n=%d", a.Truncated, len(a.Rows))
	}
	for i := range a.Rows {
		if a.Rows[i][0] != b.Rows[i][0] {
			t.Fatalf("sample not deterministic at row %d", i)
		}
	}
}

func TestShapeNetworkObjectForm(t *testing.T) {
	chart := &Chart{ID: uuid.New(), ChartType: "network_chart", ChartVersion: 1}
	cfg := ChartConfig{Nodes: "n", Children: "c"}
	rows := [][]any{{"a", "b", float64(5)}, {"b", "c", float64(2)}}
	res := Shape(chart, cfg, []ExecColumn{{Name: "n"}, {Name: "c"}, {Name: "v"}}, rows, true)
	var g struct {
		Nodes []any `json:"nodes"`
		Edges []any `json:"edges"`
	}
	if err := json.Unmarshal(res.Graph, &g); err != nil {
		t.Fatalf("graph not valid JSON: %v", err)
	}
	if len(g.Nodes) != 3 || len(g.Edges) != 2 {
		t.Fatalf("want 3 nodes / 2 edges, got %d / %d", len(g.Nodes), len(g.Edges))
	}
}

func TestShapeAxisColumnNaming(t *testing.T) {
	chart := &Chart{ID: uuid.New(), ChartType: "vertical_bar_chart", ChartVersion: 1}
	cfg := ChartConfig{X: &DimensionRef{Dimension: "region"}, Y: []MeasureRef{{Measure: "revenue", AggFn: "sum"}, {Measure: "orders", AggFn: "count"}}}
	res := Shape(chart, cfg, nil, [][]any{{"EMEA", 1.0, 2.0}}, true)
	want := []string{"region", "sum_revenue", "count_orders"}
	if len(res.Columns) != 3 || res.Columns[0] != want[0] || res.Columns[1] != want[1] || res.Columns[2] != want[2] {
		t.Fatalf("columns = %v, want %v", res.Columns, want)
	}
	if !res.Aggregated {
		t.Fatal("expected aggregated=true")
	}
}
