package domain_test

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/adapters/keycloak"
	"github.com/windrose-ai/identity-service/internal/adapters/localinfra"
	"github.com/windrose-ai/identity-service/internal/domain"
	"github.com/windrose-ai/identity-service/internal/store/memory"
)

// TestEngineReachesActiveWithLocalInfra proves the PRODUCTION-wired local
// adapters (adapters/localinfra Runner/DB/Prober — the honest no-op equivalents
// used in cmd/server/main.go, NOT the scriptable test Fakes) drive the 7-step
// saga all the way to `active`. This is the unit-level guard on gap #1: the
// happy path with a real (here: Fake) Keycloak plus the local infra adapters
// must not stall in provision_failed.
func TestEngineReachesActiveWithLocalInfra(t *testing.T) {
	ctx := context.Background()
	store := memory.New()
	deps := domain.StepDeps{
		Store:     store,
		Keycloak:  keycloak.NewFake(),
		Terraform: localinfra.Runner{},
		DB:        localinfra.DB{},
		Prober:    localinfra.Prober{},
	}
	cfg := domain.DefaultEngineConfig()
	cfg.Backoff = func(int) time.Duration { return 0 }
	engine := domain.NewEngine(store, cfg, deps.ProvisionSteps, deps.DestroySteps, nil)

	cellID, _ := uuid.NewV7()
	if err := store.CreateCell(ctx, &domain.Cell{
		ID: cellID, Name: "cell-aws-1", Cloud: "aws", Region: "us-east-1", Capacity: 10,
	}); err != nil {
		t.Fatal(err)
	}
	id, _ := uuid.NewV7()
	now := time.Now().UTC()
	tenant := &domain.Tenant{
		ID: id, Name: "acme-local", DisplayName: "acme", OwnerEmail: "owner@acme.com",
		Tier: "pool", Cloud: "aws", Status: domain.TenantProvisioning,
		Quotas: domain.DefaultQuotas(), PlatformVersion: "latest",
		Subdomain: "acme", K8sNamespace: "acme", SchemaPrefix: "acme",
		Modules: []string{"data", "config", "ui"}, CreatedAt: now, UpdatedAt: now,
	}
	if err := store.CreateTenant(ctx, tenant); err != nil {
		t.Fatal(err)
	}

	if err := engine.Provision(ctx, id); err != nil {
		t.Fatalf("provision with localinfra adapters failed: %v", err)
	}
	got, _ := store.GetTenant(ctx, id)
	if got.Status != domain.TenantActive {
		t.Fatalf("status = %s, want active", got.Status)
	}
	// Owner invite seeded (BR-7) — proves the saga ran through SeedDefaults.
	if _, err := store.GetUserByEmail(ctx, id, "owner@acme.com"); err != nil {
		t.Fatalf("owner not seeded: %v", err)
	}
}
