// Package localinfra provides HONEST local-equivalent implementations of the
// three infra ports the provisioning saga drives that have no real local
// target on a single Mac: the per-cloud Terraform runner (IDN-FR-006 step 3),
// the per-tenant database provisioner (step 4), and the synthetic health prober
// (step 7 Verify, BR-5).
//
// These are NOT the test Fakes (adapters/terraform.Fake, which can be scripted
// to FAIL). They are real, deterministic no-op-equivalents that always SUCCEED
// and advance the saga to `active`, and they announce themselves loudly at boot
// so a deploy never silently pretends a cloud Terraform apply ran. In a cloud
// deploy these ports bind to the real per-cloud infra module runner + the
// per-service schema provisioner; locally there is genuinely no cloud to call,
// so the honest behaviour is "complete successfully and say so".
package localinfra

import (
	"context"
	"log/slog"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// Runner implements domain.TerraformRunner. Apply/Destroy log the tenant
// identifier and succeed — there is no cloud Terraform target locally.
type Runner struct{ Log *slog.Logger }

func (r Runner) Apply(_ context.Context, in domain.TerraformInputs) error {
	if r.Log != nil {
		r.Log.Info("localinfra: Terraform apply (local no-op equivalent — no cloud target)",
			"identifier", in.Identifier, "namespace", in.K8sNamespace, "cloud", in.Cloud)
	}
	return nil
}

func (r Runner) Destroy(_ context.Context, in domain.TerraformInputs) error {
	if r.Log != nil {
		r.Log.Info("localinfra: Terraform destroy (local no-op equivalent)",
			"identifier", in.Identifier)
	}
	return nil
}

// DB implements domain.DatabaseProvisioner. Per-tenant service schemas + RLS
// are provisioned per cloud by the real runner; locally this records the
// intent and succeeds.
type DB struct{ Log *slog.Logger }

func (d DB) CreateSchemas(_ context.Context, tenantID uuid.UUID, prefix string) error {
	if d.Log != nil {
		d.Log.Info("localinfra: create tenant schemas (local no-op equivalent)",
			"tenant", tenantID, "schema_prefix", prefix)
	}
	return nil
}

func (d DB) DropSchemas(_ context.Context, tenantID uuid.UUID, prefix string) error {
	if d.Log != nil {
		d.Log.Info("localinfra: drop tenant schemas (local no-op equivalent)",
			"tenant", tenantID, "schema_prefix", prefix)
	}
	return nil
}

// Prober implements domain.HealthProber (step 7 Verify, BR-5). Locally the
// synthetic per-tenant health surface does not exist, so the probe succeeds;
// the earlier saga steps (real Keycloak realm creation, module registration)
// are the substantive gate on the local happy path.
type Prober struct{ Log *slog.Logger }

func (p Prober) Probe(_ context.Context, t *domain.Tenant) error {
	if p.Log != nil {
		p.Log.Info("localinfra: tenant health probe (local no-op equivalent — always healthy)",
			"tenant", t.ID, "name", t.Name)
	}
	return nil
}
