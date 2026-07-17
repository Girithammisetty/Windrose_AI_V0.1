package domain

import (
	"context"
	"strings"
	"time"

	"github.com/google/uuid"
)

// DeletionGracePeriod before destructive deletion (IDN-FR-008b).
const DeletionGracePeriod = 7 * 24 * time.Hour

// TenantService owns tenant lifecycle use-cases.
type TenantService struct {
	Store  Store
	Engine ProvisioningEngine
	Graph  ModuleGraph
	Prober HealthProber
	Clock  func() time.Time
	// Async runs provisioning in a goroutine (server mode); tests run inline.
	Async bool
	// ReservedNames extends the reserved list with cell names (IDN-FR-002).
	ReservedNames []string
}

func (s *TenantService) now() time.Time { return s.Clock().UTC() }

// CreateTenantRequest is the POST /tenants body.
type CreateTenantRequest struct {
	Name        string   `json:"name"`
	DisplayName string   `json:"display_name"`
	OwnerEmail  string   `json:"owner_email"`
	Tier        string   `json:"tier"`
	Cloud       string   `json:"cloud"`
	CellID      *string  `json:"cell_id,omitempty"` // explicit cell targeting (BR-3)
	Quotas      *Quotas  `json:"quotas,omitempty"`
	Modules     []string `json:"modules,omitempty"`
	AutoUpgrade bool     `json:"auto_upgrade"`
	Publish     bool     `json:"publish"`
}

// Create validates and creates a tenant (IDN-FR-001/002/004/005, BR-1:
// single transaction, no partial creation). publish=true also starts
// provisioning and returns the workflow id as operation id.
func (s *TenantService) Create(ctx context.Context, req CreateTenantRequest, actor Actor) (*Tenant, string, error) {
	name, sub, ns, prefix, err := NormalizeTenantName(req.Name, s.ReservedNames)
	if err != nil {
		return nil, "", err
	}
	email, err := ValidateEmail(req.OwnerEmail)
	if err != nil {
		return nil, "", err
	}
	if !ValidTiers[req.Tier] {
		return nil, "", EValidation("invalid tier", FieldError{Field: "tier", Message: "must be pool|bridge|silo"})
	}
	if !ValidClouds[strings.ToLower(req.Cloud)] {
		return nil, "", EValidation("invalid cloud", FieldError{Field: "cloud", Message: "must be aws|azure|gcp"})
	}
	modules, err := s.Graph.Resolve(req.Modules)
	if err != nil {
		return nil, "", err
	}
	quotas := DefaultQuotas()
	if req.Quotas != nil {
		quotas = *req.Quotas
		if quotas.CPU <= 0 || quotas.ProcessingCPU <= 0 {
			return nil, "", EValidation("invalid quotas", FieldError{Field: "quotas", Message: "cpu values must be positive"})
		}
	}
	now := s.now()
	id, _ := uuid.NewV7()
	t := &Tenant{
		ID: id, Name: name, DisplayName: firstNonEmpty(req.DisplayName, name), OwnerEmail: email,
		Tier: req.Tier, Cloud: strings.ToLower(req.Cloud), Status: TenantDraft,
		Quotas: quotas, PlatformVersion: "latest", Subdomain: sub, K8sNamespace: ns,
		SchemaPrefix: prefix, AutoUpgrade: req.AutoUpgrade, Modules: modules,
		CreatedBy: actor.ID, CreatedAt: now, UpdatedAt: now,
	}
	// BR-1 / AC-4: uniqueness of name and every derived identifier is
	// enforced in one transaction; a duplicate (case-insensitive, since
	// names are lowercased) creates nothing.
	if err := s.Store.CreateTenant(ctx, t,
		NewEvent(EvTenantCreated, t.ID, actor, t.URN(), now, map[string]any{"name": t.Name, "tier": t.Tier, "cloud": t.Cloud})); err != nil {
		return nil, "", err
	}
	opID := ""
	if req.Publish {
		var err error
		opID, err = s.Publish(ctx, t.ID, actor)
		if err != nil {
			return nil, "", err
		}
	}
	return t, opID, nil
}

