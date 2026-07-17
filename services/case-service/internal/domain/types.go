// Package domain holds case-service's pure business types and rules: the case
// row-reference model (CASE-FR-001), the lifecycle state machine and its
// guards (§4), dedup keys (CASE-FR-005), severity, and the copilot proposal
// contract (CASE-FR-051/052). It has no infrastructure dependencies so the
// state machine and validation are unit-testable and race-testable without
// Docker.
package domain

import (
	"crypto/sha256"
	"encoding/hex"
	"time"

	"github.com/google/uuid"
)

// JWT principal types (MASTER-FR-011).
const (
	TypUser            = "user"
	TypService         = "service"
	TypAgentOBO        = "agent_obo"
	TypAgentAutonomous = "agent_autonomous"
)

// Status is the case lifecycle state (CASE-FR-010), values mined from V1
// Case.statuses. Ordinals are wire-stable and DB-persisted.
type Status int16

const (
	StatusDraft      Status = 0
	StatusInProgress Status = 1
	StatusResolved   Status = 2
	StatusUnassigned Status = 3
	StatusClosed     Status = 4
)

var statusNames = map[Status]string{
	StatusDraft: "draft", StatusInProgress: "in_progress", StatusResolved: "resolved",
	StatusUnassigned: "unassigned", StatusClosed: "closed",
}

// String returns the wire name (used in API + search projection).
func (s Status) String() string {
	if n, ok := statusNames[s]; ok {
		return n
	}
	return "unknown"
}

// ParseStatus maps a wire name back to a Status.
func ParseStatus(name string) (Status, bool) {
	for s, n := range statusNames {
		if n == name {
			return s, true
		}
	}
	return 0, false
}

// Severity levels (CASE-FR-021).
const (
	SeverityLow      = "low"
	SeverityMedium   = "medium"
	SeverityHigh     = "high"
	SeverityCritical = "critical"
)

var severityRank = map[string]int{
	SeverityLow: 0, SeverityMedium: 1, SeverityHigh: 2, SeverityCritical: 3,
}

// ValidSeverity reports whether s is a known severity.
func ValidSeverity(s string) bool { _, ok := severityRank[s]; return ok }

// BumpSeverity returns the next level up, capped at critical (CASE-FR-015).
func BumpSeverity(s string) string {
	switch s {
	case SeverityLow:
		return SeverityMedium
	case SeverityMedium:
		return SeverityHigh
	default:
		return SeverityCritical
	}
}

// Disposition categories (CASE-FR-020).
const (
	CatTruePositive  = "true_positive"
	CatFalsePositive = "false_positive"
	CatBenign        = "benign"
	CatInconclusive  = "inconclusive"
	CatOther         = "other"
)

// Actor identifies who caused a change (MASTER-FR-031/041).
type Actor struct {
	Type string `json:"type"`
	ID   string `json:"id"`
}

// ViaAgent carries dual attribution for OBO/agent actions (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Op is the per-request mutation context, built only from verified JWT claims.
type Op struct {
	Tenant      uuid.UUID
	WorkspaceID uuid.UUID
	Actor       Actor
	ViaAgent    *ViaAgent
	UserID      string
	TraceID     string
}

