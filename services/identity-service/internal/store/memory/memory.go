// Package memory is the in-memory domain.Store used by the unit test tier
// (CONVENTIONS.md tier 1: no external dependencies). It mirrors the postgres
// store's semantics: uniqueness violations, CAS status transitions, and
// atomic mutation+outbox appends.
package memory

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/datacern-ai/identity-service/internal/domain"
)

type Store struct {
	mu sync.RWMutex

	tenants        map[uuid.UUID]*domain.Tenant
	platformAdmins map[uuid.UUID]*domain.PlatformAdmin
	cells          map[uuid.UUID]*domain.Cell
	modules      map[uuid.UUID][]string // tenantID -> modules
	steps        map[string]*domain.ProvisioningStep
	users        map[uuid.UUID]*domain.User
	invitations  map[uuid.UUID]*domain.Invitation
	serviceAccts map[uuid.UUID]*domain.ServiceAccount
	apiKeyIndex  map[uuid.UUID]uuid.UUID // saID -> tenantID
	agents       map[string]*domain.AgentPrincipal
	signingKeys  map[string]*domain.SigningKey
	embedConfigs map[uuid.UUID]*domain.TenantEmbedConfig
	branding     map[uuid.UUID]*domain.TenantBranding
	extAgentKeys map[uuid.UUID]*domain.ExternalAgentKey
	idpConfigs   map[uuid.UUID]*domain.TenantIdpConfig
	labels       map[uuid.UUID]map[string]*domain.DisplayLabel
	idempotency  map[string]*domain.IdempotencyRecord
	outbox       []*domain.OutboxEvent
}

func New() *Store {
	return &Store{
		tenants:        map[uuid.UUID]*domain.Tenant{},
		platformAdmins: map[uuid.UUID]*domain.PlatformAdmin{},
		cells:          map[uuid.UUID]*domain.Cell{},
		modules:      map[uuid.UUID][]string{},
		steps:        map[string]*domain.ProvisioningStep{},
		users:        map[uuid.UUID]*domain.User{},
		invitations:  map[uuid.UUID]*domain.Invitation{},
		serviceAccts: map[uuid.UUID]*domain.ServiceAccount{},
		apiKeyIndex:  map[uuid.UUID]uuid.UUID{},
		agents:       map[string]*domain.AgentPrincipal{},
		signingKeys:  map[string]*domain.SigningKey{},
		embedConfigs: map[uuid.UUID]*domain.TenantEmbedConfig{},
		branding:     map[uuid.UUID]*domain.TenantBranding{},
		extAgentKeys: map[uuid.UUID]*domain.ExternalAgentKey{},
		idpConfigs:   map[uuid.UUID]*domain.TenantIdpConfig{},
		labels:       map[uuid.UUID]map[string]*domain.DisplayLabel{},
		idempotency:  map[string]*domain.IdempotencyRecord{},
	}
}

func (s *Store) appendOutboxLocked(evs []domain.OutboxEvent) {
	for _, ev := range evs {
		e := ev
		s.outbox = append(s.outbox, &e)
	}
}

// --- platform admins (RLS-exempt registry) ---

func (s *Store) IsPlatformAdmin(_ context.Context, sub, email string) (bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, pa := range s.platformAdmins {
		if (sub != "" && pa.UserSub == sub) || (email != "" && strings.EqualFold(pa.Email, email)) {
			return true, nil
		}
	}
	return false, nil
}

func (s *Store) ListPlatformAdmins(_ context.Context) ([]*domain.PlatformAdmin, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []*domain.PlatformAdmin{}
	for _, pa := range s.platformAdmins {
		cp := *pa
		out = append(out, &cp)
	}
	return out, nil
}

func (s *Store) CreatePlatformAdmin(_ context.Context, pa *domain.PlatformAdmin) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *pa
	cp.Email = strings.ToLower(cp.Email)
	s.platformAdmins[pa.ID] = &cp
	return nil
}

func (s *Store) DeletePlatformAdmin(_ context.Context, id uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.platformAdmins, id)
	return nil
}

