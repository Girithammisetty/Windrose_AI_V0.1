package domain_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/adapters/keycloak"
	"github.com/windrose-ai/identity-service/internal/adapters/terraform"
	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/store/memory"
)

type engineFixture struct {
	store  *memory.Store
	kc     *keycloak.Fake
	tf     *terraform.Fake
	db     *terraform.FakeDBProvisioner
	prober *terraform.FakeProber
	engine *domain.Engine
	tenant *domain.Tenant
}

func newEngineFixture(t *testing.T) *engineFixture {
	t.Helper()
	f := &engineFixture{
		store: memory.New(), kc: keycloak.NewFake(), tf: terraform.NewFake(),
		db: terraform.NewFakeDB(), prober: &terraform.FakeProber{},
	}
	deps := domain.StepDeps{Store: f.store, Keycloak: f.kc, Terraform: f.tf, DB: f.db, Prober: f.prober}
	cfg := domain.DefaultEngineConfig()
	cfg.Backoff = func(int) time.Duration { return 0 } // no waiting in tests
	f.engine = domain.NewEngine(f.store, cfg, deps.ProvisionSteps, deps.DestroySteps, nil)

	ctx := context.Background()
	cellID, _ := uuid.NewV7()
	if err := f.store.CreateCell(ctx, &domain.Cell{ID: cellID, Name: "cell-aws-1", Cloud: "aws", Region: "us-east-1", Capacity: 10}); err != nil {
		t.Fatal(err)
	}
	id, _ := uuid.NewV7()
	now := time.Now().UTC()
	f.tenant = &domain.Tenant{
		ID: id, Name: "acme", DisplayName: "acme", OwnerEmail: "owner@acme.com",
		Tier: "pool", Cloud: "aws", Status: domain.TenantProvisioning,
		Quotas: domain.DefaultQuotas(), PlatformVersion: "latest",
		Subdomain: "acme", K8sNamespace: "acme", SchemaPrefix: "acme",
		Modules: []string{"data", "config", "ui"}, CreatedAt: now, UpdatedAt: now,
	}
	if err := f.store.CreateTenant(ctx, f.tenant); err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *engineFixture) steps(t *testing.T) []*domain.ProvisioningStep {
	t.Helper()
	steps, err := f.store.ListProvisioningSteps(context.Background(), f.tenant.ID, domain.WorkflowIDFor(f.tenant.ID))
	if err != nil {
		t.Fatal(err)
	}
	return steps
}

func TestEngineHappyPath(t *testing.T) {
	f := newEngineFixture(t)
	if err := f.engine.Provision(context.Background(), f.tenant.ID); err != nil {
		t.Fatalf("provision failed: %v", err)
	}
	got, _ := f.store.GetTenant(context.Background(), f.tenant.ID)
	if got.Status != domain.TenantActive {
		t.Fatalf("status = %s, want active", got.Status)
	}
	steps := f.steps(t)
	if len(steps) != 7 {
		t.Fatalf("expected 7 step records, got %d", len(steps))
	}
	for _, s := range steps {
		if s.Status != domain.StepSucceeded {
			t.Errorf("step %s: status %s", s.StepName, s.Status)
		}
	}
	if f.prober.Calls == 0 {
		t.Error("Verify probe never ran")
	}
	// Owner seeded (BR-7).
	if _, err := f.store.GetUserByEmail(context.Background(), f.tenant.ID, "owner@acme.com"); err != nil {
		t.Errorf("owner user not seeded: %v", err)
	}
	// Regression: the owner's user.invited event must carry is_owner=true —
	// rbac-service's consumer keys the owner's automatic Admin group
	// membership on this flag (a regular admin-driven invite, POST /users,
	// emits the SAME event type WITHOUT it, and must not auto-grant Admin).
	invited := f.store.EventsOfType(domain.EvUserInvited)
	if len(invited) != 1 {
		t.Fatalf("expected exactly 1 user.invited event, got %d", len(invited))
	}
	if isOwner, _ := invited[0].Payload["is_owner"].(bool); !isOwner {
		t.Errorf("owner user.invited event missing is_owner=true, payload=%v", invited[0].Payload)
	}
}