// Disposition is one catalog entry (CASE-FR-020).
type Disposition struct {
	ID           uuid.UUID `json:"id"`
	TenantID     uuid.UUID `json:"-"`
	WorkspaceID  uuid.UUID `json:"workspace_id"`
	Code         string    `json:"code"`
	Label        string    `json:"label"`
	Category     string    `json:"category"`
	RequiresNote bool      `json:"requires_note"`
	Active       bool      `json:"active"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

// CaseField is a custom field definition (CASE-FR-022).
type CaseField struct {
	ID          uuid.UUID      `json:"id"`
	TenantID    uuid.UUID      `json:"-"`
	WorkspaceID uuid.UUID      `json:"workspace_id"`
	QueryURN    string         `json:"query_urn,omitempty"`
	Name        string         `json:"name"`
	DataType    string         `json:"data_type"`
	Purpose     int16          `json:"purpose"` // 0=create,1=update,2=both
	FieldMeta   map[string]any `json:"field_meta"`
	CreatedAt   time.Time      `json:"created_at"`
	UpdatedAt   time.Time      `json:"updated_at"`
}

// Field purposes (CASE-FR-022).
const (
	PurposeCreate int16 = 0
	PurposeUpdate int16 = 1
	PurposeBoth   int16 = 2
)

// SLAPolicy governs SLA timer behavior per workspace (CASE-FR-012).
type SLAPolicy struct {
	TenantID         uuid.UUID     `json:"-"`
	WorkspaceID      uuid.UUID     `json:"workspace_id"`
	WarnBefore       time.Duration `json:"warn_before"`
	OnBreach         string        `json:"on_breach"`
	EscalateTo       *uuid.UUID    `json:"escalate_to,omitempty"`
	MaxReassignCount int           `json:"max_reassign_count"`
}

// SLA breach actions (CASE-FR-012).
const (
	BreachAutoUnassign = "auto_unassign"
	BreachEscalate     = "escalate"
	BreachNotifyOnly   = "notify_only"
)

// DefaultSLAPolicy is the fallback when a workspace has none configured.
func DefaultSLAPolicy(tenant, workspace uuid.UUID) SLAPolicy {
	return SLAPolicy{
		TenantID: tenant, WorkspaceID: workspace,
		WarnBefore: 24 * time.Hour, OnBreach: BreachAutoUnassign, MaxReassignCount: 3,
	}
}

// Case is the triage case aggregate (CASE-FR-001). It never stores a full row
// snapshot while open — only the row reference plus a small display projection.
type Case struct {
	ID                  uuid.UUID         `json:"id"`
	TenantID            uuid.UUID         `json:"-"`
	WorkspaceID         uuid.UUID         `json:"workspace_id"`
	CaseNumber          int64             `json:"case_number"`
	Status              Status            `json:"-"`
	Severity            string            `json:"severity"`
	AssignedToID        *uuid.UUID        `json:"assigned_to_id,omitempty"`
	AssignedToAt        *time.Time        `json:"assigned_to_at,omitempty"`
	CreatedByID         string            `json:"created_by_id"`
	DatasetURN          string            `json:"dataset_urn"`
	DatasetVersion      string            `json:"dataset_version,omitempty"`
	RowPK               string            `json:"row_pk"`
	DedupKey            *string           `json:"dedup_key,omitempty"`
	DisplayProjection   map[string]string `json:"display_projection"`
	ProjectionTruncated bool              `json:"projection_truncated"`
	SourceQueryURNs     []string          `json:"source_query_urns"`
	DashboardURN        string            `json:"dashboard_urn,omitempty"`
	DueDate             time.Time         `json:"due_date"`
	Description         string            `json:"description,omitempty"`
	CustomFields        map[string]any    `json:"custom_fields"`
	DispositionID       *uuid.UUID        `json:"disposition_id,omitempty"`
	ResolutionNote      string            `json:"resolution_note,omitempty"`
	ResolvedAt          *time.Time        `json:"resolved_at,omitempty"`
	ClosedAt            *time.Time        `json:"closed_at,omitempty"`
	SnapshotRef         string            `json:"snapshot_ref,omitempty"`
	RecurrenceOf        *uuid.UUID        `json:"recurrence_of,omitempty"`
	ReassignCount       int               `json:"reassign_count"`
	RowUnavailable      bool              `json:"row_unavailable"`
	CaseVersion         int               `json:"case_version"`
	CreatedAt           time.Time         `json:"created_at"`
	UpdatedAt           time.Time         `json:"updated_at"`
	DeletedAt           *time.Time        `json:"-"`
}

// StatusName exposes the wire status for JSON responses.
func (c *Case) StatusName() string { return c.Status.String() }

// Comment is one case comment (CASE-FR-024).
type Comment struct {
	ID        uuid.UUID  `json:"id"`
	TenantID  uuid.UUID  `json:"-"`
	CaseID    uuid.UUID  `json:"case_id"`
	AuthorID  string     `json:"author_id"`
	Body      string     `json:"body"`
	EditedAt  *time.Time `json:"edited_at,omitempty"`
	CreatedAt time.Time  `json:"created_at"`
	DeletedAt *time.Time `json:"-"`
}

// Activity is one timeline entry (CASE-FR-025).
type Activity struct {
	ID          uuid.UUID `json:"id"`
	CaseID      uuid.UUID `json:"case_id"`
	EventType   string    `json:"event_type"`
	ActorType   string    `json:"actor_type"`
	ActorID     string    `json:"actor_id"`
	ViaAgent    *ViaAgent `json:"via_agent,omitempty"`
	ProposalURN string    `json:"proposal_urn,omitempty"`
	OldValue    any       `json:"old_value,omitempty"`
	NewValue    any       `json:"new_value,omitempty"`
	OccurredAt  time.Time `json:"occurred_at"`
}

// Display projection caps (CASE-FR-001, BR-11).
const (
	MaxProjectionCols   = 12
	MaxProjectionColLen = 256
)

// TruncateProjection enforces the ≤12 cols × ≤256 chars cap (BR-11). Oversize
// values are truncated with an ellipsis marker and never rejected; the second
// return reports whether any truncation happened (sets projection_truncated).
func TruncateProjection(in map[string]string) (map[string]string, bool) {
	out := make(map[string]string, len(in))
	truncated := false
	// Deterministic column selection: take up to MaxProjectionCols keys sorted.
	keys := sortedKeys(in)
	if len(keys) > MaxProjectionCols {
		keys = keys[:MaxProjectionCols]
		truncated = true
	}
	for _, k := range keys {
		v := in[k]
		if len(v) > MaxProjectionColLen {
			v = v[:MaxProjectionColLen-1] + "…"
			truncated = true
		}
		out[k] = v
	}
	return out, truncated
}

// DedupKey is sha256(dataset_urn ‖ row_pk) as "sha256:<hex>" (CASE-FR-005). The
// second return is false for keyless creations (no key column projected, BR-2),
// which are dedup-exempt.
func DedupKey(datasetURN, rowPK string) (string, bool) {
	if datasetURN == "" || rowPK == "" {
		return "", false
	}
	sum := sha256.Sum256([]byte(datasetURN + "\x00" + rowPK))
	return "sha256:" + hex.EncodeToString(sum[:]), true
}

// NewID returns a time-ordered uuidv7 (MASTER-FR-021).
func NewID() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.New()
	}
	return id
}

func sortedKeys(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	// insertion sort keeps zero deps and is fine for ≤ a handful of columns.
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j-1] > keys[j]; j-- {
			keys[j-1], keys[j] = keys[j], keys[j-1]
		}
	}
	return keys
}
