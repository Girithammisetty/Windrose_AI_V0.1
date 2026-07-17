package resolve

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/windrose-ai/chart-service/internal/domain"
)

// Resolver orchestrates the compile → execute → shape pipeline (CHART-FR-020).
type Resolver struct {
	Semantic  SemanticCompiler
	Query     QueryExecutor
	Artifacts ArtifactFetcher
	// DefaultModel is used when a chart config omits an explicit semantic model.
	DefaultModel string
}

// opMap maps the request filter op whitelist (CHART-FR-022) to semantic ops.
var opMap = map[string]string{
	"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
	"in": "IN", "between": "BETWEEN", "like": "LIKE",
}

// Resolve resolves a saved chart's data. token is forwarded to upstreams so
// their own RLS/authz applies (defense in depth).
func (r *Resolver) Resolve(ctx context.Context, token string, chart *domain.Chart, req domain.ResolveRequest) (*domain.ShapedResult, error) {
	if chart.ConfigStatus == "broken" {
		return nil, domain.ESourceBroken("chart references a deleted measure/query")
	}
	ct, ok := domain.LookupType(chart.ChartType)
	if !ok {
		return nil, &domain.Error{Status: 422, Code: domain.CodeUnknownChartType, Message: "unknown chart_type"}
	}
	cfg, err := domain.ParseConfig(ct.Family, chart.Config)
	if err != nil {
		return nil, err
	}

	// Metric/run/dataset families: artifact pass-through (CHART-FR-025).
	if ct.Family == domain.FamilyMetric {
		return r.resolveArtifact(ctx, token, chart, ct)
	}

	limit := req.Limit
	if limit <= 0 {
		limit = 5000
	}

	// Choose source: semantic measure (hero path) or saved query.
	src := primarySource(chart)
	var exec ExecResult
	switch src.SourceType {
	case domain.SourceSavedQuery:
		exec, err = r.Query.RunSavedQuery(ctx, token, urnTail(src.SourceURN), req.Variables, limit)
	case domain.SourceSemanticMeasure, "":
		exec, err = r.compileAndRun(ctx, token, chart, cfg, req, limit)
	default:
		return nil, domain.ESourceBroken("unsupported source_type " + src.SourceType)
	}
	if err != nil {
		return nil, err
	}

	shaped := domain.Shape(chart, cfg, exec.Columns, exec.Rows, req.AggregatedDefault())
	return shaped, nil
}

// compileAndRun runs the semantic-service compile then query-service execute.
func (r *Resolver) compileAndRun(ctx context.Context, token string, chart *domain.Chart, cfg domain.ChartConfig, req domain.ResolveRequest, limit int) (ExecResult, error) {
	creq := r.buildCompile(chart, cfg, req)
	if len(creq.Metrics) == 0 {
		return ExecResult{}, domain.EValidation("chart has no measures to resolve")
	}
	compiled, err := r.Semantic.Compile(ctx, token, creq)
	if err != nil {
		return ExecResult{}, err
	}
	binds := make([]any, 0, len(compiled.Params))
	for _, p := range compiled.Params {
		binds = append(binds, p.Value)
	}
	return r.Query.RunSQL(ctx, token, compiled.SQL, binds, limit)
}

// buildCompile maps a chart config + request into a semantic compile request.
func (r *Resolver) buildCompile(chart *domain.Chart, cfg domain.ChartConfig, req domain.ResolveRequest) CompileRequest {
	model := modelFromChart(chart, cfg)
	if model == "" {
		model = r.DefaultModel
	}
	metrics := make([]string, 0, len(cfg.Y))
	for _, y := range cfg.Y {
		if y.Measure != "" {
			metrics = append(metrics, y.Measure)
		}
	}
	var dims []string
	if req.AggregatedDefault() {
		if cfg.X != nil && cfg.X.Dimension != "" {
			dims = append(dims, cfg.X.Dimension)
		}
		if cfg.Dataseries != nil && cfg.Dataseries.Dimension != "" {
			dims = append(dims, cfg.Dataseries.Dimension)
		}
	}
	filters := make([]CompileFilter, 0, len(req.Filters))
	for _, f := range req.Filters {
		op := opMap[f.Op]
		if op == "" {
			op = "="
		}
		filters = append(filters, CompileFilter{Dimension: f.Field, Op: op, Values: toValues(f.Value)})
	}
	return CompileRequest{
		Model: model, WorkspaceID: workspaceFromChart(chart), Metrics: metrics,
		Dimensions: dims, Filters: filters, Variables: req.Variables,
	}
}

