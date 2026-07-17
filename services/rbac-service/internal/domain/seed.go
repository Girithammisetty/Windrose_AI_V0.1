package domain

import (
	"fmt"

	"gopkg.in/yaml.v3"
)

// RoleSeed is one system role with its default action bindings (RBC-FR-024).
type RoleSeed struct {
	Name    string   `yaml:"name"`
	Actions []string `yaml:"actions"`
}

type rolesSeedFile struct {
	Roles []RoleSeed `yaml:"roles"`
}

// ParseRoleSeeds parses seed/roles_actions.yaml content and validates that
// every role is a known system role and every action exists in the canonical
// catalog with a legal name.
func ParseRoleSeeds(raw []byte) ([]RoleSeed, error) {
	var f rolesSeedFile
	if err := yaml.Unmarshal(raw, &f); err != nil {
		return nil, fmt.Errorf("parse roles seed: %w", err)
	}
	known := map[string]bool{}
	for _, n := range SystemRoleNames() {
		known[n] = true
	}
	catalog := CatalogMap()
	seen := map[string]bool{}
	for _, r := range f.Roles {
		if !known[r.Name] {
			return nil, fmt.Errorf("seed role %q is not a system role", r.Name)
		}
		if seen[r.Name] {
			return nil, fmt.Errorf("seed role %q duplicated", r.Name)
		}
		seen[r.Name] = true
		for _, a := range r.Actions {
			if _, _, _, err := ParseAction(a); err != nil {
				return nil, fmt.Errorf("seed role %q: %w", r.Name, err)
			}
			if _, ok := catalog[a]; !ok {
				return nil, fmt.Errorf("seed role %q references unknown catalog action %q", r.Name, a)
			}
		}
	}
	if len(f.Roles) != len(SystemRoleNames()) {
		return nil, fmt.Errorf("seed must define all %d system roles, got %d", len(SystemRoleNames()), len(f.Roles))
	}
	return f.Roles, nil
}
