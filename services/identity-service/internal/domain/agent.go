package domain

import (
	"time"

	"github.com/google/uuid"
)

// AgentPrincipalStatus per IDN-FR-040/043.
type AgentPrincipalStatus string

const (
	AgentActive AgentPrincipalStatus = "active"
	AgentKilled AgentPrincipalStatus = "killed" // kill-switch (AC-7)
)

// AgentPrincipal is created/updated exclusively from agent-registry events
// (IDN-FR-040) — never via manual API. Tenant-scoped, RLS.
type AgentPrincipal struct {
	ID                uuid.UUID            `json:"id"`
	TenantID          uuid.UUID            `json:"tenant_id"`
	AgentID           string               `json:"agent_id"`
	AgentVersion      string               `json:"agent_version"`
	Scopes            []string             `json:"scopes"` // toolset-derived
	AutonomousAllowed bool                 `json:"autonomous_allowed"`
	EvalGateOK        bool                 `json:"eval_gate_ok"` // from eval-service events (IDN-FR-043)
	Status            AgentPrincipalStatus `json:"status"`
	CreatedAt         time.Time            `json:"created_at"`
	UpdatedAt         time.Time            `json:"updated_at"`
}

func (a *AgentPrincipal) URN() string {
	return URN(a.TenantID, "agent_principal", a.AgentID+"@"+a.AgentVersion)
}

// IssuableOBO returns nil if an OBO token may be issued against this
// principal, or the refusal error per IDN-FR-043.
func (a *AgentPrincipal) IssuableOBO() error {
	if a.Status == AgentKilled {
		return EAgentDisabled("agent version killed for tenant")
	}
	if !a.EvalGateOK {
		return EAgentDisabled("agent version eval gate failing")
	}
	if a.Status != AgentActive {
		return EAgentDisabled("agent version not enabled for tenant")
	}
	return nil
}

// AgentRegistryEvent is the consumed shape from agent.events.v1 (BRD §6).
type AgentRegistryEvent struct {
	EventType         string    `json:"event_type"` // agent_version.published|killed|eval_gate_changed
	TenantID          uuid.UUID `json:"tenant_id"`
	AgentID           string    `json:"agent_id"`
	AgentVersion      string    `json:"agent_version"`
	Scopes            []string  `json:"scopes"`
	AutonomousAllowed bool      `json:"autonomous_allowed"`
	EvalGateOK        bool      `json:"eval_gate_ok"`
}
