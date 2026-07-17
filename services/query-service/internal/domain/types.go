package domain

import (
	"time"

	"github.com/google/uuid"
)

// JWT typ values (MASTER-FR-011).
const (
	TypUser            = "user"
	TypService         = "service"
	TypAgentOBO        = "agent_obo"
	TypAgentAutonomous = "agent_autonomous"
)

// CallerClass buckets executions for governance (QRY-FR-022/042/044).
type CallerClass string

const (
	CallerUser    CallerClass = "user"
	CallerService CallerClass = "service"
	CallerAgent   CallerClass = "agent"
)

// CallerClassForTyp maps a JWT typ to a caller class.
func CallerClassForTyp(typ string) CallerClass {
	switch typ {
	case TypService:
		return CallerService
	case TypAgentOBO, TypAgentAutonomous:
		return CallerAgent
	default:
		return CallerUser
	}
}

// SavedQuery is the mutable head of a saved query (QRY-FR-001).
type SavedQuery struct {
	ID               uuid.UUID  `json:"id"`
	TenantID         uuid.UUID  `json:"-"`
	WorkspaceID      uuid.UUID  `json:"workspace_id"`
	Name             string     `json:"name"`
	Description      string     `json:"description"`
	CurrentVersionNo int        `json:"current_version_no"`
	Tags             []string   `json:"tags"`
	ModuleNames      []string   `json:"module_names"`
	CreatedBy        string     `json:"created_by"`
	CreatedAt        time.Time  `json:"created_at"`
	UpdatedAt        time.Time  `json:"updated_at"`
	DeletedAt        *time.Time `json:"deleted_at,omitempty"`
}

// SavedQueryVersion is one immutable version row (QRY-FR-001).
type SavedQueryVersion struct {
	ID           uuid.UUID      `json:"id"`
	TenantID     uuid.UUID      `json:"-"`
	SavedQueryID uuid.UUID      `json:"saved_query_id"`
	VersionNo    int            `json:"version_no"`
	SQLText      string         `json:"sql_text"`
	Variables    []VariableDecl `json:"variables"`
	DatasetRefs  []DatasetRef   `json:"dataset_refs"`
	CreatedBy    string         `json:"created_by"`
	CreatedAt    time.Time      `json:"created_at"`
}

// DatasetRef is one {{dataset(...)}} reference resolved at save time
// (QRY-FR-005). Version 0 means "latest at execution time".
type DatasetRef struct {
	Name    string `json:"name"`
	Version int    `json:"version,omitempty"`
	URN     string `json:"urn,omitempty"`
}

// Execution statuses (BRD §4.2).
const (
	StatusCreated          = "created"
	StatusPlanning         = "planning"
	StatusRejected         = "rejected"
	StatusQueued           = "queued"
	StatusRunning          = "running"
	StatusStreamingResults = "streaming_results"
	StatusSucceeded        = "succeeded"
	StatusFailed           = "failed"
	StatusCancelled        = "cancelled"
	StatusCeilingExceeded  = "ceiling_exceeded"
)

// TerminalStatuses per BRD §4.2.
func IsTerminalStatus(s string) bool {
	switch s {
	case StatusSucceeded, StatusFailed, StatusCancelled, StatusRejected, StatusCeilingExceeded:
		return true
	}
	return false
}

// legalTransitions encodes the execution state machine (BRD §4.2).
var legalTransitions = map[string][]string{
	StatusCreated:          {StatusPlanning},
	StatusPlanning:         {StatusRejected, StatusQueued, StatusRunning, StatusSucceeded},
	StatusQueued:           {StatusRunning, StatusCancelled, StatusFailed},
	StatusRunning:          {StatusStreamingResults, StatusSucceeded, StatusFailed, StatusCancelled, StatusCeilingExceeded},
	StatusStreamingResults: {StatusSucceeded, StatusFailed, StatusCancelled, StatusCeilingExceeded},
}

// CanTransition reports whether from → to is a legal state-machine move.
// planning → succeeded exists only for cache hits (QRY-FR-046: no engine
// contact, results reused).
func CanTransition(from, to string) bool {
	for _, t := range legalTransitions[from] {
		if t == to {
			return true
		}
	}
	return false
}

// ExecError is the persisted error of a failed/rejected execution.
type ExecError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Details any    `json:"details,omitempty"`
}

// RoutingReason records why an engine was chosen (QRY-FR-040, §4.3).
type RoutingReason struct {
	Rule     string   `json:"rule"` // tenant_policy | small_interactive | default_large | engine_fallback | engine_hint
	Detail   string   `json:"detail,omitempty"`
	Warnings []string `json:"warnings,omitempty"`
}

// Execution is one row of query history (QRY-FR-080).
type Execution struct {
	ID                 uuid.UUID      `json:"id"`
	TenantID           uuid.UUID      `json:"-"`
	WorkspaceID        uuid.UUID      `json:"workspace_id"`
	SavedQueryID       *uuid.UUID     `json:"saved_query_id,omitempty"`
	QueryVersionNo     *int           `json:"query_version_no,omitempty"`
	SQLFingerprint     string         `json:"sql_fingerprint"`
	SQLText            string         `json:"-"`            // stored compressed
	CacheKey           string         `json:"-"`            // (tenant, fingerprint, params, dataset versions) QRY-FR-046
	DatasetURNs        []string       `json:"-"`            // for dataset.deleted queue invalidation (§6)
	BoundParams        map[string]any `json:"bound_params"` // PII-redacted (BR-12)
	CallerClass        CallerClass    `json:"caller_class"`
	Engine             string         `json:"engine"`
	RoutingReason      *RoutingReason `json:"routing_reason,omitempty"`
	Status             string         `json:"status"`
	QueuePosition      *int           `json:"queue_position,omitempty"`
	EstimatedScanBytes int64          `json:"estimated_scan_bytes"`
	ActualScanBytes    int64          `json:"actual_scan_bytes"`
	ResultRows         int64          `json:"result_rows"`
	ResultBytes        int64          `json:"result_bytes"`
	ResultURI          string         `json:"-"`
	CacheHit           bool           `json:"cache_hit"`
	Error              *ExecError     `json:"error,omitempty"`
	Ceilings           *Ceilings      `json:"ceilings,omitempty"`
	StartedAt          *time.Time     `json:"started_at,omitempty"`
	FinishedAt         *time.Time     `json:"finished_at,omitempty"`
	CreatedBy          string         `json:"created_by"`
	ViaAgent           map[string]any `json:"via_agent,omitempty"`
	TraceID            string         `json:"trace_id"`
	CreatedAt          time.Time      `json:"created_at"`
	Warnings           []string       `json:"warnings,omitempty"`
	DurationMS         int64          `json:"duration_ms"`
}

// Actor identifies who caused a mutation (event envelope, MASTER-FR-031).
type Actor struct {
	Type string `json:"type"` // user | service | agent
	ID   string `json:"id"`
}

// ViaAgent carries dual attribution for OBO actions (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Op is the per-request mutation context derived from verified claims only
// (MASTER-FR-001/002: tenant never comes from request input).
type Op struct {
	Tenant   uuid.UUID
	Actor    Actor
	ViaAgent *ViaAgent
	TraceID  string
	Caller   CallerClass
	UserID   string // effective user for fairness accounting (OBO → obo_sub)
}

// NewID returns a UUIDv7 (MASTER-FR-021: time-ordered ids).
func NewID() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.New()
	}
	return id
}
