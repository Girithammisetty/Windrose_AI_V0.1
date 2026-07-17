package domain

import (
	"time"

	"github.com/google/uuid"
)

// Actor identifies who performed an action (MASTER-FR-031, MASTER-FR-041).
type Actor struct {
	Type  string `json:"type"` // user | agent | service | platform
	ID    string `json:"id"`
	Scope string `json:"scope,omitempty"` // "platform" for super-admin actions (IDN-FR-025)
}

// ViaAgent carries dual attribution for OBO writes (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// OutboxEvent is the transactional-outbox row matching the master event
// envelope (MASTER-FR-031, MASTER-FR-034). Stores persist it in the same
// transaction as the state change it describes (BR-12).
type OutboxEvent struct {
	EventID     uuid.UUID      `json:"event_id"` // uuidv7
	EventType   string         `json:"event_type"`
	TenantID    uuid.UUID      `json:"tenant_id"`
	Actor       Actor          `json:"actor"`
	ViaAgent    *ViaAgent      `json:"via_agent"`
	ResourceURN string         `json:"resource_urn"`
	OccurredAt  time.Time      `json:"occurred_at"`
	TraceID     string         `json:"trace_id"`
	Payload     map[string]any `json:"payload"`
	PublishedAt *time.Time     `json:"published_at,omitempty"`
}

// NewEvent builds an outbox event with a fresh uuidv7 event id.
func NewEvent(eventType string, tenantID uuid.UUID, actor Actor, urn string, now time.Time, payload map[string]any) OutboxEvent {
	id, _ := uuid.NewV7()
	if payload == nil {
		payload = map[string]any{}
	}
	return OutboxEvent{
		EventID:     id,
		EventType:   eventType,
		TenantID:    tenantID,
		Actor:       actor,
		ResourceURN: urn,
		OccurredAt:  now.UTC(),
		Payload:     payload,
	}
}

// URN builds a windrose resource URN: wr:<tenant_id>:<service>:<type>/<id>
// (MASTER-FR-013). identity-service resources use service segment "identity".
func URN(tenantID uuid.UUID, resourceType, resourceID string) string {
	return "wr:" + tenantID.String() + ":identity:" + resourceType + "/" + resourceID
}

// PlatformURN is used for platform-scoped resources (tenants themselves,
// signing keys) where the tenant segment is the affected tenant or "platform".
func PlatformURN(resourceType, resourceID string) string {
	return "wr:platform:identity:" + resourceType + "/" + resourceID
}

// Event type names (MASTER-FR-035, BRD §6).
const (
	EvTenantCreated         = "tenant.created"
	EvTenantPublished       = "tenant.published"
	EvTenantStepCompleted   = "tenant.provision_step_completed"
	EvTenantProvisioned     = "tenant.provisioned"
	EvTenantProvisionFailed = "tenant.provision_failed"
	EvTenantSuspended       = "tenant.suspended"
	EvTenantReactivated     = "tenant.reactivated"
	EvTenantDeletionStarted = "tenant.deletion_started"
	EvTenantDeleted         = "tenant.deleted"
	EvUserInvited           = "user.invited"
	EvUserActivated         = "user.activated"
	EvUserUpdated           = "user.updated"
	EvUserDeactivated       = "user.deactivated"
	EvUserDeleted           = "user.deleted"
	EvSvcAccountCreated     = "service_account.created"
	EvSvcAccountRotated     = "service_account.rotated"
	EvSvcAccountRevoked     = "service_account.revoked"
	EvAgentPrincipalSynced  = "agent_principal.synced"
	EvTokenOBOIssued        = "token.obo_issued"
	EvSigningKeyRotated     = "signing_key.rotated"
	EvCrossTenantDenied     = "security.cross_tenant_denied"
)
