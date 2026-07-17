package domain

import (
	"context"
	"strings"
	"time"

	"github.com/google/uuid"
)

// ServiceAccountService owns tenant API keys (IDN-FR-031/033, BR-11).
type ServiceAccountService struct {
	Store    Store
	Denylist Denylist
	Clock    func() time.Time
}

func (s *ServiceAccountService) now() time.Time { return s.Clock().UTC() }

// CreateServiceAccountRequest is the POST /service-accounts body.
type CreateServiceAccountRequest struct {
	Name      string     `json:"name"`
	Scopes    []string   `json:"scopes"`
	ExpiresAt *time.Time `json:"expires_at,omitempty"`
}

// CreatedServiceAccount carries the shown-once key (IDN-FR-031, BR-11).
type CreatedServiceAccount struct {
	ServiceAccount *ServiceAccount `json:"service_account"`
	// APIKey is returned exactly once and never retrievable again.
	APIKey string `json:"api_key"`
}

func (s *ServiceAccountService) Create(ctx context.Context, tenantID uuid.UUID, req CreateServiceAccountRequest, actor Actor) (*CreatedServiceAccount, error) {
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return nil, EValidation("name is required", FieldError{Field: "name", Message: "required"})
	}
	if len(req.Scopes) == 0 {
		return nil, EValidation("at least one scope is required", FieldError{Field: "scopes", Message: "required"})
	}
	for _, sc := range req.Scopes {
		// MASTER-FR-016 action naming: <service>.<resource>.<verb>
		if strings.Count(sc, ".") != 2 {
			return nil, EValidation("invalid scope: "+sc, FieldError{Field: "scopes", Message: "scopes must be <service>.<resource>.<verb>"})
		}
	}
	n, err := s.Store.CountServiceAccounts(ctx, tenantID)
	if err != nil {
		return nil, err
	}
	if n >= MaxServiceAccountsPerTenant {
		return nil, EValidation("service account limit reached (max 20 per tenant)")
	}
	now := s.now()
	id, _ := uuid.NewV7()
	secret, err := NewAPIKeySecret()
	if err != nil {
		return nil, err
	}
	hash, err := HashSecret(secret)
	if err != nil {
		return nil, err
	}
	sa := &ServiceAccount{
		ID: id, TenantID: tenantID, Name: name, SecretHash: hash,
		Scopes: req.Scopes, ExpiresAt: req.ExpiresAt, CreatedAt: now, UpdatedAt: now,
	}
	if err := s.Store.CreateServiceAccount(ctx, sa,
		NewEvent(EvSvcAccountCreated, tenantID, actor, sa.URN(), now, map[string]any{"name": name})); err != nil {
		return nil, err
	}
	return &CreatedServiceAccount{ServiceAccount: sa, APIKey: FormatAPIKey(id, secret)}, nil
}

// Rotate issues a new secret; the old one keeps working for RotationOverlap
// (IDN-FR-033 create-new + deprecate-old with overlap).
func (s *ServiceAccountService) Rotate(ctx context.Context, tenantID, id uuid.UUID, actor Actor) (*CreatedServiceAccount, error) {
	sa, err := s.Store.GetServiceAccount(ctx, tenantID, id)
	if err != nil {
		return nil, err
	}
	if sa.RevokedAt != nil {
		return nil, EConflict("service account is revoked")
	}
	now := s.now()
	secret, err := NewAPIKeySecret()
	if err != nil {
		return nil, err
	}
	hash, err := HashSecret(secret)
	if err != nil {
		return nil, err
	}
	old := sa.SecretHash
	oldExp := now.Add(RotationOverlap)
	sa.OldSecretHash = &old
	sa.OldSecretExpiresAt = &oldExp
	sa.SecretHash = hash
	sa.UpdatedAt = now
	if err := s.Store.UpdateServiceAccount(ctx, sa,
		NewEvent(EvSvcAccountRotated, tenantID, actor, sa.URN(), now, nil)); err != nil {
		return nil, err
	}
	return &CreatedServiceAccount{ServiceAccount: sa, APIKey: FormatAPIKey(id, secret)}, nil
}

// Revoke revokes immediately: DB flag + edge denylist (IDN-FR-033, AC-11).
func (s *ServiceAccountService) Revoke(ctx context.Context, tenantID, id uuid.UUID, actor Actor) error {
	sa, err := s.Store.GetServiceAccount(ctx, tenantID, id)
	if err != nil {
		return err
	}
	if sa.RevokedAt != nil {
		return nil // idempotent
	}
	now := s.now()
	sa.RevokedAt = &now
	sa.UpdatedAt = now
	if err := s.Store.UpdateServiceAccount(ctx, sa,
		NewEvent(EvSvcAccountRevoked, tenantID, actor, sa.URN(), now, nil)); err != nil {
		return err
	}
	s.Denylist.Revoke(id.String())
	return nil
}

// CredentialInventory implements US-8: every active credential per tenant
// with last-used timestamps.
type CredentialEntry struct {
	Kind       string     `json:"kind"` // user | service_account | agent_principal
	ID         string     `json:"id"`
	Name       string     `json:"name"`
	Status     string     `json:"status"`
	LastUsedAt *time.Time `json:"last_used_at,omitempty"`
}

func (s *ServiceAccountService) CredentialInventory(ctx context.Context, tenantID uuid.UUID) ([]CredentialEntry, error) {
	out := []CredentialEntry{}
	users, _, err := s.Store.ListUsers(ctx, tenantID, UserFilter{}, PageRequest{Limit: MaxPageLimit})
	if err != nil {
		return nil, err
	}
	for _, u := range users {
		if u.DeletedAt != nil {
			continue
		}
		out = append(out, CredentialEntry{Kind: "user", ID: u.ID.String(), Name: u.Email, Status: string(u.Status), LastUsedAt: u.LastLoginAt})
	}
	sas, _, err := s.Store.ListServiceAccounts(ctx, tenantID, PageRequest{Limit: MaxPageLimit})
	if err != nil {
		return nil, err
	}
	for _, sa := range sas {
		status := "active"
		if sa.RevokedAt != nil {
			status = "revoked"
		}
		out = append(out, CredentialEntry{Kind: "service_account", ID: sa.ID.String(), Name: sa.Name, Status: status, LastUsedAt: sa.LastUsedAt})
	}
	agents, err := s.Store.ListAgentPrincipals(ctx, tenantID)
	if err != nil {
		return nil, err
	}
	for _, a := range agents {
		out = append(out, CredentialEntry{Kind: "agent_principal", ID: a.AgentID + "@" + a.AgentVersion, Name: a.AgentID, Status: string(a.Status)})
	}
	return out, nil
}
