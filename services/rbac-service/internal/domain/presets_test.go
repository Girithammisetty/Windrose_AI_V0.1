package domain

import "testing"

// Presets must compose ONLY real catalog actions (role_actions FKs to actions),
// carry a non-empty bundle each, and never collide with a system role name.
func TestPresetRoleSeedsAreCatalogValid(t *testing.T) {
	cat := CatalogMap()
	seeds := PresetRoleSeeds()
	if len(seeds) != len(PresetRoleNames()) {
		t.Fatalf("expected %d presets, got %d", len(PresetRoleNames()), len(seeds))
	}
	for _, s := range seeds {
		if len(s.Actions) == 0 {
			t.Fatalf("preset %q has no actions", s.Name)
		}
		for _, a := range s.Actions {
			if _, ok := cat[a]; !ok {
				t.Fatalf("preset %q references unknown catalog action %q", s.Name, a)
			}
		}
	}
}

func TestPresetNamesDoNotCollideWithSystemRoles(t *testing.T) {
	sys := map[string]bool{}
	for _, n := range SystemRoleNames() {
		sys[n] = true
	}
	for _, n := range PresetRoleNames() {
		if sys[n] {
			t.Fatalf("preset name %q collides with a system role name", n)
		}
	}
}
