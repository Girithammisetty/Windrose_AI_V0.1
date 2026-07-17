package domain

import (
	"encoding/json"
	"fmt"
	"sort"
)

// Config families (CHART-FR-012).
const (
	FamilyAxis    = "axis"
	FamilyYOnly   = "y_only"
	FamilyHeatmap = "heatmap"
	FamilyNetwork = "network"
	FamilyGrid    = "grid"
	FamilyMetric  = "metric"
)

// Data-source classes (CHART-FR-011).
const (
	ClassQuery   = "query"   // semantic/query compiled
	ClassDataset = "dataset" // dataset-service profile pointers
	ClassRun     = "run"     // experiment-service MLflow artifacts
)

// AllowedAggFns is the whitelist (CHART-FR-014 / BR-1). V1 ALLOWED_AGG_FNS.
var AllowedAggFns = map[string]bool{
	"sum": true, "avg": true, "min": true, "max": true, "count": true, "first": true,
}

// AllowedAggList is the sorted whitelist for error details.
func AllowedAggList() []string {
	out := make([]string, 0, len(AllowedAggFns))
	for k := range AllowedAggFns {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ChartType is one entry in the catalog.
type ChartType struct {
	Name      string `json:"name"`
	Family    string `json:"family"`
	DataClass string `json:"data_class"`
	// JSONSchema is the per-type config schema served by GET /chart-types.
	JSONSchema map[string]any `json:"config_schema"`
	// Required lists the required config field names (CHART-FR-012).
	Required []string `json:"required_fields"`
}

// catalog is the full 30-type V1 catalog (CHART-FR-011).
var catalog = func() map[string]ChartType {
	type spec struct {
		name, family, class string
	}
	specs := []spec{
		// Query/semantic charts (25) — V1 IDO_CHARTS.
		{"line_chart", FamilyAxis, ClassQuery},
		{"scatter_plot", FamilyAxis, ClassQuery},
		{"pie_chart", FamilyYOnly, ClassQuery},
		{"funnel_chart", FamilyYOnly, ClassQuery},
		{"bubble_chart", FamilyAxis, ClassQuery},
		{"gauge_chart", FamilyYOnly, ClassQuery},
		{"sunburst_chart", FamilyHeatmap, ClassQuery},
		{"vertical_bar_chart", FamilyAxis, ClassQuery},
		{"vertical_stackedbar_chart", FamilyAxis, ClassQuery},
		{"sankey_chart", FamilyHeatmap, ClassQuery},
		{"whisker_chart", FamilyAxis, ClassQuery},
		{"combination_chart", FamilyAxis, ClassQuery},
		{"grid_chart", FamilyGrid, ClassQuery},
		{"geo_map_chart", FamilyAxis, ClassQuery},
		{"tree_map_chart", FamilyHeatmap, ClassQuery},
		{"heatmap_chart", FamilyHeatmap, ClassQuery},
		{"histogram_chart", FamilyAxis, ClassQuery},
		{"waterfall_chart", FamilyAxis, ClassQuery},
		{"word_cloud_chart", FamilyYOnly, ClassQuery},
		{"chord_chart", FamilyHeatmap, ClassQuery},
		{"decision_tree_chart", FamilyNetwork, ClassQuery},
		{"network_graph_chart", FamilyNetwork, ClassQuery},
		{"network_chart", FamilyNetwork, ClassQuery},
		{"tree_chart", FamilyNetwork, ClassQuery},
		{"pivot_table_chart", FamilyGrid, ClassQuery},
		// Dataset charts (2) — V1 DATASET_CHARTS.
		{"metric_chart", FamilyMetric, ClassDataset},
		{"parameter_chart", FamilyMetric, ClassDataset},
		// Run charts (3) — V1 RUN_CHARTS.
		{"roc_curve", FamilyMetric, ClassRun},
		{"confusion_matrix", FamilyMetric, ClassRun},
		{"decision_tree", FamilyMetric, ClassRun},
	}
	m := make(map[string]ChartType, len(specs))
	for _, s := range specs {
		m[s.name] = ChartType{
			Name:       s.name,
			Family:     s.family,
			DataClass:  s.class,
			Required:   requiredForFamily(s.family),
			JSONSchema: schemaForFamily(s.family),
		}
	}
	return m
}()

// Catalog returns the full catalog sorted by name (CHART-FR-012 / AC-7).
func Catalog() []ChartType {
	out := make([]ChartType, 0, len(catalog))
	for _, ct := range catalog {
		out = append(out, ct)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// LookupType returns the catalog entry for name.
func LookupType(name string) (ChartType, bool) {
	ct, ok := catalog[name]
	return ct, ok
}

func requiredForFamily(family string) []string {
	switch family {
	case FamilyAxis:
		return []string{"x", "y"}
	case FamilyYOnly:
		return []string{"y"}
	case FamilyHeatmap:
		return []string{"x", "y", "dataseries"}
	case FamilyNetwork:
		return []string{"nodes", "children"}
	case FamilyGrid:
		return []string{"columns"}
	case FamilyMetric:
		return []string{} // run/dataset ref only, carried in sources
	}
	return nil
}

func schemaForFamily(family string) map[string]any {
	obj := func(props map[string]any, required []string) map[string]any {
		s := map[string]any{"type": "object", "properties": props, "additionalProperties": true}
		if len(required) > 0 {
			s["required"] = required
		}
		return s
	}
	dimRef := map[string]any{"type": "object", "properties": map[string]any{"dimension": map[string]any{"type": "string"}}}
	measureArr := map[string]any{"type": "array", "minItems": 1, "items": map[string]any{
		"type": "object",
		"properties": map[string]any{
			"measure": map[string]any{"type": "string"},
			"agg_fn":  map[string]any{"type": "string", "enum": AllowedAggList()},
		},
	}}
	switch family {
	case FamilyAxis:
		return obj(map[string]any{
			"x": dimRef, "y": measureArr,
			"dataseries": map[string]any{"type": []string{"object", "null"}},
		}, []string{"x", "y"})
	case FamilyYOnly:
		return obj(map[string]any{"x": dimRef, "y": measureArr}, []string{"y"})
	case FamilyHeatmap:
		return obj(map[string]any{"x": dimRef, "y": dimRef, "dataseries": dimRef}, []string{"x", "y", "dataseries"})
	case FamilyNetwork:
		return obj(map[string]any{
			"nodes": map[string]any{"type": "string"}, "children": map[string]any{"type": "string"},
			"node_values": map[string]any{"type": "string"},
		}, []string{"nodes", "children"})
	case FamilyGrid:
		return obj(map[string]any{
			"columns": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
			"pivot":   map[string]any{"type": "object"},
		}, []string{"columns"})
	case FamilyMetric:
		return obj(map[string]any{}, nil)
	}
	return map[string]any{"type": "object"}
}

// --- config parsing ---

// DimensionRef references a semantic dimension or query column.
type DimensionRef struct {
	Dimension string `json:"dimension"`
}

// MeasureRef references a measure/column plus its aggregation.
type MeasureRef struct {
	Measure string `json:"measure"`
	AggFn   string `json:"agg_fn"`
}

// ChartConfig is the union config shape across families.
type ChartConfig struct {
	X          *DimensionRef  `json:"x"`
	Y          []MeasureRef   `json:"y"`
	Dataseries *DimensionRef  `json:"dataseries"`
	Nodes      string         `json:"nodes"`
	Children   string         `json:"children"`
	NodeValues string         `json:"node_values"`
	Columns    []string       `json:"columns"`
	Pivot      map[string]any `json:"pivot"`
	// YDim is the heatmap family's "y" (schemaForFamily(FamilyHeatmap): a
	// dimension ref, not a measure array). Populated only by ParseConfig when
	// family == FamilyHeatmap — see the comment there for why "y" can't share
	// the Y field across families.
	YDim *DimensionRef `json:"-"`
}

// ParseConfig decodes raw config JSON. family selects the per-family shape:
// every family except heatmap uses the struct tags above directly. Heatmap is
// special-cased because its "y" is documented (schemaForFamily) and rendered
// (Shape's tabularColumns) as a DIMENSION ref (`{"dimension": "..."}`), which
// collides with the array-of-measures shape every other family's "y" uses —
// encoding/json can't unmarshal an object into the `Y []MeasureRef` field, so
// decoding a real heatmap config through the shared struct always fails with
// "cannot unmarshal object into Go value of type []domain.MeasureRef", surfaced
// as a spurious "config is not valid JSON" 422 for every heatmap-family type.
func ParseConfig(family string, raw json.RawMessage) (ChartConfig, error) {
	var c ChartConfig
	if len(raw) == 0 {
		return c, nil
	}
	if family == FamilyHeatmap {
		var h struct {
			X          *DimensionRef `json:"x"`
			Y          *DimensionRef `json:"y"`
			Dataseries *DimensionRef `json:"dataseries"`
		}
		if err := json.Unmarshal(raw, &h); err != nil {
			return c, EValidation("config is not valid JSON")
		}
		c.X, c.YDim, c.Dataseries = h.X, h.Y, h.Dataseries
		return c, nil
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		return c, EValidation("config is not valid JSON")
	}
	return c, nil
}

// FieldDetail is one per-field validation problem (master §2.3-024 details).
type FieldDetail struct {
	Field   string   `json:"field"`
	Code    string   `json:"code"`
	Message string   `json:"message,omitempty"`
	Allowed []string `json:"allowed,omitempty"`
}

// ValidateConfig checks a config against its chart-type family and the agg
// whitelist (CHART-FR-012/014). knownFields, when non-nil, is the set of valid
// dimension/measure names discovered from the semantic/query metadata; unknown
// refs produce UNKNOWN_DIMENSION details (CHART-FR-013). Returns a *Error with
// per-field Details on failure.
func ValidateConfig(chartType string, raw json.RawMessage, knownFields map[string]bool) error {
	ct, ok := LookupType(chartType)
	if !ok {
		return &Error{Status: 422, Code: CodeUnknownChartType,
			Message: fmt.Sprintf("unknown chart_type %q", chartType)}
	}
	cfg, err := ParseConfig(ct.Family, raw)
	if err != nil {
		return err
	}
	var details []FieldDetail

	// agg whitelist (defense in depth: also enforced at resolve time).
	for i, y := range cfg.Y {
		if y.AggFn != "" && !AllowedAggFns[y.AggFn] {
			details = append(details, FieldDetail{
				Field: fmt.Sprintf("config.y[%d].agg_fn", i), Code: CodeInvalidAggregation,
				Allowed: AllowedAggList(),
			})
		}
	}

	checkKnown := func(field, name string) {
		if name == "" || knownFields == nil {
			return
		}
		if !knownFields[name] {
			details = append(details, FieldDetail{
				Field: field, Code: CodeUnknownDimension,
				Message: fmt.Sprintf("%q not found in source metadata", name),
			})
		}
	}

	switch ct.Family {
	case FamilyAxis:
		if cfg.X == nil || cfg.X.Dimension == "" {
			details = append(details, FieldDetail{Field: "config.x", Code: "REQUIRED"})
		} else {
			checkKnown("config.x.dimension", cfg.X.Dimension)
		}
		if len(cfg.Y) == 0 {
			details = append(details, FieldDetail{Field: "config.y", Code: "REQUIRED", Message: "at least one measure"})
		}
		for i, y := range cfg.Y {
			checkKnown(fmt.Sprintf("config.y[%d].measure", i), y.Measure)
		}
		if cfg.Dataseries != nil {
			checkKnown("config.dataseries.dimension", cfg.Dataseries.Dimension)
		}
	case FamilyYOnly:
		if len(cfg.Y) == 0 {
			details = append(details, FieldDetail{Field: "config.y", Code: "REQUIRED"})
		}
		for i, y := range cfg.Y {
			checkKnown(fmt.Sprintf("config.y[%d].measure", i), y.Measure)
		}
	case FamilyHeatmap:
		if cfg.X == nil || cfg.X.Dimension == "" {
			details = append(details, FieldDetail{Field: "config.x", Code: "REQUIRED"})
		}
		// heatmap uses x, y (a dimension, ParseConfig populates cfg.YDim — see
		// its doc comment), and dataseries as dims.
		if cfg.Dataseries == nil || cfg.Dataseries.Dimension == "" {
			details = append(details, FieldDetail{Field: "config.dataseries", Code: "REQUIRED"})
		}
		if cfg.YDim == nil || cfg.YDim.Dimension == "" {
			details = append(details, FieldDetail{Field: "config.y", Code: "REQUIRED"})
		}
	case FamilyNetwork:
		if cfg.Nodes == "" {
			details = append(details, FieldDetail{Field: "config.nodes", Code: "REQUIRED"})
		}
		if cfg.Children == "" {
			details = append(details, FieldDetail{Field: "config.children", Code: "REQUIRED"})
		}
	case FamilyGrid:
		if len(cfg.Columns) == 0 {
			details = append(details, FieldDetail{Field: "config.columns", Code: "REQUIRED"})
		}
		for i, col := range cfg.Columns {
			checkKnown(fmt.Sprintf("config.columns[%d]", i), col)
		}
	case FamilyMetric:
		// run/dataset ref only — carried in sources, validated by resolver.
	}

	if len(details) > 0 {
		return EValidation("chart config invalid", details)
	}
	return nil
}
