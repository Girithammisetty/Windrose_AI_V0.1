package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// Store is the persistence port. Two implementations exist:
//   - store/memory: in-memory fake for the unit test tier
//   - store/postgres: pgx implementation with RLS (integration tier)
//
// Every mutating method accepts trailing outbox events which MUST be
// persisted atomically with the mutation (MASTER-FR-034, BR-12).
type Store interface {
	// --- tenants (platform-scoped, RLS-exempt; BRD §4) ---
	CreateTenant(ctx context.Context, t *Tenant, evs ...OutboxEvent) error
	GetTenant(ctx context.Context, id uuid.UUID) (*Tenant, error)
	GetTenantByName(ctx context.Context, name string) (*Tenant, error)
	ListTenants(ctx context.Context, f TenantFilter, page PageRequest) ([]*Tenant, PageInfo, error)
	UpdateTenant(ctx context.Context, t *Tenant, evs ...OutboxEvent) error
	// TransitionTenant is a compare-and-set status change: fails with
	// CONFLICT if the stored status differs from `from` (guarded state
	// machine at the persistence boundary, IDN-FR-003).
	TransitionTenant(ctx context.Context, id uuid.UUID, from, to TenantStatus, evs ...OutboxEvent) error

	// --- platform admins (platform-scoped, RLS-exempt; first-class cross-tenant
	// operator, distinct from the per-tenant "Admin" role) ---
	IsPlatformAdmin(ctx context.Context, sub, email string) (bool, error)
	ListPlatformAdmins(ctx context.Context) ([]*PlatformAdmin, error)
	CreatePlatformAdmin(ctx context.Context, pa *PlatformAdmin) error
	DeletePlatformAdmin(ctx context.Context, id uuid.UUID) error

	// --- cells ---
	CreateCell(ctx context.Context, c *Cell) error
	ListCells(ctx context.Context) ([]*Cell, error)
	// ReserveCell atomically increments tenant_count if capacity allows.
	ReserveCell(ctx context.Context, cellID uuid.UUID) error
	ReleaseCell(ctx context.Context, cellID uuid.UUID) error

	// --- tenant modules (IDN-FR-005) ---
	SetTenantModules(ctx context.Context, tenantID uuid.UUID, modules []string, version string) error
	DeleteTenantModules(ctx context.Context, tenantID uuid.UUID) error
	GetTenantModules(ctx context.Context, tenantID uuid.UUID) ([]string, error)

	// --- provisioning runs (IDN-FR-006/007) ---
	SaveProvisioningStep(ctx context.Context, s *ProvisioningStep) error
	ListProvisioningSteps(ctx context.Context, tenantID uuid.UUID, workflowID string) ([]*ProvisioningStep, error)

	// --- users (tenant-scoped, RLS) ---
	CreateUser(ctx context.Context, u *User, evs ...OutboxEvent) error
	GetUser(ctx context.Context, tenantID, id uuid.UUID) (*User, error)
	GetUserByEmail(ctx context.Context, tenantID uuid.UUID, email string) (*User, error)
	GetUserBySub(ctx context.Context, tenantID uuid.UUID, sub string) (*User, error)
	ListUsers(ctx context.Context, tenantID uuid.UUID, f UserFilter, page PageRequest) ([]*User, PageInfo, error)
	UpdateUser(ctx context.Context, u *User, evs ...OutboxEvent) error

	// --- invitations (tenant-scoped, RLS) ---
	CreateInvitation(ctx context.Context, inv *Invitation, evs ...OutboxEvent) error
	// GetInvitationByTokenHash is tenant-less: accept links are public
	// (pre-auth). The postgres impl uses a SECURITY-scoped lookup.
	GetInvitationByTokenHash(ctx context.Context, tokenHash string) (*Invitation, error)
	UpdateInvitation(ctx context.Context, inv *Invitation, evs ...OutboxEvent) error
	// InvalidateInvitations invalidates all open invitations for a user
	// (resend invalidates old tokens, AC-5).
	InvalidateInvitations(ctx context.Context, tenantID, userID uuid.UUID, now time.Time) error

	// --- service accounts / api keys (tenant-scoped, RLS) ---
	CreateServiceAccount(ctx context.Context, sa *ServiceAccount, evs ...OutboxEvent) error
	GetServiceAccount(ctx context.Context, tenantID, id uuid.UUID) (*ServiceAccount, error)
	// --- embedded-UI config (IDN-FR-043) ---
	GetTenantEmbedConfig(ctx context.Context, tenantID uuid.UUID) (*TenantEmbedConfig, error)
	UpsertTenantEmbedConfig(ctx context.Context, cfg *TenantEmbedConfig) error

	// --- per-tenant OIDC IdP config (BYO-P4) ---
	GetTenantIdpConfig(ctx context.Context, tenantID uuid.UUID) (*TenantIdpConfig, error)
	// GetTenantIdpConfigByIssuer routes an inbound ID token to its tenant by the
	// token's `iss` claim (issuer is globally unique). Read on the login path.
	GetTenantIdpConfigByIssuer(ctx context.Context, issuer string) (*TenantIdpConfig, error)
	UpsertTenantIdpConfig(ctx context.Context, cfg *TenantIdpConfig) error
	DeleteTenantIdpConfig(ctx context.Context, tenantID uuid.UUID) error

	// --- per-tenant display-label overlays (BRD 23 inc3) ---
	ListTenantDisplayLabels(ctx context.Context, tenantID uuid.UUID) ([]DisplayLabel, error)
	UpsertTenantDisplayLabel(ctx context.Context, l *DisplayLabel) error
	DeleteTenantDisplayLabel(ctx context.Context, tenantID uuid.UUID, key string) error

	// ResolveAPIKeyTenant maps a service-account id to its tenant via the
	// platform-scoped api_key_index table (pre-auth edge exchange).
	ResolveAPIKeyTenant(ctx context.Context, saID uuid.UUID) (uuid.UUID, error)
	ListServiceAccounts(ctx context.Context, tenantID uuid.UUID, page PageRequest) ([]*ServiceAccount, PageInfo, error)
	CountServiceAccounts(ctx context.Context, tenantID uuid.UUID) (int, error)
	UpdateServiceAccount(ctx context.Context, sa *ServiceAccount, evs ...OutboxEvent) error

	// --- agent principals (tenant-scoped, RLS; synced from events IDN-FR-040) ---
	UpsertAgentPrincipal(ctx context.Context, a *AgentPrincipal, evs ...OutboxEvent) error
	GetAgentPrincipal(ctx context.Context, tenantID uuid.UUID, agentID, version string) (*AgentPrincipal, error)
	ListAgentPrincipals(ctx context.Context, tenantID uuid.UUID) ([]*AgentPrincipal, error)

	// --- signing keys (platform-scoped registry, IDN-FR-050..052) ---
	SaveSigningKey(ctx context.Context, k *SigningKey, evs ...OutboxEvent) error
	ListSigningKeys(ctx context.Context) ([]*SigningKey, error)
	UpdateSigningKey(ctx context.Context, k *SigningKey) error

	// --- idempotency (MASTER-FR-025) ---
	GetIdempotency(ctx context.Context, tenantID uuid.UUID, key string) (*IdempotencyRecord, error)
	PutIdempotency(ctx context.Context, rec *IdempotencyRecord) error

	// --- outbox ---
	// AppendOutbox writes standalone events (e.g. audit denials) outside a
	// mutation transaction.
	AppendOutbox(ctx context.Context, evs ...OutboxEvent) error
	// ListOutbox returns unpublished events, oldest first (poller + tests).
	ListOutbox(ctx context.Context, limit int) ([]*OutboxEvent, error)
	MarkOutboxPublished(ctx context.Context, eventIDs []uuid.UUID, at time.Time) error
}

