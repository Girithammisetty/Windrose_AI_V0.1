// Package domain holds usage-service's core types: the meter catalog,
// metering records, budgets and their window state, rate cards, anomalies,
// reconciliations and adjustments (BRD 17 §4). Types are transport-agnostic;
// the store, api and event layers map onto these.
package domain

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// NewID returns a time-ordered uuidv7 (MASTER-FR-021), falling back to v4.
func NewID() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.New()
	}
	return id
}

// Actor identifies who caused a change (MASTER-FR-031/041).
type Actor struct {
	Type string `json:"type"` // user | service | agent | platform
	ID   string `json:"id"`
}

// ViaAgent carries dual attribution for OBO actions (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Op is the per-request context threaded through store writes: the verified
// tenant, actor and trace. Never sourced from request bodies (MASTER-FR-002).
type Op struct {
	Tenant   uuid.UUID
	Actor    Actor
	ViaAgent *ViaAgent
	TraceID  string
	Platform bool // platform-operator scope (app.tenant_id='*' bypass, audited)
}

// Meter catalog keys (USG-FR-001). Canonical units never change post-launch
// (USG-FR-005).
const (
	MeterAPICalls            = "api_calls"
	MeterQueryBytesScanned   = "query_bytes_scanned"
	MeterPipelineMinutes     = "pipeline_minutes"
	MeterStorageGBMonth      = "storage_gb_month"
	MeterLLMInputTokens      = "llm_input_tokens"
	MeterLLMOutputTokens     = "llm_output_tokens"
	MeterAgentTasksCompleted = "agent_tasks_completed"
)

// Aggregation kinds (USG-FR-001).
const (
	AggSum         = "sum"
	AggTimeWeighted = "time_weighted_avg"
)

// Meter is one catalog entry (USG-FR-001/003).
type Meter struct {
	MeterKey    string   `json:"meter_key"`
	Unit        string   `json:"unit"`
	Aggregation string   `json:"aggregation"`
	Description string   `json:"description"`
	Dimensions  []string `json:"dimensions"`
	Deprecated  bool     `json:"deprecated"`
}

// Catalog is the seeded, versioned platform meter catalog (USG-FR-001). No
// tenant-defined meters in v1.
func Catalog() []Meter {
	dims := []string{"tenant_id", "workspace_id", "user_id", "agent_id", "resource_urn", "model", "cloud"}
	return []Meter{
		{MeterAPICalls, "count", AggSum, "Edge API requests completed", dims, false},
		{MeterQueryBytesScanned, "bytes", AggSum, "Bytes scanned by executed queries", dims, false},
		{MeterPipelineMinutes, "minutes", AggSum, "Pipeline node-minutes consumed", dims, false},
		{MeterStorageGBMonth, "gb_month", AggTimeWeighted, "Time-weighted storage in GB-months", dims, false},
		{MeterLLMInputTokens, "tokens", AggSum, "LLM prompt (input) tokens", dims, false},
		{MeterLLMOutputTokens, "tokens", AggSum, "LLM completion (output) tokens", dims, false},
		{MeterAgentTasksCompleted, "count", AggSum, "Agent runs completed successfully", dims, false},
	}
}

// CatalogKeys is the set of valid meter keys.
func CatalogKeys() map[string]Meter {
	m := map[string]Meter{}
	for _, e := range Catalog() {
		m[e.MeterKey] = e
	}
	return m
}

// MeterRecord is one raw metering row (USG-FR-002). Unknown dimensions are
// stored as nil, never dropped.
type MeterRecord struct {
	Time        time.Time
	TenantID    uuid.UUID
	MeterKey    string
	Quantity    float64
	WorkspaceID *string
	UserID      *string
	AgentID     *string
	Model       *string
	Cloud       string
	ResourceURN *string
	EventID     uuid.UUID
	Late        bool
}

// Budget windows (USG-FR-030).
const (
	WindowCalendarMonth = "calendar_month"
	WindowCalendarDay   = "calendar_day"
	WindowRolling7d     = "rolling_7d"
)

// action_at_100 values.
const (
	ActionAlertOnly = "alert_only"
	ActionHardStop  = "hard_stop"
)

// Budget statuses.
const (
	BudgetActive  = "active"
	BudgetDeleted = "deleted"
)

// Scope is a budget/report scope (USG-FR-030). tenant_id is always implicit.
type Scope struct {
	TenantID    uuid.UUID `json:"tenant_id"`
	WorkspaceID *string   `json:"workspace_id,omitempty"`
	UserID      *string   `json:"user_id,omitempty"`
	AgentID     *string   `json:"agent_id,omitempty"`
}

