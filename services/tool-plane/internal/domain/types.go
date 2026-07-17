// Package domain holds the tool-plane catalog + enforcement domain model
// (BRD 13 §4): tools, versioned tool records, per-tenant enablement, kill
// switches, BYO submissions, and the enforcement decision types shared by the
// registry (catalog/admin) and the gateway (per-call enforcement) deployables.
package domain

import (
	"time"

	"github.com/google/uuid"
)

// Permission tiers (TPL-FR-001). Order matters for max-tier comparison.
const (
	TierRead          = "read"
	TierWriteProposal = "write-proposal"
	TierWriteDirect   = "write-direct"
	TierAdmin         = "admin"
)

// TierRank orders tiers for max_tier_override comparisons (higher = more
// privileged). Unknown tiers rank -1.
func TierRank(t string) int {
	switch t {
	case TierRead:
		return 0
	case TierWriteProposal:
		return 1
	case TierWriteDirect:
		return 2
	case TierAdmin:
		return 3
	}
	return -1
}

// Side effects (TPL-FR-001). Destructive tools can never be write-direct (BR-2).
const (
	SideEffectNone        = "none"
	SideEffectReversible  = "reversible"
	SideEffectDestructive = "destructive"
)

// Version lifecycle states (TPL-FR-002).
const (
	StatusDraft       = "draft"
	StatusPublished   = "published"
	StatusDeprecated  = "deprecated"
	StatusRetired     = "retired"
	StatusQuarantined = "quarantined"
)

// JWT typ claims (MASTER-FR-011).
const (
	TypUser            = "user"
	TypService         = "service"
	TypAgentOBO        = "agent_obo"
	TypAgentAutonomous = "agent_autonomous"
)

// PlatformTenant is the reserved tenant that owns platform-scoped catalog rows
// (BRD §4: "platform-scoped catalog rows use the reserved platform tenant").
var PlatformTenant = uuid.MustParse("00000000-0000-0000-0000-000000000000")

// DeclaredSLA is the tool's owner-declared service level (TPL-FR-001).
type DeclaredSLA struct {
	P95MS        int     `json:"p95_ms"`
	ErrorRatePct float64 `json:"error_rate_pct"`
}

// Example is a documented input/description pair used for discovery embedding
// (TPL-FR-020: embedding over semantic_description + examples).
type Example struct {
	Input       map[string]any `json:"input"`
	Description string         `json:"description"`
}

// Tool is a catalog object (BRD §4 tools table). Platform-scoped.
type Tool struct {
	ToolID          string    `json:"tool_id"` // namespaced, e.g. case.assign
	DisplayName     string    `json:"display_name"`
	OwnerService    string    `json:"owner_service"`
	OwnerTeam       string    `json:"owner_team"`
	EnabledByDefault bool     `json:"enabled_by_default"`
	SideEffects     string    `json:"side_effects"`
	Tags            []string  `json:"tags"`
	CreatedAt       time.Time `json:"created_at"`
	UpdatedAt       time.Time `json:"updated_at"`
}

// ToolVersion is an immutable-once-published version of a tool (BRD §4
// tool_versions table).
type ToolVersion struct {
	ToolID              string         `json:"tool_id"`
	Version             string         `json:"version"` // semver
	Status              string         `json:"status"`
	InputSchema         map[string]any `json:"input_schema"`
	OutputSchema        map[string]any `json:"output_schema"`
	SemanticDescription string         `json:"semantic_description"`
	PermissionTier      string         `json:"permission_tier"`
	CostWeight          int            `json:"cost_weight"`
	DeclaredSLA         DeclaredSLA    `json:"declared_sla"`
	SideEffects         string         `json:"side_effects"`
	Examples            []Example      `json:"examples"`
	Embedding           []float32      `json:"-"`
	EmbeddingModelVer   string         `json:"embedding_model_ver"`
	DeprecationEndsAt   *time.Time     `json:"deprecation_ends_at,omitempty"`
	PublishedAt         *time.Time     `json:"published_at,omitempty"`
	CreatedAt           time.Time      `json:"created_at"`
	UpdatedAt           time.Time      `json:"updated_at"`
}

// Deprecation is the warning attached to results/list items for a deprecated
// version (BR-5, TPL-FR-011).
type Deprecation struct {
	EndsAt  time.Time `json:"ends_at"`
	Message string    `json:"message"`
}