// --- tenants ---

func (s *Store) CreateTenant(_ context.Context, t *domain.Tenant, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, ex := range s.tenants {
		if ex.Name == t.Name || ex.Subdomain == t.Subdomain ||
			ex.K8sNamespace == t.K8sNamespace || ex.SchemaPrefix == t.SchemaPrefix {
			// AC-4 / BR-1: no partial creation on collision.
			return domain.EValidation("tenant name (or a derived identifier) already exists",
				domain.FieldError{Field: "name", Message: "already in use"})
		}
	}
	cp := *t
	s.tenants[t.ID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) GetTenant(_ context.Context, id uuid.UUID) (*domain.Tenant, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	t, ok := s.tenants[id]
	if !ok {
		return nil, domain.ENotFound("tenant")
	}
	cp := *t
	return &cp, nil
}

func (s *Store) GetTenantByName(_ context.Context, name string) (*domain.Tenant, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, t := range s.tenants {
		if t.Name == name {
			cp := *t
			return &cp, nil
		}
	}
	return nil, domain.ENotFound("tenant")
}

func (s *Store) ListTenants(_ context.Context, f domain.TenantFilter, page domain.PageRequest) ([]*domain.Tenant, domain.PageInfo, error) {
	s.mu.RLock()
	all := make([]*domain.Tenant, 0, len(s.tenants))
	for _, t := range s.tenants {
		if f.Status != "" && string(t.Status) != f.Status {
			continue
		}
		if f.Cloud != "" && t.Cloud != f.Cloud {
			continue
		}
		if f.CellID != "" && (t.CellID == nil || t.CellID.String() != f.CellID) {
			continue
		}
		cp := *t
		all = append(all, &cp)
	}
	s.mu.RUnlock()
	sort.Slice(all, func(i, j int) bool { return all[i].ID.String() < all[j].ID.String() })
	all = afterID(all, page.AfterID, func(t *domain.Tenant) uuid.UUID { return t.ID })
	all = capLen(all, page.Limit+1)
	items, info := domain.BuildPage(all, page.Limit, func(t *domain.Tenant) uuid.UUID { return t.ID })
	return items, info, nil
}

func (s *Store) UpdateTenant(_ context.Context, t *domain.Tenant, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.tenants[t.ID]; !ok {
		return domain.ENotFound("tenant")
	}
	cp := *t
	s.tenants[t.ID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) TransitionTenant(_ context.Context, id uuid.UUID, from, to domain.TenantStatus, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tenants[id]
	if !ok {
		return domain.ENotFound("tenant")
	}
	if t.Status != from {
		return domain.EConflict("tenant status is " + string(t.Status) + ", expected " + string(from))
	}
	if !domain.CanTransition(from, to) {
		return domain.EConflict("invalid tenant status transition " + string(from) + " -> " + string(to))
	}
	t.Status = to
	t.UpdatedAt = time.Now().UTC()
	s.appendOutboxLocked(evs)
	return nil
}

// --- cells ---

func (s *Store) CreateCell(_ context.Context, c *domain.Cell) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *c
	s.cells[c.ID] = &cp
	return nil
}

func (s *Store) ListCells(_ context.Context) ([]*domain.Cell, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*domain.Cell, 0, len(s.cells))
	for _, c := range s.cells {
		cp := *c
		out = append(out, &cp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, nil
}

func (s *Store) ReserveCell(_ context.Context, cellID uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.cells[cellID]
	if !ok {
		return domain.ENotFound("cell")
	}
	if c.TenantCount >= c.Capacity {
		return domain.EConflict("cell at capacity")
	}
	c.TenantCount++
	return nil
}

func (s *Store) ReleaseCell(_ context.Context, cellID uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.cells[cellID]
	if !ok {
		return domain.ENotFound("cell")
	}
	if c.TenantCount > 0 {
		c.TenantCount--
	}
	return nil
}

// --- tenant modules ---

func (s *Store) SetTenantModules(_ context.Context, tenantID uuid.UUID, modules []string, _ string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.modules[tenantID] = append([]string(nil), modules...)
	return nil
}

func (s *Store) DeleteTenantModules(_ context.Context, tenantID uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.modules, tenantID)
	return nil
}

func (s *Store) GetTenantModules(_ context.Context, tenantID uuid.UUID) ([]string, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return append([]string(nil), s.modules[tenantID]...), nil
}

func (s *Store) GetTenantEmbedConfig(_ context.Context, tenantID uuid.UUID) (*domain.TenantEmbedConfig, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	c, ok := s.embedConfigs[tenantID]
	if !ok {
		return nil, domain.ENotFound("embed config")
	}
	cp := *c
	return &cp, nil
}

func (s *Store) UpsertTenantEmbedConfig(_ context.Context, cfg *domain.TenantEmbedConfig) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *cfg
	s.embedConfigs[cfg.TenantID] = &cp
	return nil
}

