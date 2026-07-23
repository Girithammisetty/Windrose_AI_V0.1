package domain

import (
	"strings"
	"time"

	"github.com/google/uuid"
)

// ExternalAgentKey is a per-agent credential a tenant admin mints so a
// customer's OWN agent can self-service a short-lived agent_autonomous token
// (BRD 60 WS2). It carries the agent identity + scopes the minted token gets;
// only the argon2 hash of the secret is stored (the plaintext is shown once at
// creation). Revocable (Active=false) and per-tenant for admin management.
type ExternalAgentKey struct {
	ID           uuid.UUID  `json:"id"`
	TenantID     uuid.UUID  `json:"tenant_id"`
	AgentID      string     `json:"agent_id"`
	AgentVersion int        `json:"agent_version"`
	Scopes       []string   `json:"scopes"`
	SecretHash   string     `json:"-"`
	Label        string     `json:"label"`
	Active       bool       `json:"active"`
	CreatedBy    string     `json:"created_by"`
	CreatedAt    time.Time  `json:"created_at"`
	LastUsedAt   *time.Time `json:"last_used_at,omitempty"`
}

// externalAgentKeyPrefix distinguishes an external-agent key from a
// service-account key (wr_sa_) at a glance, and lets ParseExternalAgentKey
// reject the wrong credential type with a clear error.
const externalAgentKeyPrefix = "wr_xa_"

// FormatExternalAgentKey renders the shown-once key: wr_xa_<id>.<secret>.
func FormatExternalAgentKey(id uuid.UUID, secret string) string {
	return externalAgentKeyPrefix + id.String() + "." + secret
}

// NewExternalAgentKey mints a fresh credential: it generates the id + secret,
// stores only the argon2 hash, and returns the row plus the shown-once
// plaintext key (wr_xa_<id>.<secret>) the caller surfaces to the admin exactly
// once. Reuses the service-account key primitives (NewAPIKeySecret/HashSecret).
func NewExternalAgentKey(tenantID uuid.UUID, agentID string, agentVersion int, scopes []string, label, createdBy string, now time.Time) (*ExternalAgentKey, string, error) {
	secret, err := NewAPIKeySecret()
	if err != nil {
		return nil, "", err
	}
	hash, err := HashSecret(secret)
	if err != nil {
		return nil, "", err
	}
	id := uuid.New()
	if scopes == nil {
		scopes = []string{}
	}
	k := &ExternalAgentKey{
		ID: id, TenantID: tenantID, AgentID: agentID, AgentVersion: agentVersion,
		Scopes: scopes, SecretHash: hash, Label: label, Active: true,
		CreatedBy: createdBy, CreatedAt: now,
	}
	return k, FormatExternalAgentKey(id, secret), nil
}

// ParseExternalAgentKey splits a presented key into its credential id + secret.
func ParseExternalAgentKey(key string) (uuid.UUID, string, error) {
	if !strings.HasPrefix(key, externalAgentKeyPrefix) {
		return uuid.Nil, "", EUnauthenticated("malformed external-agent key")
	}
	rest := strings.TrimPrefix(key, externalAgentKeyPrefix)
	idStr, secret, ok := strings.Cut(rest, ".")
	if !ok || secret == "" {
		return uuid.Nil, "", EUnauthenticated("malformed external-agent key")
	}
	id, err := uuid.Parse(idStr)
	if err != nil {
		return uuid.Nil, "", EUnauthenticated("malformed external-agent key")
	}
	return id, secret, nil
}