// Publish transitions draft -> provisioning and starts the workflow.
// The CAS transition makes concurrent publishes collide with 409 (BR-2).
func (s *TenantService) Publish(ctx context.Context, id uuid.UUID, actor Actor) (string, error) {
	t, err := s.Store.GetTenant(ctx, id)
	if err != nil {
		return "", err
	}
	now := s.now()
	if err := s.Store.TransitionTenant(ctx, id, TenantDraft, TenantProvisioning,
		NewEvent(EvTenantPublished, id, actor, t.URN(), now, nil)); err != nil {
		return "", err
	}
	wfID := WorkflowIDFor(id)
	s.runWorkflow(func() error { return s.Engine.Provision(context.WithoutCancel(ctx), id) })
	return wfID, nil
}

// RetryProvisioning resumes a failed workflow from the failed step (US-2, AC-3).
func (s *TenantService) RetryProvisioning(ctx context.Context, id uuid.UUID, actor Actor) (string, error) {
	t, err := s.Store.GetTenant(ctx, id)
	if err != nil {
		return "", err
	}
	if err := s.Store.TransitionTenant(ctx, id, TenantProvisionFailed, TenantProvisioning,
		NewEvent(EvTenantPublished, id, actor, t.URN(), s.now(), map[string]any{"retry": true})); err != nil {
		return "", err
	}
	s.runWorkflow(func() error { return s.Engine.Provision(context.WithoutCancel(ctx), id) })
	return WorkflowIDFor(id), nil
}

func (s *TenantService) runWorkflow(fn func() error) {
	if s.Async {
		go func() { _ = fn() }()
		return
	}
	_ = fn() // inline (tests): outcome is observable via tenant status + step records
}

// Suspend blocks access, retains infra (IDN-FR-003, BR-4).
func (s *TenantService) Suspend(ctx context.Context, id uuid.UUID, actor Actor) (*Tenant, error) {
	t, err := s.Store.GetTenant(ctx, id)
	if err != nil {
		return nil, err
	}
	if err := s.Store.TransitionTenant(ctx, id, t.Status, TenantSuspended,
		NewEvent(EvTenantSuspended, id, actor, t.URN(), s.now(), nil)); err != nil {
		return nil, err
	}
	return s.Store.GetTenant(ctx, id)
}

// Reactivate runs a Verify probe first and reports drift (BR-5); it never
// re-runs provisioning.
func (s *TenantService) Reactivate(ctx context.Context, id uuid.UUID, actor Actor) (*Tenant, string, error) {
	t, err := s.Store.GetTenant(ctx, id)
	if err != nil {
		return nil, "", err
	}
	drift := ""
	if s.Prober != nil {
		if perr := s.Prober.Probe(ctx, t); perr != nil {
			drift = perr.Error() // reported, not blocking
		}
	}
	if err := s.Store.TransitionTenant(ctx, id, TenantSuspended, TenantActive,
		NewEvent(EvTenantReactivated, id, actor, t.URN(), s.now(), map[string]any{"drift": drift})); err != nil {
		return nil, "", err
	}
	t2, err := s.Store.GetTenant(ctx, id)
	return t2, drift, err
}