// --- white-label branding (BRD 59 WS3) ---

func (s *Store) GetTenantBranding(_ context.Context, tenantID uuid.UUID) (*domain.TenantBranding, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	b, ok := s.branding[tenantID]
	if !ok {
		return nil, domain.ENotFound("tenant branding")
	}
	cp := *b
	return &cp, nil
}

func (s *Store) UpsertTenantBranding(_ context.Context, b *domain.TenantBranding) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *b
	s.branding[b.TenantID] = &cp
	return nil
}

func (s *Store) DeleteTenantBranding(_ context.Context, tenantID uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.branding, tenantID)
	return nil
}

// --- self-service external-agent credentials (BRD 60 WS2) ---

func (s *Store) CreateExternalAgentKey(_ context.Context, k *domain.ExternalAgentKey) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *k
	s.extAgentKeys[k.ID] = &cp
	return nil
}

func (s *Store) GetExternalAgentKey(_ context.Context, id uuid.UUID) (*domain.ExternalAgentKey, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	k, ok := s.extAgentKeys[id]
	if !ok {
		return nil, domain.ENotFound("external agent key")
	}
	cp := *k
	return &cp, nil
}

func (s *Store) ListExternalAgentKeys(_ context.Context, tenantID uuid.UUID) ([]*domain.ExternalAgentKey, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var out []*domain.ExternalAgentKey
	for _, k := range s.extAgentKeys {
		if k.TenantID == tenantID {
			cp := *k
			out = append(out, &cp)
		}
	}
	return out, nil
}

func (s *Store) RevokeExternalAgentKey(_ context.Context, tenantID, id uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	k, ok := s.extAgentKeys[id]
	if !ok || k.TenantID != tenantID {
		return domain.ENotFound("external agent key")
	}
	k.Active = false
	return nil
}

func (s *Store) TouchExternalAgentKey(_ context.Context, id uuid.UUID, t time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if k, ok := s.extAgentKeys[id]; ok {
		k.LastUsedAt = &t
	}
	return nil
}

// --- per-tenant OIDC IdP config (BYO-P4) ---

func (s *Store) GetTenantIdpConfig(_ context.Context, tenantID uuid.UUID) (*domain.TenantIdpConfig, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	c, ok := s.idpConfigs[tenantID]
	if !ok {
		return nil, domain.ENotFound("idp config")
	}
	cp := *c
	return &cp, nil
}

func (s *Store) GetTenantIdpConfigByIssuer(_ context.Context, issuer string) (*domain.TenantIdpConfig, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, c := range s.idpConfigs {
		if c.Issuer == issuer {
			cp := *c
			return &cp, nil
		}
	}
	return nil, domain.ENotFound("idp config")
}

func (s *Store) UpsertTenantIdpConfig(_ context.Context, cfg *domain.TenantIdpConfig) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *cfg
	s.idpConfigs[cfg.TenantID] = &cp
	return nil
}

func (s *Store) DeleteTenantIdpConfig(_ context.Context, tenantID uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.idpConfigs, tenantID)
	return nil
}