// TenantToolSettings is a per-tenant enablement row (BRD §4
// tenant_tool_settings table). RLS-scoped.
type TenantToolSettings struct {
	TenantID           uuid.UUID      `json:"tenant_id"`
	ToolID             string         `json:"tool_id"`
	Enabled            bool           `json:"enabled"`
	MaxTierOverride    string         `json:"max_tier_override,omitempty"`
	ArgumentConstraints map[string]any `json:"argument_constraints,omitempty"`
	RateLimitOverride  *RateLimitOverride `json:"rate_limit_override,omitempty"`
	UpdatedAt          time.Time      `json:"updated_at"`
}

// RateLimitOverride overrides the cost-weight-derived per-minute cap (TPL-FR-033).
type RateLimitOverride struct {
	PerMin int `json:"per_min"`
}

// KillScope identifies the kill-switch granularity (TPL-FR-052).
const (
	KillScopeTool        = "tool"
	KillScopeToolVersion = "tool_version"
	KillScopeToolTenant  = "tool_tenant"
)

// KillSwitch is a persisted, Redis-replicated kill (BRD §4 kill_switches table).
type KillSwitch struct {
	ID       uuid.UUID  `json:"id"`
	Scope    string     `json:"scope"`
	ToolID   string     `json:"tool_id"`
	Version  string     `json:"version,omitempty"`
	TenantID *uuid.UUID `json:"tenant_id,omitempty"`
	Active   bool       `json:"active"`
	Reason   string     `json:"reason"`
	SetBy    string     `json:"set_by"`
	CreatedAt time.Time `json:"created_at"`
}

// BYO submission states (TPL-FR-040).
const (
	BYOPending  = "pending_approval"
	BYOApproved = "approved"
	BYORejected = "rejected"
)

// BYOSubmission is a third-party/BYO onboarding request (BRD §4 byo_submissions).
type BYOSubmission struct {
	ID              uuid.UUID      `json:"id"`
	Manifest        map[string]any `json:"manifest"`
	EndpointURL     string         `json:"endpoint_url"`
	AuthMethod      string         `json:"auth_method"`
	RequestedTier   string         `json:"requested_tier"`
	EgressDescription string       `json:"egress_description"`
	Status          string         `json:"status"`
	DecidedBy       string         `json:"decided_by,omitempty"`
	DecisionMessage string         `json:"decision_message,omitempty"`
	CreatedAt       time.Time      `json:"created_at"`
}

// MCPBackend is a registered domain-service MCP facade or external endpoint
// (BRD §4 mcp_backends table, TPL-FR-010/012).
type MCPBackend struct {
	Name           string   `json:"name"`
	InternalURL    string   `json:"internal_url"`
	SpiffeID       string   `json:"spiffe_id"`
	Kind           string   `json:"kind"` // internal | external
	EgressAllowlist []string `json:"egress_allowlist"`
	VaultAuthRef   string   `json:"vault_auth_ref"`
	Status         string   `json:"status"`
}

// HealthHourly is one rolled-up health bucket (BRD §4 tool_health_hourly).
type HealthHourly struct {
	ToolID      string         `json:"tool_id"`
	Version     string         `json:"version"`
	Hour        time.Time      `json:"hour"`
	Calls       int64          `json:"calls"`
	ErrorsByKind map[string]int64 `json:"errors_by_kind"`
	P50MS       int            `json:"p50_ms"`
	P95MS       int            `json:"p95_ms"`
	P99MS       int            `json:"p99_ms"`
}

// InvocationLog is a digest-level enforcement record (BRD §4 invocation_log).
type InvocationLog struct {
	ID         uuid.UUID `json:"id"`
	TenantID   uuid.UUID `json:"tenant_id"`
	AgentID    string    `json:"agent_id"`
	AgentVersion string  `json:"agent_version"`
	OboSub     string    `json:"obo_sub,omitempty"`
	ToolID     string    `json:"tool_id"`
	ToolVersion string   `json:"tool_version"`
	Tier       string    `json:"tier"`
	Decision   string    `json:"decision"`
	ErrorCode  string    `json:"error_code,omitempty"`
	ArgsDigest string    `json:"args_digest"`
	URNs       []string  `json:"urns"`
	LatencyMS  int       `json:"latency_ms"`
	TraceID    string    `json:"trace_id"`
	CreatedAt  time.Time `json:"created_at"`
}

// Actor / ViaAgent mirror the master envelope attribution (MASTER-FR-041).
type Actor struct {
	Type string `json:"type"`
	ID   string `json:"id"`
}

// ViaAgent carries dual OBO attribution.
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// NewID returns a time-ordered uuidv7 (MASTER-FR-021).
func NewID() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.New()
	}
	return id
}
