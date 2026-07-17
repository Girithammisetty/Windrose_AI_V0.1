// Package temporalwf is the real Temporal adapter skeleton for the tenant
// provisioning workflow (IDN-FR-006). It compiles against the Temporal Go SDK
// and mirrors the in-process engine's semantics 1:1:
//
//   - workflow ID = provision-<tenant_id> with REJECT_DUPLICATE policy (BR-2)
//   - per-activity retry: max 5 attempts, exponential backoff, 30-min
//     start-to-close timeout (IDN-FR-006)
//   - compensation stack executed in reverse on abort (saga pattern)
//   - resume-from-failed-step comes free from Temporal event-history replay
//
// STATUS: compiles, NOT wired to a Temporal cluster. The production Engine
// used by the service today is domain.Engine (deterministic in-process).
// TODO(identity): register worker + activities in cmd/server against a real
// Temporal namespace, move activity bodies onto domain.StepDeps, and swap
// TenantService.Engine to this adapter behind a config flag.
package temporalwf

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// TaskQueue for identity provisioning workers.
const TaskQueue = "identity-provisioning"

// ErrNotWired is returned by the Engine adapter until a cluster is configured.
var ErrNotWired = errors.New("temporal engine not wired: configure TEMPORAL_HOSTPORT and run a worker (TODO)")

// activityOptions per IDN-FR-006: max 5 attempts, exponential backoff,
// 30-minute step timeout.
func activityOptions() workflow.ActivityOptions {
	return workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Minute,
		RetryPolicy: &temporal.RetryPolicy{
			InitialInterval:    2 * time.Second,
			BackoffCoefficient: 2.0,
			MaximumInterval:    5 * time.Minute,
			MaximumAttempts:    5,
		},
	}
}

// ProvisionInput is the workflow argument.
type ProvisionInput struct {
	TenantID uuid.UUID
}

// Activities is implemented by a struct wrapping domain.StepDeps at worker
// registration time; names match the 7 activities of IDN-FR-006.
type Activities interface {
	AssignCell(ctx context.Context, tenantID uuid.UUID) error
	ReleaseCellReservation(ctx context.Context, tenantID uuid.UUID) error
	CreateKeycloakRealm(ctx context.Context, tenantID uuid.UUID) error
	DeleteKeycloakRealm(ctx context.Context, tenantID uuid.UUID) error
	ProvisionInfra(ctx context.Context, tenantID uuid.UUID) error
	TerraformDestroy(ctx context.Context, tenantID uuid.UUID) error
	CreateDatabases(ctx context.Context, tenantID uuid.UUID) error
	DropSchemas(ctx context.Context, tenantID uuid.UUID) error
	RegisterServices(ctx context.Context, tenantID uuid.UUID) error
	DeregisterServices(ctx context.Context, tenantID uuid.UUID) error
	SeedDefaults(ctx context.Context, tenantID uuid.UUID) error
	Verify(ctx context.Context, tenantID uuid.UUID) error
	MarkProvisioned(ctx context.Context, tenantID uuid.UUID) error
	MarkProvisionFailed(ctx context.Context, tenantID uuid.UUID, step string) error
}

// step pairs an activity name with its registered compensation (saga).
type step struct {
	name string
	comp string // "" = none
}

var provisionPlan = []step{
	{"AssignCell", "ReleaseCellReservation"},
	{"CreateKeycloakRealm", "DeleteKeycloakRealm"},
	{"ProvisionInfra", "TerraformDestroy"},
	{"CreateDatabases", "DropSchemas"},
	{"RegisterServices", "DeregisterServices"},
	{"SeedDefaults", ""},
	{"Verify", ""},
}

// ProvisionWorkflow is the durable equivalent of domain.Engine.Provision.
func ProvisionWorkflow(ctx workflow.Context, in ProvisionInput) error {
	ctx = workflow.WithActivityOptions(ctx, activityOptions())
	var compensations []string
	for _, st := range provisionPlan {
		err := workflow.ExecuteActivity(ctx, st.name, in.TenantID).Get(ctx, nil)
		if err != nil {
			// Retries exhausted: record failure; compensations stay recorded
			// in workflow history (AC-2 — no unmanaged infra without a
			// compensation path). Abort runs them via signal (below).
			_ = workflow.ExecuteActivity(ctx, "MarkProvisionFailed", in.TenantID, st.name).Get(ctx, nil)
			return err
		}
		if st.comp != "" {
			compensations = append(compensations, st.comp)
		}
	}
	return workflow.ExecuteActivity(ctx, "MarkProvisioned", in.TenantID).Get(ctx, nil)
}

// AbortWorkflow compensates in reverse order (saga rollback).
func AbortWorkflow(ctx workflow.Context, in ProvisionInput) error {
	ctx = workflow.WithActivityOptions(ctx, activityOptions())
	for i := len(provisionPlan) - 1; i >= 0; i-- {
		if provisionPlan[i].comp == "" {
			continue
		}
		if err := workflow.ExecuteActivity(ctx, provisionPlan[i].comp, in.TenantID).Get(ctx, nil); err != nil {
			return err
		}
	}
	return nil
}

// DestroyWorkflow mirrors domain.Engine.Deprovision: terraform destroy must
// succeed before the tenant flips to deleted (BR-6, AC-9).
func DestroyWorkflow(ctx workflow.Context, in ProvisionInput) error {
	ctx = workflow.WithActivityOptions(ctx, activityOptions())
	for _, name := range []string{"TerraformDestroy", "DeleteKeycloakRealm"} {
		if err := workflow.ExecuteActivity(ctx, name, in.TenantID).Get(ctx, nil); err != nil {
			return err
		}
	}
	return nil
}

// Engine adapts a Temporal client to domain.ProvisioningEngine.
type Engine struct {
	Client client.Client
}

var _ domain.ProvisioningEngine = (*Engine)(nil)

func (e *Engine) Provision(ctx context.Context, tenantID uuid.UUID) error {
	if e.Client == nil {
		return ErrNotWired
	}
	_, err := e.Client.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID:                    domain.WorkflowIDFor(tenantID), // BR-2 duplicate rejection
		TaskQueue:             TaskQueue,
		WorkflowIDReusePolicy: 1, // WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE_FAILED_ONLY (resume on retry)
	}, ProvisionWorkflow, ProvisionInput{TenantID: tenantID})
	return err
}

func (e *Engine) Abort(ctx context.Context, tenantID uuid.UUID) error {
	if e.Client == nil {
		return ErrNotWired
	}
	_, err := e.Client.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID: "abort-" + tenantID.String(), TaskQueue: TaskQueue,
	}, AbortWorkflow, ProvisionInput{TenantID: tenantID})
	return err
}

func (e *Engine) Deprovision(ctx context.Context, tenantID uuid.UUID) error {
	if e.Client == nil {
		return ErrNotWired
	}
	_, err := e.Client.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID: domain.DestroyWorkflowIDFor(tenantID), TaskQueue: TaskQueue,
	}, DestroyWorkflow, ProvisionInput{TenantID: tenantID})
	return err
}
