package domain

import (
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
)

// TenantStatus is the tenant lifecycle state (IDN-FR-003).
type TenantStatus string

const (
	TenantDraft           TenantStatus = "draft"
	TenantProvisioning    TenantStatus = "provisioning"
	TenantProvisionFailed TenantStatus = "provision_failed"
	TenantActive          TenantStatus = "active"
	TenantSuspended       TenantStatus = "suspended"
	TenantDeleting        TenantStatus = "deleting"
	TenantDeleted         TenantStatus = "deleted"
)

// tenantTransitions is the guarded transition table from IDN-FR-003.
// Any transition not present here is rejected with 409 CONFLICT.
var tenantTransitions = map[TenantStatus][]TenantStatus{
	TenantDraft:           {TenantProvisioning},
	TenantProvisioning:    {TenantActive, TenantProvisionFailed},
	TenantProvisionFailed: {TenantProvisioning, TenantDeleting},
	TenantActive:          {TenantSuspended, TenantDeleting},
	TenantSuspended:       {TenantActive, TenantDeleting},
	TenantDeleting:        {TenantDeleted},
}

// CanTransition reports whether from -> to is an allowed tenant transition.
func CanTransition(from, to TenantStatus) bool {
	for _, t := range tenantTransitions[from] {
		if t == to {
			return true
		}
	}
	return false
}

// AllTenantStatuses is exported for the transition-matrix test.
var AllTenantStatuses = []TenantStatus{
	TenantDraft, TenantProvisioning, TenantProvisionFailed,
	TenantActive, TenantSuspended, TenantDeleting, TenantDeleted,
}

// Quotas per IDN-FR-004 (V1 defaults).
type Quotas struct {
	CPU              int    `json:"cpu"`
	Memory           string `json:"memory"`
	ProcessingCPU    int    `json:"processing_cpu"`
	ProcessingMemory string `json:"processing_memory"`
}

func DefaultQuotas() Quotas {
	return Quotas{CPU: 4, Memory: "16Gi", ProcessingCPU: 4, ProcessingMemory: "16Gi"}
}

// Tenant is the root registry entity (IDN-FR-001). Platform-scoped (RLS-exempt).
type Tenant struct {
	ID                  uuid.UUID    `json:"id"`
	Name                string       `json:"name"`
	DisplayName         string       `json:"display_name"`
	OwnerEmail          string       `json:"owner_email"`
	Tier                string       `json:"tier"` // pool|bridge|silo
	CellID              *uuid.UUID   `json:"cell_id,omitempty"`
	Cloud               string       `json:"cloud"` // aws|azure|gcp
	Status              TenantStatus `json:"status"`
	Quotas              Quotas       `json:"quotas"`
	PlatformVersion     string       `json:"platform_version"`
	Subdomain           string       `json:"subdomain"`
	K8sNamespace        string       `json:"k8s_namespace"`
	SchemaPrefix        string       `json:"schema_prefix"`
	AutoUpgrade         bool         `json:"auto_upgrade"`
	Modules             []string     `json:"modules"` // resolved module set (IDN-FR-005)
	CreatedBy           string       `json:"created_by"`
	CreatedAt           time.Time    `json:"created_at"`
	UpdatedAt           time.Time    `json:"updated_at"`
	DeletedAt           *time.Time   `json:"deleted_at,omitempty"`
	DeletionScheduledAt *time.Time   `json:"deletion_scheduled_at,omitempty"`
}

func (t *Tenant) URN() string { return "wr:" + t.ID.String() + ":identity:tenant/" + t.ID.String() }

// Transition applies a guarded state change (IDN-FR-003). Invalid -> 409.
func (t *Tenant) Transition(to TenantStatus, now time.Time) error {
	if !CanTransition(t.Status, to) {
		return EConflict("invalid tenant status transition " + string(t.Status) + " -> " + string(to))
	}
	t.Status = to
	t.UpdatedAt = now.UTC()
	return nil
}

// tenantNameRe per IDN-FR-002: ^[a-z][a-z0-9-]{2,38}$ (applied after lowercasing).
var tenantNameRe = regexp.MustCompile(`^[a-z][a-z0-9-]{2,38}$`)