func TestEngineRetriesWithBackoffThenFails(t *testing.T) {
	f := newEngineFixture(t)
	f.tf.FailApplyAlways = errors.New("cloud down")
	err := f.engine.Provision(context.Background(), f.tenant.ID)
	var sf *domain.StepFailedError
	if !errors.As(err, &sf) || sf.StepName != "ProvisionInfra" {
		t.Fatalf("expected StepFailedError on ProvisionInfra, got %v", err)
	}
	if f.tf.ApplyCalls != 5 {
		t.Fatalf("expected 5 attempts (IDN-FR-006), got %d", f.tf.ApplyCalls)
	}
	got, _ := f.store.GetTenant(context.Background(), f.tenant.ID)
	if got.Status != domain.TenantProvisionFailed {
		t.Fatalf("status = %s, want provision_failed", got.Status)
	}
	steps := f.steps(t)
	if steps[2].Status != domain.StepFailed || steps[2].Attempt != 5 || steps[2].Error == "" {
		t.Errorf("step 3 record wrong: %+v", steps[2])
	}
}

func TestEngineCompensationStack(t *testing.T) {
	f := newEngineFixture(t)
	f.tf.FailApplyAlways = errors.New("cloud down")
	_ = f.engine.Provision(context.Background(), f.tenant.ID)

	// Every succeeded step has its compensation recorded (IDN-FR-007).
	steps := f.steps(t)
	for _, s := range steps[:2] {
		if s.Status != domain.StepSucceeded || s.CompensationName == "" {
			t.Fatalf("step %s: expected succeeded with recorded compensation, got %+v", s.StepName, s)
		}
	}
	if err := f.engine.Abort(context.Background(), f.tenant.ID); err != nil {
		t.Fatalf("abort failed: %v", err)
	}
	steps = f.steps(t)
	for _, s := range steps[:2] {
		if s.Status != domain.StepCompensated {
			t.Errorf("step %s: expected compensated, got %s", s.StepName, s.Status)
		}
	}
	if f.kc.Realms["acme"] {
		t.Error("keycloak realm not compensated")
	}
	cells, _ := f.store.ListCells(context.Background())
	if cells[0].TenantCount != 0 {
		t.Error("cell reservation not released")
	}
}

func TestEngineCellCapacityFailsFast(t *testing.T) {
	f := newEngineFixture(t)
	// Fill the only cell.
	cells, _ := f.store.ListCells(context.Background())
	for i := 0; i < 10; i++ {
		_ = f.store.ReserveCell(context.Background(), cells[0].ID)
	}
	err := f.engine.Provision(context.Background(), f.tenant.ID)
	var sf *domain.StepFailedError
	if !errors.As(err, &sf) || sf.StepName != "AssignCell" {
		t.Fatalf("expected AssignCell failure, got %v", err)
	}
	de, ok := domain.AsError(sf.Err)
	if !ok || de.Code != domain.CodeCellCapacity {
		t.Fatalf("expected CELL_CAPACITY (BR-3), got %v", sf.Err)
	}
}

func TestEngineDeprovisionStaysDeletingOnDestroyFailure(t *testing.T) {
	f := newEngineFixture(t)
	if err := f.engine.Provision(context.Background(), f.tenant.ID); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if err := f.store.TransitionTenant(ctx, f.tenant.ID, domain.TenantActive, domain.TenantDeleting); err != nil {
		t.Fatal(err)
	}
	f.tf.FailDestroyAlways = errors.New("destroy stuck")
	if err := f.engine.Deprovision(ctx, f.tenant.ID); err == nil {
		t.Fatal("expected deprovision error")
	}
	got, _ := f.store.GetTenant(ctx, f.tenant.ID)
	if got.Status != domain.TenantDeleting || got.DeletedAt != nil {
		t.Fatalf("BR-6 violated: status=%s deleted_at=%v", got.Status, got.DeletedAt)
	}
	// Fix the runner; retry completes.
	f.tf.FailDestroyAlways = nil
	if err := f.engine.Deprovision(ctx, f.tenant.ID); err != nil {
		t.Fatalf("retry deprovision failed: %v", err)
	}
	got, _ = f.store.GetTenant(ctx, f.tenant.ID)
	if got.Status != domain.TenantDeleted || got.DeletedAt == nil {
		t.Fatalf("expected deleted, got %s", got.Status)
	}
}