// Delete implements IDN-FR-008. mode=archive suspends and retains;
// mode=destroy schedules (7-day grace) or, with force by a super-admin,
// immediately runs the destroy workflow.
func (s *TenantService) Delete(ctx context.Context, id uuid.UUID, mode string, force bool, actor Actor) (*Tenant, error) {
	t, err := s.Store.GetTenant(ctx, id)
	if err != nil {
		return nil, err
	}
	now := s.now()
	switch mode {
	case "archive":
		if err := s.Store.TransitionTenant(ctx, id, t.Status, TenantSuspended,
			NewEvent(EvTenantSuspended, id, actor, t.URN(), now, map[string]any{"archive": true})); err != nil {
			return nil, err
		}
	case "destroy":
		wasProvisionFailed := t.Status == TenantProvisionFailed
		if err := s.Store.TransitionTenant(ctx, id, t.Status, TenantDeleting,
			NewEvent(EvTenantDeletionStarted, id, actor, t.URN(), now, map[string]any{"force": force})); err != nil {
			return nil, err
		}
		t, err = s.Store.GetTenant(ctx, id)
		if err != nil {
			return nil, err
		}
		if force || wasProvisionFailed {
			// Aborting a failed provisioning runs the compensation stack
			// (IDN-FR-003 provision_failed -> deleting).
			if wasProvisionFailed {
				if err := s.Engine.Abort(ctx, id); err != nil {
					return nil, err
				}
				if err := s.Store.TransitionTenant(ctx, id, TenantDeleting, TenantDeleted,
					NewEvent(EvTenantDeleted, id, actor, t.URN(), s.now(), nil)); err != nil {
					return nil, err
				}
			} else {
				s.runWorkflow(func() error { return s.Engine.Deprovision(context.WithoutCancel(ctx), id) })
			}
		} else {
			sched := now.Add(DeletionGracePeriod)
			t.DeletionScheduledAt = &sched
			if err := s.Store.UpdateTenant(ctx, t); err != nil {
				return nil, err
			}
		}
	default:
		return nil, EValidation("mode must be archive or destroy", FieldError{Field: "mode", Message: "archive|destroy"})
	}
	return s.Store.GetTenant(ctx, id)
}

// RunScheduledDeletions processes tenants whose grace period elapsed
// (invoked by the scheduler loop in main; directly in tests).
func (s *TenantService) RunScheduledDeletions(ctx context.Context) error {
	tenants, _, err := s.Store.ListTenants(ctx, TenantFilter{Status: string(TenantDeleting)}, PageRequest{Limit: MaxPageLimit})
	if err != nil {
		return err
	}
	now := s.now()
	for _, t := range tenants {
		if t.DeletionScheduledAt != nil && !now.Before(*t.DeletionScheduledAt) {
			if err := s.Engine.Deprovision(ctx, t.ID); err != nil {
				continue // BR-6: stays deleting, retried next sweep
			}
		}
	}
	return nil
}

// ProvisioningStatus returns step-by-step status (IDN-FR-007).
func (s *TenantService) ProvisioningStatus(ctx context.Context, id uuid.UUID) ([]*ProvisioningStep, error) {
	if _, err := s.Store.GetTenant(ctx, id); err != nil {
		return nil, err
	}
	return s.Store.ListProvisioningSteps(ctx, id, WorkflowIDFor(id))
}

// PatchTenantRequest per API spec (quotas, display_name, auto_upgrade).
type PatchTenantRequest struct {
	DisplayName *string `json:"display_name,omitempty"`
	Quotas      *Quotas `json:"quotas,omitempty"`
	AutoUpgrade *bool   `json:"auto_upgrade,omitempty"`
}

func (s *TenantService) Patch(ctx context.Context, id uuid.UUID, req PatchTenantRequest, actor Actor) (*Tenant, error) {
	t, err := s.Store.GetTenant(ctx, id)
	if err != nil {
		return nil, err
	}
	if req.DisplayName != nil {
		t.DisplayName = *req.DisplayName
	}
	if req.Quotas != nil {
		if req.Quotas.CPU <= 0 || req.Quotas.ProcessingCPU <= 0 {
			return nil, EValidation("invalid quotas", FieldError{Field: "quotas", Message: "cpu values must be positive"})
		}
		t.Quotas = *req.Quotas // NOTE: quota-change resize workflow is a stub (see README)
	}
	if req.AutoUpgrade != nil {
		t.AutoUpgrade = *req.AutoUpgrade
	}
	t.UpdatedAt = s.now()
	if err := s.Store.UpdateTenant(ctx, t,
		NewEvent("tenant.updated", t.ID, actor, t.URN(), t.UpdatedAt, nil)); err != nil {
		return nil, err
	}
	return t, nil
}

func firstNonEmpty(a, b string) string {
	if strings.TrimSpace(a) != "" {
		return a
	}
	return b
}
