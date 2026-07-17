//go:build integration

package integration

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/adapters/keycloak"
	"github.com/windrose-ai/identity-service/internal/adapters/terraform"
	"github.com/windrose-ai/identity-service/internal/domain"
	pgstore "github.com/windrose-ai/identity-service/internal/store/postgres"
)

var nameSeq int

func freshName(prefix string) string {
	nameSeq++
	return fmt.Sprintf("%s-%d-%d", prefix, time.Now().UnixNano()%1_000_000, nameSeq)
}

func newTenantRow(t *testing.T, store domain.Store, status domain.TenantStatus) *domain.Tenant {
	t.Helper()
	name := freshName("t")
	now := time.Now().UTC()
	id, _ := uuid.NewV7()
	tn := &domain.Tenant{
		ID: id, Name: name, DisplayName: name, OwnerEmail: "owner@" + name + ".com",
		Tier: "pool", Cloud: "aws", Status: status, Quotas: domain.DefaultQuotas(),
		PlatformVersion: "latest", Subdomain: name, K8sNamespace: name,
		SchemaPrefix: name, Modules: []string{"data"}, CreatedAt: now, UpdatedAt: now,
	}
	if err := store.CreateTenant(context.Background(), tn); err != nil {
		t.Fatal(err)
	}
	return tn
}

func newUserRow(t *testing.T, store domain.Store, tenantID uuid.UUID, email string) *domain.User {
	t.Helper()
	now := time.Now().UTC()
	id, _ := uuid.NewV7()
	u := &domain.User{
		ID: id, TenantID: tenantID, Email: email, FullName: "U",
		Status: domain.UserActive, CreatedAt: now, UpdatedAt: now,
	}
	if err := store.CreateUser(context.Background(), u); err != nil {
		t.Fatal(err)
	}
	return u
}