// ReservedTenantNames rejects platform-colliding names (IDN-FR-002).
// Cell names are appended at runtime via config.
var ReservedTenantNames = map[string]bool{
	"admin": true, "api": true, "www": true, "internal": true,
	"platform": true, "identity": true, "keycloak": true,
}

// NormalizeTenantName lowercases and validates the tenant name and returns
// the derived unique identifiers (IDN-FR-002).
func NormalizeTenantName(raw string, extraReserved []string) (name, subdomain, k8sNamespace, schemaPrefix string, err error) {
	name = strings.ToLower(strings.TrimSpace(raw))
	if !tenantNameRe.MatchString(name) {
		return "", "", "", "", EValidation("tenant name must match ^[a-z][a-z0-9-]{2,38}$ after lowercasing",
			FieldError{Field: "name", Message: "must start with a letter; letters, digits, hyphens; length 3-39"})
	}
	if ReservedTenantNames[name] {
		return "", "", "", "", EValidation("tenant name is reserved", FieldError{Field: "name", Message: "reserved name"})
	}
	for _, r := range extraReserved {
		if name == strings.ToLower(r) {
			return "", "", "", "", EValidation("tenant name is reserved", FieldError{Field: "name", Message: "reserved name (cell)"})
		}
	}
	return name, name, name, strings.ReplaceAll(name, "-", "_"), nil
}

// ValidTiers / ValidClouds for creation validation.
var ValidTiers = map[string]bool{"pool": true, "bridge": true, "silo": true}
var ValidClouds = map[string]bool{"aws": true, "azure": true, "gcp": true}

// ModuleGraph is the platform-level module dependency graph (IDN-FR-005,
// V1 service_dependencies). Enabling a module auto-enables its dependencies.
type ModuleGraph struct {
	// Deps maps module -> direct dependencies.
	Deps map[string][]string
	// Mandatory modules are always enabled for every tenant.
	Mandatory []string
}

// DefaultModuleGraph mirrors V1's mandatory data/config/UI services.
func DefaultModuleGraph() ModuleGraph {
	return ModuleGraph{
		Deps: map[string][]string{
			"ui":        {"config"},
			"config":    {"data"},
			"train":     {"data"},
			"infer":     {"train"},
			"visualize": {"data", "ui"},
			"triage":    {"data"},
		},
		Mandatory: []string{"data", "config", "ui"},
	}
}

// Resolve returns the transitive closure of requested+mandatory modules,
// sorted, or an error for unknown modules.
func (g ModuleGraph) Resolve(requested []string) ([]string, error) {
	known := map[string]bool{}
	for m := range g.Deps {
		known[m] = true
	}
	for _, m := range g.Mandatory {
		known[m] = true
	}
	for _, ds := range g.Deps {
		for _, d := range ds {
			known[d] = true
		}
	}
	set := map[string]bool{}
	var visit func(m string) error
	visit = func(m string) error {
		if set[m] {
			return nil
		}
		if !known[m] {
			return EValidation("unknown module: "+m, FieldError{Field: "modules", Message: "unknown module " + m})
		}
		set[m] = true
		for _, d := range g.Deps[m] {
			if err := visit(d); err != nil {
				return err
			}
		}
		return nil
	}
	for _, m := range g.Mandatory {
		if err := visit(m); err != nil {
			return nil, err
		}
	}
	for _, m := range requested {
		if err := visit(strings.ToLower(strings.TrimSpace(m))); err != nil {
			return nil, err
		}
	}
	out := make([]string, 0, len(set))
	for m := range set {
		out = append(out, m)
	}
	sort.Strings(out)
	return out, nil
}

// Cell is a deployment cell (~500 tenants) tenants are pinned to.
type Cell struct {
	ID          uuid.UUID `json:"id"`
	Name        string    `json:"name"`
	Cloud       string    `json:"cloud"`
	Region      string    `json:"region"`
	Capacity    int       `json:"capacity"` // max tenants
	TenantCount int       `json:"tenant_count"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}
