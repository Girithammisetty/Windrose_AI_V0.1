// Package terraform provides the TerraformRunner port implementation used in
// this build: a controllable Fake. The real runner (per-cloud modules,
// callback/poll completion per IDN-FR-006 step 3) lives in the infra repo;
// its contract is domain.TerraformInputs.
//
// TODO(identity): wire the production runner client (HTTP job API) once the
// infra repo publishes it.
package terraform

import (
	"context"
	"sync"

	"github.com/google/uuid"

	"github.com/windrose-ai/identity-service/internal/domain"
)

// Fake is a scriptable TerraformRunner for tests.
type Fake struct {
	mu sync.Mutex
	// ApplyErrs / DestroyErrs are popped per call; nil slice = always succeed.
	ApplyErrs   []error
	DestroyErrs []error
	// FailApplyAlways / FailDestroyAlways force persistent failure (AC-2, AC-9).
	FailApplyAlways   error
	FailDestroyAlways error

	ApplyCalls   int
	DestroyCalls int
	Applied      map[string]bool // identifier -> currently provisioned
}

func NewFake() *Fake { return &Fake{Applied: map[string]bool{}} }

func (f *Fake) Apply(_ context.Context, in domain.TerraformInputs) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ApplyCalls++
	if f.FailApplyAlways != nil {
		return f.FailApplyAlways
	}
	if len(f.ApplyErrs) > 0 {
		err := f.ApplyErrs[0]
		f.ApplyErrs = f.ApplyErrs[1:]
		if err != nil {
			return err
		}
	}
	f.Applied[in.Identifier] = true
	return nil
}

func (f *Fake) Destroy(_ context.Context, in domain.TerraformInputs) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.DestroyCalls++
	if f.FailDestroyAlways != nil {
		return f.FailDestroyAlways
	}
	if len(f.DestroyErrs) > 0 {
		err := f.DestroyErrs[0]
		f.DestroyErrs = f.DestroyErrs[1:]
		if err != nil {
			return err
		}
	}
	delete(f.Applied, in.Identifier)
	return nil
}

// HasInfra reports whether unmanaged infra exists for an identifier (AC-2).
func (f *Fake) HasInfra(identifier string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.Applied[identifier]
}

// FakeDBProvisioner implements domain.DatabaseProvisioner (step 4).
type FakeDBProvisioner struct {
	mu          sync.Mutex
	Schemas     map[string]bool
	CreateCalls int
	Err         error
}

func NewFakeDB() *FakeDBProvisioner { return &FakeDBProvisioner{Schemas: map[string]bool{}} }

func (f *FakeDBProvisioner) CreateSchemas(_ context.Context, _ uuid.UUID, prefix string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.CreateCalls++
	if f.Err != nil {
		return f.Err
	}
	f.Schemas[prefix] = true
	return nil
}

func (f *FakeDBProvisioner) DropSchemas(_ context.Context, _ uuid.UUID, prefix string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.Err != nil {
		return f.Err
	}
	delete(f.Schemas, prefix)
	return nil
}

// FakeProber implements domain.HealthProber (step 7 Verify, BR-5).
type FakeProber struct {
	mu    sync.Mutex
	Err   error
	Calls int
}

func (f *FakeProber) Probe(_ context.Context, _ *domain.Tenant) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Calls++
	return f.Err
}