// TestRLSIsolation is the tenant-isolation suite at the persistence boundary
// (MASTER-FR-001/004, AC-12 data layer): with app.tenant_id set to tenant A,
// tenant B's rows are invisible for reads, writes, and raw SQL.
func TestRLSIsolation(t *testing.T) {
	requirePG(t)
	ctx := context.Background()
	store := pgstore.New(appPool)
	a := newTenantRow(t, store, domain.TenantActive)
	b := newTenantRow(t, store, domain.TenantActive)
	aUser := newUserRow(t, store, a.ID, "a@a.com")
	bUser := newUserRow(t, store, b.ID, "b@b.com")

	// Store-level cross-tenant read -> 404 semantics.
	if _, err := store.GetUser(ctx, a.ID, bUser.ID); err == nil {
		t.Fatal("tenant A read tenant B's user through RLS")
	} else if de, _ := domain.AsError(err); de == nil || de.HTTP != 404 {
		t.Fatalf("want NOT_FOUND, got %v", err)
	}
	if _, err := store.GetUser(ctx, a.ID, aUser.ID); err != nil {
		t.Fatalf("own-tenant read failed: %v", err)
	}

	// Cross-tenant update attempt does not touch the row.
	evil := *bUser
	evil.TenantID = a.ID
	evil.FullName = "hax"
	if err := store.UpdateUser(ctx, &evil); err == nil {
		t.Fatal("cross-tenant update succeeded")
	}
	got, _ := store.GetUser(ctx, b.ID, bUser.ID)
	if got.FullName == "hax" {
		t.Fatal("tenant B row mutated cross-tenant")
	}

	// Raw SQL proof: the session sees only its tenant's rows.
	tx, err := appPool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SELECT set_config('app.tenant_id', $1, true)", a.ID.String()); err != nil {
		t.Fatal(err)
	}
	var n int
	if err := tx.QueryRow(ctx, "SELECT count(*) FROM users WHERE id = $1", bUser.ID).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatal("raw SQL under tenant A context saw tenant B's user")
	}
	if err := tx.QueryRow(ctx, "SELECT count(*) FROM users WHERE id = $1", aUser.ID).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatal("raw SQL under tenant A context did not see its own user")
	}
	_ = tx.Rollback(ctx)

	// No tenant context at all -> zero rows visible.
	var total int
	if err := appPool.QueryRow(ctx, "SELECT count(*) FROM users").Scan(&total); err != nil {
		t.Fatal(err)
	}
	if total != 0 {
		t.Fatalf("without app.tenant_id, %d user rows visible (want 0)", total)
	}

	// Same policy shape on service_accounts and agent_principals.
	now := time.Now().UTC()
	saID, _ := uuid.NewV7()
	if err := store.CreateServiceAccount(ctx, &domain.ServiceAccount{
		ID: saID, TenantID: b.ID, Name: "b-key", SecretHash: "$argon2id$v=19$m=1,t=1,p=1$AA$AA",
		Scopes: []string{"x.y.z"}, CreatedAt: now, UpdatedAt: now,
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.GetServiceAccount(ctx, a.ID, saID); err == nil {
		t.Fatal("cross-tenant service account visible")
	}
}

// TestStateMachinePersistence exercises the guarded transition matrix at the
// database boundary: the CAS UPDATE enforces IDN-FR-003 even under races.
func TestStateMachinePersistence(t *testing.T) {
	requirePG(t)
	ctx := context.Background()
	store := pgstore.New(appPool)

	for _, from := range domain.AllTenantStatuses {
		for _, to := range domain.AllTenantStatuses {
			tn := newTenantRow(t, store, from)
			err := store.TransitionTenant(ctx, tn.ID, from, to)
			if domain.CanTransition(from, to) {
				if err != nil {
					t.Errorf("%s -> %s: want allowed, got %v", from, to, err)
				}
				got, _ := store.GetTenant(ctx, tn.ID)
				if got.Status != to {
					t.Errorf("%s -> %s: persisted status %s", from, to, got.Status)
				}
			} else {
				de, ok := domain.AsError(err)
				if !ok || de.HTTP != 409 {
					t.Errorf("%s -> %s: want 409 CONFLICT, got %v", from, to, err)
				}
				got, _ := store.GetTenant(ctx, tn.ID)
				if got.Status != from {
					t.Errorf("%s -> %s: status mutated on rejection", from, to)
				}
			}
		}
	}

	// CAS under stale expectations: two racing publishers, one wins (BR-2).
	tn := newTenantRow(t, store, domain.TenantDraft)
	if err := store.TransitionTenant(ctx, tn.ID, domain.TenantDraft, domain.TenantProvisioning); err != nil {
		t.Fatal(err)
	}
	err := store.TransitionTenant(ctx, tn.ID, domain.TenantDraft, domain.TenantProvisioning)
	if de, _ := domain.AsError(err); de == nil || de.HTTP != 409 {
		t.Fatalf("second publish: want 409, got %v", err)
	}
}

// TestOutboxTransactional proves MASTER-FR-034 / BR-12: mutation and outbox
// event commit or roll back together.
func TestOutboxTransactional(t *testing.T) {
	requirePG(t)
	ctx := context.Background()
	store := pgstore.New(appPool)
	tn := newTenantRow(t, store, domain.TenantActive)
	now := time.Now().UTC()

	countOutbox := func(eventType string) int {
		var n int
		if err := adminPool.QueryRow(ctx, "SELECT count(*) FROM outbox WHERE event_type=$1 AND tenant_id=$2", eventType, tn.ID).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}

	id, _ := uuid.NewV7()
	u := &domain.User{ID: id, TenantID: tn.ID, Email: "w@x.com", Status: domain.UserInvited, CreatedAt: now, UpdatedAt: now}
	if err := store.CreateUser(ctx, u,
		domain.NewEvent(domain.EvUserInvited, tn.ID, domain.Actor{Type: "user", ID: "admin"}, u.URN(), now, nil)); err != nil {
		t.Fatal(err)
	}
	if countOutbox(domain.EvUserInvited) != 1 {
		t.Fatal("outbox event missing after committed mutation")
	}

	// Failed mutation (duplicate email) -> no orphan outbox row.
	id2, _ := uuid.NewV7()
	dup := &domain.User{ID: id2, TenantID: tn.ID, Email: "W@X.COM", Status: domain.UserInvited, CreatedAt: now, UpdatedAt: now}
	if err := store.CreateUser(ctx, dup,
		domain.NewEvent(domain.EvUserInvited, tn.ID, domain.Actor{Type: "user", ID: "admin"}, dup.URN(), now, nil)); err == nil {
		t.Fatal("duplicate email accepted")
	}
	if countOutbox(domain.EvUserInvited) != 1 {
		t.Fatal("outbox row leaked from rolled-back transaction")
	}

	// Poller path: ListOutbox sees the row, MarkOutboxPublished clears it.
	evs, err := store.ListOutbox(ctx, 100)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, ev := range evs {
		if ev.TenantID == tn.ID && ev.EventType == domain.EvUserInvited {
			found = true
			if err := store.MarkOutboxPublished(ctx, []uuid.UUID{ev.EventID}, time.Now().UTC()); err != nil {
				t.Fatal(err)
			}
		}
	}
	if !found {
		t.Fatal("platform poller did not see the outbox row")
	}
	evs, _ = store.ListOutbox(ctx, 100)
	for _, ev := range evs {
		if ev.TenantID == tn.ID && ev.EventType == domain.EvUserInvited {
			t.Fatal("published event still listed as unpublished")
		}
	}
}

// TestProvisioningPersistenceAndResume: step records survive an engine
// "restart"; the resumed run skips succeeded steps (AC-3 on real storage).
func TestProvisioningPersistenceAndResume(t *testing.T) {
	requirePG(t)
	ctx := context.Background()
	store := pgstore.New(appPool)

	cellID, _ := uuid.NewV7()
	if err := store.CreateCell(ctx, &domain.Cell{ID: cellID, Name: freshName("cell"), Cloud: "aws", Region: "us-east-1", Capacity: 100}); err != nil {
		t.Fatal(err)
	}
	tn := newTenantRow(t, store, domain.TenantProvisioning)

	kc := keycloak.NewFake()
	tf := terraform.NewFake()
	tf.FailApplyAlways = errors.New("cloud down")
	deps := domain.StepDeps{Store: store, Keycloak: kc, Terraform: tf, DB: terraform.NewFakeDB(), Prober: &terraform.FakeProber{}}
	cfg := domain.DefaultEngineConfig()
	cfg.Backoff = func(int) time.Duration { return 0 }
	engine := domain.NewEngine(store, cfg, deps.ProvisionSteps, deps.DestroySteps, nil)

	if err := engine.Provision(ctx, tn.ID); err == nil {
		t.Fatal("expected step failure")
	}
	got, _ := store.GetTenant(ctx, tn.ID)
	if got.Status != domain.TenantProvisionFailed {
		t.Fatalf("status %s, want provision_failed", got.Status)
	}
	steps, _ := store.ListProvisioningSteps(ctx, tn.ID, domain.WorkflowIDFor(tn.ID))
	if len(steps) != 3 || steps[2].Status != domain.StepFailed || steps[2].Attempt != 5 {
		t.Fatalf("persisted steps wrong: %+v", steps)
	}

	// "Restart": a brand-new engine over the same store resumes from step 3.
	tf.FailApplyAlways = nil
	engine2 := domain.NewEngine(store, cfg, deps.ProvisionSteps, deps.DestroySteps, nil)
	if err := store.TransitionTenant(ctx, tn.ID, domain.TenantProvisionFailed, domain.TenantProvisioning); err != nil {
		t.Fatal(err)
	}
	realmCalls := kc.Calls["CreateRealm"]
	if err := engine2.Provision(ctx, tn.ID); err != nil {
		t.Fatalf("resume failed: %v", err)
	}
	if kc.Calls["CreateRealm"] != realmCalls {
		t.Error("succeeded step re-executed after restart")
	}
	got, _ = store.GetTenant(ctx, tn.ID)
	if got.Status != domain.TenantActive {
		t.Fatalf("after resume: %s", got.Status)
	}
	steps, _ = store.ListProvisioningSteps(ctx, tn.ID, domain.WorkflowIDFor(tn.ID))
	if len(steps) != 7 {
		t.Fatalf("expected 7 persisted steps, got %d", len(steps))
	}
	for _, s := range steps {
		if s.Status != domain.StepSucceeded {
			t.Errorf("step %s: %s", s.StepName, s.Status)
		}
	}
}