func (s *Store) ListTenantDisplayLabels(_ context.Context, tenantID uuid.UUID) ([]domain.DisplayLabel, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []domain.DisplayLabel{}
	for _, l := range s.labels[tenantID] {
		out = append(out, *l)
	}
	return out, nil
}

func (s *Store) UpsertTenantDisplayLabel(_ context.Context, l *domain.DisplayLabel) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.labels[l.TenantID] == nil {
		s.labels[l.TenantID] = map[string]*domain.DisplayLabel{}
	}
	cp := *l
	s.labels[l.TenantID][l.Key] = &cp
	return nil
}

func (s *Store) DeleteTenantDisplayLabel(_ context.Context, tenantID uuid.UUID, key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if m := s.labels[tenantID]; m != nil {
		delete(m, key)
	}
	return nil
}

// --- provisioning steps ---

func stepKey(wfID string, idx int) string { return fmt.Sprintf("%s#%d", wfID, idx) }

func (s *Store) SaveProvisioningStep(_ context.Context, rec *domain.ProvisioningStep) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *rec
	s.steps[stepKey(rec.WorkflowID, rec.StepIndex)] = &cp
	return nil
}

func (s *Store) ListProvisioningSteps(_ context.Context, tenantID uuid.UUID, workflowID string) ([]*domain.ProvisioningStep, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []*domain.ProvisioningStep{}
	for _, st := range s.steps {
		if st.TenantID == tenantID && st.WorkflowID == workflowID {
			cp := *st
			out = append(out, &cp)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].StepIndex < out[j].StepIndex })
	return out, nil
}

// --- users ---

