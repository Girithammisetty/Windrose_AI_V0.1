// Package domain holds notification-service's core types, enums, errors and
// state machines (BRD 19 §4). Nothing here talks to infrastructure; the store,
// api, channel and pipeline packages depend on these types.
package domain

import (
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

// Actor identifies who caused an event (MASTER-FR-031/041).
type Actor struct {
	Type string `json:"type"`
	ID   string `json:"id"`
}

// ViaAgent carries dual attribution for OBO actions (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Op is the authenticated mutation context, built only from verified JWT claims
// (MASTER-FR-001/002).
type Op struct {
	Tenant   uuid.UUID
	Actor    Actor
	ViaAgent *ViaAgent
	UserID   string // effective user (OBO → original)
	TraceID  string
}

// Severity classes (BRD 19 §3 NOTIF-FR-002).
const (
	SeverityInfo     = "info"
	SeverityWarning  = "warning"
	SeverityCritical = "critical"
)

// Channels (BRD 19 §3).
const (
	ChannelInApp   = "in_app"
	ChannelEmail   = "email"
	ChannelWebhook = "webhook"
)

// Delivery statuses (BRD 19 §4 state machine).
const (
	StatusQueued              = "queued"
	StatusSent                = "sent"
	StatusDelivered           = "delivered"
	StatusBounced             = "bounced"
	StatusComplained          = "complained"
	StatusFailed              = "failed"
	StatusSuppressed          = "suppressed"
	StatusRateLimitedDigested = "rate_limited_digested"
	// StatusSkipped records an email that was not sent because the recipient has
	// no email address on file (genuine directory miss). Distinct from failed:
	// nothing went wrong on the wire; there was simply no address to send to.
	StatusSkipped = "skipped"
)

// Webhook circuit states (BRD 19 §4 state machine).
const (
	CircuitClosed   = "closed"
	CircuitOpen     = "open"
	CircuitHalfOpen = "half_open"
	CircuitDisabled = "disabled"
)

// Template statuses (NOTIF-FR-040).
const (
	TemplateDraft     = "draft"
	TemplatePublished = "published"
	TemplateArchived  = "archived"
)

// Rule scopes (NOTIF-FR-010).
const (
	ScopeUser      = "user"
	ScopeWorkspace = "workspace"
	ScopeTenant    = "tenant"
)

// SubjectType for a subscription rule (NOTIF-FR-010).
const (
	SubjectUser  = "user"
	SubjectGroup = "group"
)

// SubscriptionRule is a per user/workspace/tenant routing rule (NOTIF-FR-010).
type SubscriptionRule struct {
	ID            uuid.UUID      `json:"id"`
	TenantID      uuid.UUID      `json:"tenant_id"`
	Scope         string         `json:"scope"`
	SubjectType   string         `json:"subject_type"`
	SubjectID     string         `json:"subject_id"`
	EventTypes    []string       `json:"event_types"`
	ResourceFtr   ResourceFilter `json:"resource_filter"`
	Channels      []string       `json:"channels"`
	DigestEnabled bool           `json:"digest_enabled"`
	DigestWindow  string         `json:"digest_window"`
	Active        bool           `json:"active"`
	CreatedBy     string         `json:"created_by"`
	CreatedAt     time.Time      `json:"created_at"`
	UpdatedAt     time.Time      `json:"updated_at"`
	DeletedAt     *time.Time     `json:"deleted_at,omitempty"`
}

// ResourceFilter narrows which events a rule fires on (NOTIF-FR-010). attrs are
// whitelisted payload fields per event type (BR-12).
type ResourceFilter struct {
	ResourceURNPrefix string              `json:"resource_urn_prefix,omitempty"`
	Attrs             map[string][]string `json:"attrs,omitempty"`
}

// UserPreferences are per-user delivery preferences (NOTIF-FR-012).
type UserPreferences struct {
	ID              uuid.UUID           `json:"id"`
	TenantID        uuid.UUID           `json:"tenant_id"`
	UserID          string              `json:"user_id"`
	ChannelOverride map[string][]string `json:"channel_overrides"` // event_type -> channels
	Mutes           Mutes               `json:"mutes"`
	QuietHours      *QuietHours         `json:"quiet_hours,omitempty"`
	DigestConfig    map[string]string   `json:"digest_config"` // event_class -> window
	UpdatedAt       time.Time           `json:"updated_at"`
}

// Mutes captures muted event types and resource URNs (NOTIF-FR-012).
type Mutes struct {
	EventTypes   []string `json:"event_types,omitempty"`
	ResourceURNs []string `json:"resource_urns,omitempty"`
}

// QuietHours is a local-TZ window during which email is deferred (NOTIF-FR-012).
type QuietHours struct {
	TZ    string `json:"tz"`    // IANA name, e.g. America/New_York
	Start string `json:"start"` // "22:00"
	End   string `json:"end"`   // "07:00"
}

// Notification is a persisted in-app notification (NOTIF-FR-020).
type Notification struct {
	ID            uuid.UUID   `json:"id"`
	TenantID      uuid.UUID   `json:"tenant_id"`
	UserID        string      `json:"user_id"`
	EventID       uuid.UUID   `json:"event_id"`
	EventType     string      `json:"event_type"`
	SeverityClass string      `json:"severity_class"`
	Title         string      `json:"title"`
	Body          string      `json:"body"`
	ResourceURN   string      `json:"resource_urn"`
	DeepLink      string      `json:"deep_link"`
	MatchedRules  []uuid.UUID `json:"matched_rules"`
	ReadAt        *time.Time  `json:"read_at,omitempty"`
	CreatedAt     time.Time   `json:"created_at"`
}

// WebhookEndpoint is a tenant-registered webhook target (NOTIF-FR-022).
type WebhookEndpoint struct {
	ID                  uuid.UUID       `json:"id"`
	TenantID            uuid.UUID       `json:"tenant_id"`
	URL                 string          `json:"url"`
	EventTypes          []string        `json:"event_types"`
	Secrets             []WebhookSecret `json:"secrets"`
	Active              bool            `json:"active"`
	VerifiedAt          *time.Time      `json:"verified_at,omitempty"`
	CircuitState        string          `json:"circuit_state"`
	CircuitOpenedAt     *time.Time      `json:"circuit_opened_at,omitempty"`
	ConsecutiveFailures int             `json:"consecutive_failures"`
	CreatedBy           string          `json:"created_by"`
	CreatedAt           time.Time       `json:"created_at"`
	UpdatedAt           time.Time       `json:"updated_at"`
}

// WebhookSecret is one signing secret version. Two remain active during the 24h
// rotation overlap (NOTIF-FR-022, AC-6).
type WebhookSecret struct {
	Version   int        `json:"version"`
	Secret    string     `json:"secret"`
	CreatedAt time.Time  `json:"created_at"`
	ExpiresAt *time.Time `json:"expires_at,omitempty"` // set on the superseded secret at rotation
}

// ActiveSecrets returns the signing secrets still valid at now (unexpired).
func (e *WebhookEndpoint) ActiveSecrets(now time.Time) []WebhookSecret {
	var out []WebhookSecret
	for _, s := range e.Secrets {
		if s.ExpiresAt == nil || s.ExpiresAt.After(now) {
			out = append(out, s)
		}
	}
	return out
}

// Delivery records one attempted delivery on any channel (NOTIF-FR-050).
type Delivery struct {
	ID                uuid.UUID  `json:"id"`
	TenantID          uuid.UUID  `json:"tenant_id"`
	NotificationID    *uuid.UUID `json:"notification_id,omitempty"`
	WebhookEndpointID *uuid.UUID `json:"webhook_endpoint_id,omitempty"`
	EventID           uuid.UUID  `json:"event_id"`
	Recipient         string     `json:"recipient"` // user_id or endpoint id, part of the unique key
	Channel           string     `json:"channel"`
	Provider          string     `json:"provider"`
	Status            string     `json:"status"`
	ProviderMsgID     string     `json:"provider_msg_id"`
	Attempts          int        `json:"attempts"`
	LastError         string     `json:"last_error"`
	NextRetryAt       *time.Time `json:"next_retry_at,omitempty"`
	CreatedAt         time.Time  `json:"created_at"`
	UpdatedAt         time.Time  `json:"updated_at"`
}

// Template is a versioned, per-tenant-overridable message template
// (NOTIF-FR-040/041). TenantID nil = platform default.
type Template struct {
	ID          uuid.UUID  `json:"id"`
	TenantID    *uuid.UUID `json:"tenant_id,omitempty"`
	Key         string     `json:"key"`
	Channel     string     `json:"channel"`
	Locale      string     `json:"locale"`
	Version     int        `json:"version"`
	SubjectTpl  string     `json:"subject_tpl"`
	BodyHTMLTpl string     `json:"body_html_tpl"`
	BodyTextTpl string     `json:"body_text_tpl"`
	Status      string     `json:"status"`
	PublishedAt *time.Time `json:"published_at,omitempty"`
	CreatedBy   string     `json:"created_by"`
	CreatedAt   time.Time  `json:"created_at"`
}

// Suppression auto-mutes an email address after a hard bounce/complaint
// (NOTIF-FR-021, AC-10).
type Suppression struct {
	ID        uuid.UUID  `json:"id"`
	TenantID  uuid.UUID  `json:"tenant_id"`
	EmailHash string     `json:"email_hash"`
	Reason    string     `json:"reason"` // bounce | complaint | manual
	CreatedAt time.Time  `json:"created_at"`
	ClearedAt *time.Time `json:"cleared_at,omitempty"`
}

// DigestBuffer accumulates digest-flagged notifications for one
// (recipient, channel, event_class) window (NOTIF-FR-030).
type DigestBuffer struct {
	ID         uuid.UUID    `json:"id"`
	TenantID   uuid.UUID    `json:"tenant_id"`
	UserID     string       `json:"user_id"`
	Channel    string       `json:"channel"`
	EventClass string       `json:"event_class"`
	Items      []DigestItem `json:"items"`
	WindowEnd  time.Time    `json:"window_end"`
	CreatedAt  time.Time    `json:"created_at"`
}

// DigestItem is one rolled-up notification reference in a digest (NOTIF-FR-030).
type DigestItem struct {
	EventID     uuid.UUID `json:"event_id"`
	EventType   string    `json:"event_type"`
	Title       string    `json:"title"`
	ResourceURN string    `json:"resource_urn"`
	DeepLink    string    `json:"deep_link"`
	At          time.Time `json:"at"`
}

// Report subscription cadences (NOTIF-FR-060).
const (
	CadenceDaily  = "daily"
	CadenceWeekly = "weekly"
)

// Report subscription formats (NOTIF-FR-060).
const (
	ReportFormatHTML = "html"
	ReportFormatText = "text"
)

// Report subscription last-run statuses (NOTIF-FR-060).
const (
	ReportStatusSent   = "sent"
	ReportStatusFailed = "failed"
)

// ReportSubscription is a scheduled dashboard-digest email subscription
// (NOTIF-FR-060,"Case Reports / Team Reports"). Each enabled
// row backs one real Temporal Schedule that periodically fires ReportWorkflow.
type ReportSubscription struct {
	ID                  uuid.UUID  `json:"id"`
	TenantID            uuid.UUID  `json:"tenant_id"`
	WorkspaceID         uuid.UUID  `json:"workspace_id"`
	DashboardID         uuid.UUID  `json:"dashboard_id"`
	Name                string     `json:"name"`
	Recipients          []string   `json:"recipients"`
	Cadence             string     `json:"cadence"`      // daily | weekly
	SendHour            int        `json:"send_hour"`    // 0-23, local to Timezone
	SendWeekday         *int       `json:"send_weekday,omitempty"` // 0(Sun)-6(Sat), weekly only
	Timezone            string     `json:"timezone"`
	Format              string     `json:"format"` // html | text
	Enabled             bool       `json:"enabled"`
	TemporalScheduleID  string     `json:"temporal_schedule_id,omitempty"`
	LastSentAt          *time.Time `json:"last_sent_at,omitempty"`
	LastStatus          string     `json:"last_status,omitempty"`
	LastError           string     `json:"last_error,omitempty"`
	CreatedBy           string     `json:"created_by"`
	CreatedAt           time.Time  `json:"created_at"`
	UpdatedAt           time.Time  `json:"updated_at"`
	DeletedAt           *time.Time `json:"deleted_at,omitempty"`
}

// NewID returns a time-ordered uuidv7 (MASTER-FR-021).
func NewID() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.New()
	}
	return id
}
