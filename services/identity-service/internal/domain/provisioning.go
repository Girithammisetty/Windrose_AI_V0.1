package domain

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// ProvisioningEngine is the workflow port (IDN-FR-006..008). The default
// implementation is the deterministic in-process Engine below; the
// internal/temporalwf package holds the real Temporal adapter skeleton that
// implements the same interface (TODO: wire to a Temporal cluster).
type ProvisioningEngine interface {
	// Provision runs (or resumes, AC-3) the 7-step provisioning workflow.
	// It transitions the tenant to active on success or provision_failed
	// when a step exhausts its retries.
	Provision(ctx context.Context, tenantID uuid.UUID) error
	// Abort compensates all succeeded steps in reverse order and moves the
	// tenant provision_failed -> deleting -> deleted.
	Abort(ctx context.Context, tenantID uuid.UUID) error
	// Deprovision runs the destroy workflow (IDN-FR-008 mode=destroy).
	// The tenant only reaches deleted after Terraform destroy succeeds (BR-6, AC-9).
	Deprovision(ctx context.Context, tenantID uuid.UUID) error
}

// WorkflowIDFor gives the singleton provisioning workflow id (BR-2):
// duplicate starts for the same tenant collide on this id.
func WorkflowIDFor(tenantID uuid.UUID) string { return "provision-" + tenantID.String() }

// DestroyWorkflowIDFor is the destroy workflow id.
func DestroyWorkflowIDFor(tenantID uuid.UUID) string { return "destroy-" + tenantID.String() }

// Step is one workflow activity with an optional registered compensation
// (IDN-FR-006: "each idempotent ... and a registered compensation").
type Step struct {
	Name             string
	CompensationName string // "" == none (steps 6, 7)
	Run              func(ctx context.Context, t *Tenant) error
	Compensate       func(ctx context.Context, t *Tenant) error
}

// StepFailedError marks a step that exhausted its retries.
type StepFailedError struct {
	StepIndex int
	StepName  string
	Err       error
}

func (e *StepFailedError) Error() string {
	return fmt.Sprintf("step %d (%s) failed after retries: %v", e.StepIndex, e.StepName, e.Err)
}
func (e *StepFailedError) Unwrap() error { return e.Err }

// EngineConfig tunes retry behaviour (IDN-FR-006: max 5 attempts,
// exponential backoff; tests inject zero backoff).
type EngineConfig struct {
	MaxAttempts int
	Backoff     func(attempt int) time.Duration
	Clock       func() time.Time
	Sleep       func(ctx context.Context, d time.Duration) error
}

func DefaultEngineConfig() EngineConfig {
	return EngineConfig{
		MaxAttempts: 5,
		Backoff: func(attempt int) time.Duration {
			return time.Duration(1<<uint(attempt)) * time.Second // 2s,4s,8s,16s
		},
		Clock: time.Now,
		Sleep: func(ctx context.Context, d time.Duration) error {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(d):
				return nil
			}
		},
	}
}

// Engine is the deterministic in-process workflow engine. State is persisted
// per step in provisioning_runs, so a crashed or failed run resumes from the
// first non-succeeded step (idempotency markers, AC-3).
type Engine struct {
	store  Store
	cfg    EngineConfig
	notify func(ctx context.Context, t *Tenant, s *ProvisioningStep) // step-completed events (IDN-FR-010)

	provisionSteps func(t *Tenant) []Step
	destroySteps   func(t *Tenant) []Step
}

// NewEngine wires the engine with step factories (built in engine_steps.go
// via StepDeps) and an optional step-completion notifier.
func NewEngine(store Store, cfg EngineConfig, provision, destroy func(t *Tenant) []Step,
	notify func(ctx context.Context, t *Tenant, s *ProvisioningStep)) *Engine {
	if cfg.MaxAttempts == 0 {
		cfg = DefaultEngineConfig()
	}
	if notify == nil {
		notify = func(context.Context, *Tenant, *ProvisioningStep) {}
	}
	return &Engine{store: store, cfg: cfg, provisionSteps: provision, destroySteps: destroy, notify: notify}
}

func (e *Engine) Provision(ctx context.Context, tenantID uuid.UUID) error {
	t, err := e.store.GetTenant(ctx, tenantID)
	if err != nil {
		return err
	}
	if t.Status != TenantProvisioning {
		return EConflict("tenant is not in provisioning state")
	}
	wfID := WorkflowIDFor(t.ID)
	runErr := e.run(ctx, t, wfID, e.provisionSteps(t))
	now := e.cfg.Clock().UTC()
	actor := Actor{Type: "service", ID: "identity-service"}
	if runErr != nil {
		var sf *StepFailedError
		if errors.As(runErr, &sf) {
			_ = e.store.TransitionTenant(ctx, t.ID, TenantProvisioning, TenantProvisionFailed,
				NewEvent(EvTenantProvisionFailed, t.ID, actor, t.URN(), now, map[string]any{
					"step": sf.StepName, "error": sf.Err.Error(),
				}))
		}
		return runErr
	}
	return e.store.TransitionTenant(ctx, t.ID, TenantProvisioning, TenantActive,
		NewEvent(EvTenantProvisioned, t.ID, actor, t.URN(), now, nil))
}