// UserFilter narrows ListUsers. IDs is the `filter[id]` batch-hydration
// filter (comma-separated ids on the wire): empty means "no id filter";
// non-empty returns only users whose id is in the set (bff-graphql sends up
// to 100 ids per call to hydrate nested User fields, BFF-FR-030/031).
// Status, when set (e.g. "active"), returns only users in that lifecycle
// state — used by the member-safe assignable-users listing so a deactivated
// or not-yet-activated user is never offered as a case assignee.
type UserFilter struct {
	IDs    []uuid.UUID
	Status string
}

// TenantFilter per MASTER-FR-023 (only indexed fields are filterable).
type TenantFilter struct {
	Status string
	CellID string
	Cloud  string
}

// PlatformAdmin is a first-class, cross-tenant platform operator. It lives in a
// platform-scoped (RLS-exempt) registry — NOT the per-tenant rbac "Admin" role.
// A user matched here (by sub or email) has the platform scopes + platform_admin
// claim injected at login.
type PlatformAdmin struct {
	ID        uuid.UUID `json:"id"`
	UserSub   string    `json:"user_sub,omitempty"`
	Email     string    `json:"email"`
	GrantedBy string    `json:"granted_by,omitempty"`
	GrantedAt time.Time `json:"granted_at"`
}

// SigningKey is the platform key registry row (IDN-FR-050..052).
// Private material never lands here: LocalSigner keeps it in memory (dev),
// Vault keeps it in transit (prod, vault_ref).
type SigningKey struct {
	KID          string     `json:"kid"`
	Alg          string     `json:"alg"`
	VaultRef     string     `json:"vault_ref,omitempty"`
	PublicKeyPEM string     `json:"public_key_pem"`
	NotBefore    time.Time  `json:"not_before"` // published >=10 min before use (IDN-FR-052)
	RetiredAt    *time.Time `json:"retired_at,omitempty"`
	CreatedAt    time.Time  `json:"created_at"`
	UpdatedAt    time.Time  `json:"updated_at"`
}

// IdempotencyRecord stores a completed POST response for 24h replay
// (MASTER-FR-025).
type IdempotencyRecord struct {
	TenantID    uuid.UUID
	Key         string
	RequestHash string
	Status      int
	Body        []byte
	CreatedAt   time.Time
}

// IdempotencyTTL per MASTER-FR-025.
const IdempotencyTTL = 24 * time.Hour

// ProvisioningStep is one persisted step record of a provisioning /
// deprovisioning workflow (provisioning_runs table, IDN-FR-006/007).
type ProvisioningStep struct {
	ID               uuid.UUID  `json:"id"`
	TenantID         uuid.UUID  `json:"tenant_id"`
	WorkflowID       string     `json:"workflow_id"`
	StepIndex        int        `json:"step_index"`
	StepName         string     `json:"step_name"`
	Status           StepStatus `json:"status"`
	Attempt          int        `json:"attempt"`
	Error            string     `json:"error,omitempty"`
	CompensationName string     `json:"compensation,omitempty"` // recorded comp path (AC-2)
	StartedAt        *time.Time `json:"started_at,omitempty"`
	FinishedAt       *time.Time `json:"finished_at,omitempty"`
}

type StepStatus string

const (
	StepPending     StepStatus = "pending"
	StepRunning     StepStatus = "running"
	StepSucceeded   StepStatus = "succeeded"
	StepFailed      StepStatus = "failed"
	StepCompensated StepStatus = "compensated"
)