// resolveArtifact handles metric/parameter/run charts (CHART-FR-025).
func (r *Resolver) resolveArtifact(ctx context.Context, token string, chart *domain.Chart, ct domain.ChartType) (*domain.ShapedResult, error) {
	src := primarySource(chart)
	if src.SourceURN == "" || r.Artifacts == nil {
		return nil, domain.ESourceBroken("no artifact source configured")
	}
	art, err := r.Artifacts.FetchArtifact(ctx, token, src.SourceURN)
	if err != nil {
		return nil, err
	}
	return &domain.ShapedResult{
		ChartID: chart.ID.String(), ChartType: chart.ChartType, ChartVersion: chart.ChartVersion,
		Aggregated: false, Columns: []string{}, Artifact: art, RowCount: 1,
	}, nil
}

// Drilldown executes a separate paginated query with the clicked dimension
// injected as an AND bind-parameter predicate (CHART-FR-040 / AC-6).
type DrilldownRequest struct {
	Clicked struct {
		Dimension string `json:"dimension"`
		Value     any    `json:"value"`
	} `json:"clicked"`
	DataseriesValue any             `json:"dataseries_value"`
	Filters         []domain.Filter `json:"filters"`
	Cursor          string          `json:"cursor"`
	Limit           int             `json:"limit"`
}

// Drilldown wraps a saved query's SQL with a bind predicate and paginates.
func (r *Resolver) Drilldown(ctx context.Context, token, queryURN string, dr DrilldownRequest) (ExecResult, error) {
	if queryURN == "" {
		return ExecResult{}, domain.ENoDrilldown()
	}
	limit := dr.Limit
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	baseSQL, err := r.Query.SavedQuerySQL(ctx, token, urnTail(queryURN))
	if err != nil {
		return ExecResult{}, err
	}
	if dr.Clicked.Dimension == "" {
		return ExecResult{}, domain.EValidation("clicked.dimension is required")
	}
	if !identRe(dr.Clicked.Dimension) {
		return ExecResult{}, domain.EValidation("invalid dimension identifier")
	}
	// Wrap the query as a subselect and add an AND bind predicate; the clicked
	// value is a bind parameter, never interpolated (BR-6).
	var binds []any
	binds = append(binds, dr.Clicked.Value)
	wrapped := fmt.Sprintf("SELECT * FROM (%s) AS drill WHERE %s = $%d", strings.TrimRight(baseSQL, "; \n\t"), dr.Clicked.Dimension, len(binds))
	for _, f := range dr.Filters {
		if !identRe(f.Field) || !domain.AllowedFilterOps[f.Op] {
			return ExecResult{}, domain.EValidation("invalid drilldown filter")
		}
		binds = append(binds, f.Value)
		wrapped += fmt.Sprintf(" AND %s %s $%d", f.Field, opMap[f.Op], len(binds))
	}
	return r.Query.RunSQLPaged(ctx, token, wrapped, binds, dr.Cursor, limit)
}

// --- helpers ---

func primarySource(c *domain.Chart) domain.ChartSource {
	if len(c.Sources) == 0 {
		return domain.ChartSource{}
	}
	best := c.Sources[0]
	for _, s := range c.Sources {
		if s.Position < best.Position {
			best = s
		}
	}
	return best
}

func urnTail(urn string) string {
	if i := strings.LastIndex(urn, "/"); i >= 0 {
		return urn[i+1:]
	}
	return urn
}

func toValues(v any) []any {
	if v == nil {
		return nil
	}
	if arr, ok := v.([]any); ok {
		return arr
	}
	return []any{v}
}

// ChartModel returns a chart's semantic model name (from display_meta or
// config), or "" for saved-query/artifact charts. Exposed for cross-filter
// targeting in batch resolution (CHART-FR-041).
func ChartModel(chart *domain.Chart) string {
	return modelFromChart(chart, domain.ChartConfig{})
}

func modelFromChart(chart *domain.Chart, cfg domain.ChartConfig) string {
	var meta struct {
		Model string `json:"semantic_model"`
	}
	_ = json.Unmarshal(chart.DisplayMeta, &meta)
	if meta.Model != "" {
		return meta.Model
	}
	var cm struct {
		Model string `json:"model"`
	}
	_ = json.Unmarshal(chart.Config, &cm)
	return cm.Model
}

func workspaceFromChart(chart *domain.Chart) string {
	var meta struct {
		WorkspaceID string `json:"workspace_id"`
	}
	_ = json.Unmarshal(chart.DisplayMeta, &meta)
	return meta.WorkspaceID
}

// identRe validates a SQL identifier (letters, digits, underscore, dots).
func identRe(s string) bool {
	if s == "" || len(s) > 128 {
		return false
	}
	for i, ch := range s {
		switch {
		case ch >= 'a' && ch <= 'z', ch >= 'A' && ch <= 'Z', ch == '_':
		case ch == '.' && i > 0:
		case ch >= '0' && ch <= '9' && i > 0:
		default:
			return false
		}
	}
	return true
}
