package domain

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// Modules a dashboard can belong to (CHART-FR-001), mined from V1 route
// namespaces.
const (
	ModuleInsights       = "insights"
	ModuleCaseManagement = "case_management"
	ModuleInspector      = "inspector"
)

// Source types for typed chart_sources (CHART-FR-013).
const (
	SourceSemanticMeasure = "semantic_measure"
	SourceSavedQuery      = "saved_query"
	SourceDataset         = "dataset"
	SourceMLRun           = "ml_run"
)

// Link types (CHART-FR-015).
const (
	LinkSharedSource  = 0
	LinkMainSecondary = 1
)

// Dashboard is a grid of charts within a workspace/module.
type Dashboard struct {
	ID          uuid.UUID       `json:"id"`
	TenantID    uuid.UUID       `json:"tenant_id"`
	WorkspaceID uuid.UUID       `json:"workspace_id"`
	Name        string          `json:"name"`
	Module      string          `json:"module"`
	Description string          `json:"description"`
	Layout      json.RawMessage `json:"layout"`
	Meta        json.RawMessage `json:"meta"`
	Tags        []string        `json:"tags"`
	OwnerUserID string          `json:"owner_user_id"`
	Status      string          `json:"status"`
	Archived    bool            `json:"archived"`
	ArchivedAt  *time.Time      `json:"archived_at,omitempty"`
	CreatedAt   time.Time       `json:"created_at"`
	UpdatedAt   time.Time       `json:"updated_at"`
	LastContent *time.Time      `json:"last_content_updated_at,omitempty"`
}

// LayoutPlacement is one grid cell (CHART-FR-002).
type LayoutPlacement struct {
	ChartID string `json:"chart_id"`
	X       int    `json:"x"`
	Y       int    `json:"y"`
	W       int    `json:"w"`
	H       int    `json:"h"`
}

// Chart is a single visualization bound to typed sources.
type Chart struct {
	ID             uuid.UUID       `json:"id"`
	TenantID       uuid.UUID       `json:"tenant_id"`
	DashboardID    uuid.UUID       `json:"dashboard_id"`
	Name           string          `json:"name"`
	ChartType      string          `json:"chart_type"`
	Description    string          `json:"description"`
	Config         json.RawMessage `json:"config"`
	DisplayMeta    json.RawMessage `json:"display_meta"`
	ChartVersion   int             `json:"chart_version"`
	Custom         bool            `json:"custom"`
	ConfigStatus   string          `json:"config_status"`
	LinkType       *int            `json:"link_type,omitempty"`
	LinkedParentID *uuid.UUID      `json:"linked_parent_id,omitempty"`
	Sources        []ChartSource   `json:"sources"`
	CreatedAt      time.Time       `json:"created_at"`
	UpdatedAt      time.Time       `json:"updated_at"`
}

// ChartSource is one typed source reference (CHART-FR-013).
type ChartSource struct {
	Position   int    `json:"position"`
	SourceType string `json:"source_type"`
	SourceURN  string `json:"source_urn"`
}

// ChartLink is a cross-module link between charts (CHART-FR-015).
type ChartLink struct {
	ID            uuid.UUID    `json:"id"`
	ParentChartID uuid.UUID    `json:"parent_chart_id"`
	ChildChartID  uuid.UUID    `json:"child_chart_id"`
	LinkedColumns []ColumnPair `json:"linked_columns"`
}

// ColumnPair maps a parent column to a child column.
type ColumnPair struct {
	ParentCol string `json:"parent_col"`
	ChildCol  string `json:"child_col"`
}

// Documentation is markdown attached to a dashboard or chart (CHART-FR-006).
type Documentation struct {
	ID               uuid.UUID  `json:"id"`
	DocumentableType string     `json:"documentable_type"`
	DocumentableID   uuid.UUID  `json:"documentable_id"`
	Content          string     `json:"content"`
	Archived         bool       `json:"archived"`
	ArchivedAt       *time.Time `json:"archived_at,omitempty"`
	UpdatedAt        time.Time  `json:"updated_at"`
}

// Operation is an async export/render job (CHART-FR-041).
type Operation struct {
	ID          uuid.UUID       `json:"id"`
	ChartID     *uuid.UUID      `json:"chart_id,omitempty"`
	Kind        string          `json:"kind"`
	Format      string          `json:"format,omitempty"`
	Status      string          `json:"status"`
	ArtifactURL string          `json:"artifact_url,omitempty"`
	ArtifactURN string          `json:"artifact_urn,omitempty"`
	Error       string          `json:"error,omitempty"`
	Request     json.RawMessage `json:"-"`
	ExpiresAt   *time.Time      `json:"expires_at,omitempty"`
	CreatedBy   string          `json:"-"`
	CreatedAt   time.Time       `json:"created_at"`
	UpdatedAt   time.Time       `json:"updated_at"`
}

// Filter is a bind-parameter predicate (CHART-FR-022, BR-6).
type Filter struct {
	Field string `json:"field"`
	Op    string `json:"op"`
	Value any    `json:"value"`
	// Origin is the id of the chart whose selection emitted this filter
	// (cross-filter, CHART-FR-041). Batch resolution uses it to keep a chart from
	// filtering itself and to scope the filter to same-model charts. It is
	// ignored by single-chart resolution and never reaches the compile request.
	Origin string `json:"origin,omitempty"`
}

// AllowedFilterOps is the whitelist (CHART-FR-022).
var AllowedFilterOps = map[string]bool{
	"eq": true, "neq": true, "in": true, "gt": true, "gte": true,
	"lt": true, "lte": true, "between": true, "like": true,
}

// ResolveRequest carries variables + filters + paging for data resolution.
type ResolveRequest struct {
	Variables  map[string]any `json:"variables"`
	Filters    []Filter       `json:"filters"`
	Aggregated *bool          `json:"aggregated"`
	Cursor     string         `json:"cursor"`
	Limit      int            `json:"limit"`
	Page       int            `json:"page"`
}

// AggregatedDefault reports the effective aggregated flag (default true,
// CHART-FR-021).
func (r ResolveRequest) AggregatedDefault() bool {
	if r.Aggregated == nil {
		return true
	}
	return *r.Aggregated
}

// ShapedResult is the shaped, cache-storable response body (CHART-FR-020).
type ShapedResult struct {
	ChartID      string          `json:"chart_id"`
	ChartType    string          `json:"chart_type"`
	ChartVersion int             `json:"chart_version"`
	Aggregated   bool            `json:"aggregated"`
	Columns      []string        `json:"columns"`
	Rows         [][]any         `json:"rows,omitempty"`
	Graph        json.RawMessage `json:"graph,omitempty"`    // network family object shape
	Artifact     json.RawMessage `json:"artifact,omitempty"` // metric/run pass-through
	RowCount     int             `json:"row_count"`
	Truncated    bool            `json:"truncated"`
	ResolvedAt   time.Time       `json:"resolved_at"`
}