func (s *Store) CreateUser(_ context.Context, u *domain.User, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, ex := range s.users {
		if ex.TenantID == u.TenantID && strings.EqualFold(ex.Email, u.Email) {
			return domain.EConflict("user email already exists in tenant")
		}
	}
	cp := *u
	s.users[u.ID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) GetUser(_ context.Context, tenantID, id uuid.UUID) (*domain.User, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	u, ok := s.users[id]
	if !ok || u.TenantID != tenantID { // cross-tenant reads look like 404 (MASTER-FR-003)
		return nil, domain.ENotFound("user")
	}
	cp := *u
	return &cp, nil
}

func (s *Store) GetUserByEmail(_ context.Context, tenantID uuid.UUID, email string) (*domain.User, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, u := range s.users {
		if u.TenantID == tenantID && strings.EqualFold(u.Email, email) {
			cp := *u
			return &cp, nil
		}
	}
	return nil, domain.ENotFound("user")
}

func (s *Store) GetUserBySub(_ context.Context, tenantID uuid.UUID, sub string) (*domain.User, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, u := range s.users {
		if u.TenantID == tenantID && u.IdpSubject != nil && *u.IdpSubject == sub {
			cp := *u
			return &cp, nil
		}
	}
	return nil, domain.ENotFound("user")
}

func (s *Store) ListUsers(_ context.Context, tenantID uuid.UUID, f domain.UserFilter, page domain.PageRequest) ([]*domain.User, domain.PageInfo, error) {
	idSet := map[uuid.UUID]bool{}
	for _, id := range f.IDs {
		idSet[id] = true
	}
	s.mu.RLock()
	all := []*domain.User{}
	for _, u := range s.users {
		if u.TenantID != tenantID {
			continue
		}
		if len(idSet) > 0 && !idSet[u.ID] {
			continue
		}
		if f.Status != "" && string(u.Status) != f.Status {
			continue
		}
		cp := *u
		all = append(all, &cp)
	}
	s.mu.RUnlock()
	sort.Slice(all, func(i, j int) bool { return all[i].ID.String() < all[j].ID.String() })
	all = afterID(all, page.AfterID, func(u *domain.User) uuid.UUID { return u.ID })
	all = capLen(all, page.Limit+1)
	items, info := domain.BuildPage(all, page.Limit, func(u *domain.User) uuid.UUID { return u.ID })
	return items, info, nil
}

func (s *Store) UpdateUser(_ context.Context, u *domain.User, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	ex, ok := s.users[u.ID]
	if !ok || ex.TenantID != u.TenantID {
		return domain.ENotFound("user")
	}
	cp := *u
	s.users[u.ID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

// --- invitations ---

func (s *Store) CreateInvitation(_ context.Context, inv *domain.Invitation, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *inv
	s.invitations[inv.ID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) GetInvitationByTokenHash(_ context.Context, tokenHash string) (*domain.Invitation, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, inv := range s.invitations {
		if inv.TokenHash == tokenHash {
			cp := *inv
			return &cp, nil
		}
	}
	return nil, domain.ENotFound("invitation")
}

func (s *Store) UpdateInvitation(_ context.Context, inv *domain.Invitation, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.invitations[inv.ID]; !ok {
		return domain.ENotFound("invitation")
	}
	cp := *inv
	s.invitations[inv.ID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) InvalidateInvitations(_ context.Context, tenantID, userID uuid.UUID, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, inv := range s.invitations {
		if inv.TenantID == tenantID && inv.UserID == userID && inv.AcceptedAt == nil && inv.InvalidatedAt == nil {
			n := now
			inv.InvalidatedAt = &n
			inv.UpdatedAt = n
		}
	}
	return nil
}

// --- service accounts ---

func (s *Store) CreateServiceAccount(_ context.Context, sa *domain.ServiceAccount, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, ex := range s.serviceAccts {
		if ex.TenantID == sa.TenantID && ex.Name == sa.Name {
			return domain.EConflict("service account name already exists")
		}
	}
	cp := *sa
	s.serviceAccts[sa.ID] = &cp
	s.apiKeyIndex[sa.ID] = sa.TenantID
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) GetServiceAccount(_ context.Context, tenantID, id uuid.UUID) (*domain.ServiceAccount, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	sa, ok := s.serviceAccts[id]
	if !ok || sa.TenantID != tenantID {
		return nil, domain.ENotFound("service account")
	}
	cp := *sa
	return &cp, nil
}

func (s *Store) ResolveAPIKeyTenant(_ context.Context, saID uuid.UUID) (uuid.UUID, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	t, ok := s.apiKeyIndex[saID]
	if !ok {
		return uuid.Nil, domain.ENotFound("api key")
	}
	return t, nil
}

func (s *Store) ListServiceAccounts(_ context.Context, tenantID uuid.UUID, page domain.PageRequest) ([]*domain.ServiceAccount, domain.PageInfo, error) {
	s.mu.RLock()
	all := []*domain.ServiceAccount{}
	for _, sa := range s.serviceAccts {
		if sa.TenantID == tenantID {
			cp := *sa
			all = append(all, &cp)
		}
	}
	s.mu.RUnlock()
	sort.Slice(all, func(i, j int) bool { return all[i].ID.String() < all[j].ID.String() })
	all = afterID(all, page.AfterID, func(sa *domain.ServiceAccount) uuid.UUID { return sa.ID })
	all = capLen(all, page.Limit+1)
	items, info := domain.BuildPage(all, page.Limit, func(sa *domain.ServiceAccount) uuid.UUID { return sa.ID })
	return items, info, nil
}

func (s *Store) CountServiceAccounts(_ context.Context, tenantID uuid.UUID) (int, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	n := 0
	for _, sa := range s.serviceAccts {
		if sa.TenantID == tenantID && sa.RevokedAt == nil {
			n++
		}
	}
	return n, nil
}

func (s *Store) UpdateServiceAccount(_ context.Context, sa *domain.ServiceAccount, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	ex, ok := s.serviceAccts[sa.ID]
	if !ok || ex.TenantID != sa.TenantID {
		return domain.ENotFound("service account")
	}
	cp := *sa
	s.serviceAccts[sa.ID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

// --- agent principals ---

func agentKey(tenantID uuid.UUID, agentID, version string) string {
	return tenantID.String() + "/" + agentID + "@" + version
}

func (s *Store) UpsertAgentPrincipal(_ context.Context, a *domain.AgentPrincipal, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *a
	s.agents[agentKey(a.TenantID, a.AgentID, a.AgentVersion)] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) GetAgentPrincipal(_ context.Context, tenantID uuid.UUID, agentID, version string) (*domain.AgentPrincipal, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	a, ok := s.agents[agentKey(tenantID, agentID, version)]
	if !ok {
		return nil, domain.ENotFound("agent principal")
	}
	cp := *a
	return &cp, nil
}

func (s *Store) ListAgentPrincipals(_ context.Context, tenantID uuid.UUID) ([]*domain.AgentPrincipal, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []*domain.AgentPrincipal{}
	for _, a := range s.agents {
		if a.TenantID == tenantID {
			cp := *a
			out = append(out, &cp)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].AgentID+out[i].AgentVersion < out[j].AgentID+out[j].AgentVersion })
	return out, nil
}

// --- signing keys ---

func (s *Store) SaveSigningKey(_ context.Context, k *domain.SigningKey, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *k
	s.signingKeys[k.KID] = &cp
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) ListSigningKeys(_ context.Context) ([]*domain.SigningKey, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []*domain.SigningKey{}
	for _, k := range s.signingKeys {
		cp := *k
		out = append(out, &cp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].NotBefore.Before(out[j].NotBefore) })
	return out, nil
}

func (s *Store) UpdateSigningKey(_ context.Context, k *domain.SigningKey) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.signingKeys[k.KID]; !ok {
		return domain.ENotFound("signing key")
	}
	cp := *k
	s.signingKeys[k.KID] = &cp
	return nil
}

// --- idempotency ---

func idemKey(tenantID uuid.UUID, key string) string { return tenantID.String() + "/" + key }

func (s *Store) GetIdempotency(_ context.Context, tenantID uuid.UUID, key string) (*domain.IdempotencyRecord, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	rec, ok := s.idempotency[idemKey(tenantID, key)]
	if !ok {
		return nil, domain.ENotFound("idempotency key")
	}
	if time.Since(rec.CreatedAt) > domain.IdempotencyTTL {
		return nil, domain.ENotFound("idempotency key")
	}
	cp := *rec
	return &cp, nil
}

func (s *Store) PutIdempotency(_ context.Context, rec *domain.IdempotencyRecord) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *rec
	s.idempotency[idemKey(rec.TenantID, rec.Key)] = &cp
	return nil
}

// --- outbox ---

func (s *Store) AppendOutbox(_ context.Context, evs ...domain.OutboxEvent) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.appendOutboxLocked(evs)
	return nil
}

func (s *Store) ListOutbox(_ context.Context, limit int) ([]*domain.OutboxEvent, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []*domain.OutboxEvent{}
	for _, ev := range s.outbox {
		if ev.PublishedAt == nil {
			cp := *ev
			out = append(out, &cp)
			if limit > 0 && len(out) >= limit {
				break
			}
		}
	}
	return out, nil
}

func (s *Store) MarkOutboxPublished(_ context.Context, eventIDs []uuid.UUID, at time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	ids := map[uuid.UUID]bool{}
	for _, id := range eventIDs {
		ids[id] = true
	}
	for _, ev := range s.outbox {
		if ids[ev.EventID] {
			t := at
			ev.PublishedAt = &t
		}
	}
	return nil
}

// EventsOfType is a test helper: all outbox events of a type (any tenant).
func (s *Store) EventsOfType(eventType string) []*domain.OutboxEvent {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []*domain.OutboxEvent{}
	for _, ev := range s.outbox {
		if ev.EventType == eventType {
			cp := *ev
			out = append(out, &cp)
		}
	}
	return out
}

// --- helpers ---

func afterID[T any](items []T, after *uuid.UUID, idOf func(T) uuid.UUID) []T {
	if after == nil {
		return items
	}
	out := items[:0]
	for _, it := range items {
		if idOf(it).String() > after.String() {
			out = append(out, it)
		}
	}
	return out
}

func capLen[T any](items []T, n int) []T {
	if len(items) > n {
		return items[:n]
	}
	return items
}
