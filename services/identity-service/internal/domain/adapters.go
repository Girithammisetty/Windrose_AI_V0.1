package domain

import (
	"context"

	"github.com/google/uuid"
)

// KeycloakAdmin fronts Keycloak realm/user administration (IDN-FR-021/022,
// provisioning step 2, IDN-FR-053). Implementations:
//   - adapters/keycloak.Fake       — unit/integration tests
//   - adapters/keycloak.HTTPAdmin  — real admin REST adapter (untested against live)
type KeycloakAdmin interface {
	CreateRealm(ctx context.Context, tenantName string) error
	DeleteRealm(ctx context.Context, tenantName string) error
	CreateUser(ctx context.Context, realm, email, fullName string) (idpSubject string, err error)
	DisableUser(ctx context.Context, realm, idpSubject string) error
	// RevokeSessions revokes refresh sessions on deactivation (IDN-FR-022).
	RevokeSessions(ctx context.Context, realm, idpSubject string) error
}

// TerraformInputs is the runner contract (BRD §8: V1 pipeline-variable list).
type TerraformInputs struct {
	Identifier   string `json:"identifier"`
	SchemaPrefix string `json:"schema"`
	K8sNamespace string `json:"namespace"`
	Subdomain    string `json:"subdomain"`
	Quotas       Quotas `json:"quotas"`
	Cloud        string `json:"cloud"`
	Version      string `json:"version"`
}

// TerraformRunner invokes the per-cloud infra module and awaits completion
// inside the call (IDN-FR-006 step 3). Fake for tests; real runner is a
// separate infra concern.
type TerraformRunner interface {
	Apply(ctx context.Context, in TerraformInputs) error
	Destroy(ctx context.Context, in TerraformInputs) error
}

// DatabaseProvisioner creates per-tenant service schemas + RLS policies +
// seed rows (IDN-FR-006 step 4). Faked in tests.
type DatabaseProvisioner interface {
	CreateSchemas(ctx context.Context, tenantID uuid.UUID, schemaPrefix string) error
	DropSchemas(ctx context.Context, tenantID uuid.UUID, schemaPrefix string) error
}

// HealthProber runs the synthetic tenant health probe (step 7 Verify, BR-5).
type HealthProber interface {
	Probe(ctx context.Context, tenant *Tenant) error
}
