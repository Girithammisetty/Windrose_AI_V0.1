package domain

import (
	"time"

	"github.com/google/uuid"
)

// Record is one row of the ClickHouse audit_events table (AUD-FR-003/010, §4).
// It is the immutable, append-only projection of a consumed envelope plus the
// derived digest/chain fields.
type Record struct {
	EventID         uuid.UUID
	EventType       string
	SourceTopic     string
	TenantID        uuid.UUID
	ActorType       string // user | service | agent
	ActorID         string
	ViaAgentID      string
	ViaAgentVersion string
	OboUserID       string
	ResourceURN     string
	ResourceService string
	ResourceType    string
	Action          string
	OccurredAt      time.Time
	IngestedAt      time.Time
	TraceID         string
	PayloadDigest   string // sha256 hex, always set
	PayloadJSON     string // PII-clean canonical JSON, may be empty
	PayloadRef      string // topic/partition/offset when body withheld
	ChainDate       string // YYYY-MM-DD ingest day the chain seq belongs to (BR-2/BR-3)
	ChainSeq        uint64
	ChainHash       string // sha256 hex
}

// SearchFilter is the parsed /audit/search query (AUD-FR-030/031).
type SearchFilter struct {
	TenantID       uuid.UUID
	ActorID        string
	ActorType      string
	ViaAgentID     string
	OboUserID      string
	ResourceURN    string
	ResourcePrefix string // when set, prefix match on resource_urn
	Action         string
	EventType      string
	TraceID        string
	From           time.Time
	To             time.Time
	IncludeAuto    bool // dual-attribution: also include autonomous agent rows
	Limit          int
	AfterOccurred  *time.Time // cursor
	AfterEventID   *uuid.UUID
}

// MaxSearchRangeDays bounds a single search window (AUD-FR-030).
const MaxSearchRangeDays = 92