// Budget is a spend guard (USG-FR-030). Thresholds are the fixed v1 set.
type Budget struct {
	ID          uuid.UUID `json:"id"`
	TenantID    uuid.UUID `json:"tenant_id"`
	WorkspaceID *string   `json:"workspace_id,omitempty"`
	UserID      *string   `json:"user_id,omitempty"`
	AgentID     *string   `json:"agent_id,omitempty"`
	MeterKey    string    `json:"meter_key"`
	Window      string    `json:"window"`
	LimitValue  float64   `json:"limit_value"`
	ActionAt100 string    `json:"action_at_100"`
	Status      string    `json:"status"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// Thresholds is the fixed v1 threshold set (USG-FR-030).
var Thresholds = []int{80, 95, 100}

// BudgetState is per-window consumption + the highest threshold crossed
// (USG-FR-030/031).
type BudgetState struct {
	BudgetID     uuid.UUID  `json:"budget_id"`
	WindowStart  time.Time  `json:"window_start"`
	Consumed     float64    `json:"consumed"`
	LastThreshold int       `json:"last_threshold"` // 0/80/95/100
	ExhaustedAt  *time.Time `json:"exhausted_at,omitempty"`
}

// RateCard statuses.
const (
	RateCardDraft      = "draft"
	RateCardActive     = "active"
	RateCardSuperseded = "superseded"
)

// RateCard prices meters; tenant_id nil = default platform card (USG-FR-042).
type RateCard struct {
	ID            uuid.UUID          `json:"id"`
	TenantID      *uuid.UUID         `json:"tenant_id,omitempty"`
	Version       int                `json:"version"`
	EffectiveFrom time.Time          `json:"effective_from"`
	Status        string             `json:"status"`
	Items         map[string]float64 `json:"items"` // meter_key -> price_per_unit_usd
	CreatedAt     time.Time          `json:"created_at"`
}

// Anomaly statuses.
const (
	AnomalyOpen      = "open"
	AnomalyDismissed = "dismissed"
)

// Anomaly is a detected spend deviation (USG-FR-050/051).
type Anomaly struct {
	ID              uuid.UUID `json:"id"`
	TenantID        uuid.UUID `json:"tenant_id"`
	MeterKey        string    `json:"meter_key"`
	Day             time.Time `json:"day"`
	Observed        float64   `json:"observed"`
	Mean            float64   `json:"mean"`
	Stddev          float64   `json:"stddev"`
	Z               float64   `json:"z"`
	Status          string    `json:"status"`
	DismissedBy     *string   `json:"dismissed_by,omitempty"`
	SuppressedReason *string  `json:"suppressed_reason,omitempty"`
	CreatedAt       time.Time `json:"created_at"`
}

// Reconciliation statuses (BRD 17 §4 state machine).
const (
	ReconPending      = "pending"
	ReconMatched      = "matched"
	ReconVariance     = "variance"
	ReconAdjusted     = "adjusted"
	ReconAcknowledged = "acknowledged"
)

// Reconciliation is a monthly provider-bill vs metered comparison (USG-FR-070).
type Reconciliation struct {
	ID        uuid.UUID `json:"id"`
	Month     string    `json:"month"` // YYYY-MM
	Provider  string    `json:"provider"`
	Status    string    `json:"status"`
	ReportURI string    `json:"report_uri"`
	CreatedAt time.Time `json:"created_at"`
}

// Adjustment is an append-only signed correction on a closed month
// (USG-FR-072).
type Adjustment struct {
	ID            uuid.UUID `json:"id"`
	TenantID      uuid.UUID `json:"tenant_id"`
	MeterKey      string    `json:"meter_key"`
	Month         string    `json:"month"`
	QuantityDelta float64   `json:"quantity_delta"`
	USDDelta      float64   `json:"usd_delta"`
	Reason        string    `json:"reason"`
	Actor         string    `json:"actor"`
	CreatedAt     time.Time `json:"created_at"`
}

// RollupRow is one aggregated bucket returned from showback/chargeback queries.
type RollupRow struct {
	Bucket      *time.Time `json:"-"`
	Day         *string    `json:"day,omitempty"`
	Month       *string    `json:"month,omitempty"`
	MeterKey    string     `json:"meter_key,omitempty"`
	WorkspaceID *string    `json:"workspace_id,omitempty"`
	UserID      *string    `json:"user_id,omitempty"`
	AgentID     *string    `json:"agent_id,omitempty"`
	Model       *string    `json:"model,omitempty"`
	Unit        string     `json:"unit,omitempty"`
	Quantity    float64    `json:"quantity"`
	USD         *float64   `json:"usd,omitempty"`
}

// MarshalJSON emits the dollar figure under BOTH `cost_usd` (the report
// contract bff-graphql consumes) and the legacy `usd` key, from the single
// USD field — the two can never drift.
func (r RollupRow) MarshalJSON() ([]byte, error) {
	type alias RollupRow // no methods: avoids MarshalJSON recursion
	return json.Marshal(struct {
		alias
		CostUSD *float64 `json:"cost_usd,omitempty"`
	}{alias(r), r.USD})
}