// run executes steps, skipping any already succeeded for this workflow id.
func (e *Engine) run(ctx context.Context, t *Tenant, wfID string, steps []Step) error {
	existing, err := e.store.ListProvisioningSteps(ctx, t.ID, wfID)
	if err != nil {
		return err
	}
	done := map[int]*ProvisioningStep{}
	for _, s := range existing {
		done[s.StepIndex] = s
	}
	for i, step := range steps {
		if prev, ok := done[i]; ok && prev.Status == StepSucceeded {
			continue // resume: never re-execute succeeded steps (AC-3)
		}
		rec := done[i]
		if rec == nil {
			id, _ := uuid.NewV7()
			rec = &ProvisioningStep{ID: id, TenantID: t.ID, WorkflowID: wfID, StepIndex: i, StepName: step.Name}
		}
		rec.CompensationName = step.CompensationName
		if err := e.runStep(ctx, t, step, rec); err != nil {
			return err
		}
		e.notify(ctx, t, rec)
	}
	return nil
}

func (e *Engine) runStep(ctx context.Context, t *Tenant, step Step, rec *ProvisioningStep) error {
	var lastErr error
	for attempt := 1; attempt <= e.cfg.MaxAttempts; attempt++ {
		now := e.cfg.Clock().UTC()
		rec.Status = StepRunning
		rec.Attempt = attempt
		rec.StartedAt = ptrTime(now)
		rec.FinishedAt = nil
		if err := e.store.SaveProvisioningStep(ctx, rec); err != nil {
			return err
		}
		lastErr = step.Run(ctx, t)
		end := e.cfg.Clock().UTC()
		rec.FinishedAt = ptrTime(end)
		if lastErr == nil {
			rec.Status = StepSucceeded
			rec.Error = ""
			return e.store.SaveProvisioningStep(ctx, rec)
		}
		rec.Status = StepFailed
		rec.Error = lastErr.Error()
		if err := e.store.SaveProvisioningStep(ctx, rec); err != nil {
			return err
		}
		if attempt < e.cfg.MaxAttempts {
			if err := e.cfg.Sleep(ctx, e.cfg.Backoff(attempt)); err != nil {
				return err
			}
		}
	}
	return &StepFailedError{StepIndex: rec.StepIndex, StepName: rec.StepName, Err: lastErr}
}

// Abort compensates succeeded steps in reverse order (compensation stack,
// AC-2: no partial infra without a recorded compensation path).
func (e *Engine) Abort(ctx context.Context, tenantID uuid.UUID) error {
	t, err := e.store.GetTenant(ctx, tenantID)
	if err != nil {
		return err
	}
	wfID := WorkflowIDFor(t.ID)
	steps := e.provisionSteps(t)
	recs, err := e.store.ListProvisioningSteps(ctx, t.ID, wfID)
	if err != nil {
		return err
	}
	byIndex := map[int]*ProvisioningStep{}
	for _, r := range recs {
		byIndex[r.StepIndex] = r
	}
	for i := len(steps) - 1; i >= 0; i-- {
		rec, ok := byIndex[i]
		if !ok || rec.Status != StepSucceeded || steps[i].Compensate == nil {
			continue
		}
		if err := steps[i].Compensate(ctx, t); err != nil {
			return fmt.Errorf("compensation %s failed: %w", steps[i].CompensationName, err)
		}
		rec.Status = StepCompensated
		rec.FinishedAt = ptrTime(e.cfg.Clock().UTC())
		if err := e.store.SaveProvisioningStep(ctx, rec); err != nil {
			return err
		}
	}
	return nil
}

// Deprovision runs the destroy workflow. If any step (notably Terraform
// destroy) fails after retries the tenant STAYS `deleting` (BR-6, AC-9).
func (e *Engine) Deprovision(ctx context.Context, tenantID uuid.UUID) error {
	t, err := e.store.GetTenant(ctx, tenantID)
	if err != nil {
		return err
	}
	if t.Status != TenantDeleting {
		return EConflict("tenant is not in deleting state")
	}
	wfID := DestroyWorkflowIDFor(t.ID)
	if err := e.run(ctx, t, wfID, e.destroySteps(t)); err != nil {
		return err // stays `deleting`; retryable
	}
	now := e.cfg.Clock().UTC()
	t2, err := e.store.GetTenant(ctx, tenantID)
	if err != nil {
		return err
	}
	t2.DeletedAt = ptrTime(now)
	if err := e.store.UpdateTenant(ctx, t2); err != nil {
		return err
	}
	return e.store.TransitionTenant(ctx, t.ID, TenantDeleting, TenantDeleted,
		NewEvent(EvTenantDeleted, t.ID, Actor{Type: "service", ID: "identity-service"}, t.URN(), now, map[string]any{
			"cascade": []string{"keycloak_realm", "service_accounts", "agent_principals", "api_keys"},
		}))
}

func ptrTime(t time.Time) *time.Time { return &t }
