package api

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/windrose-ai/tool-plane/internal/authz"
)

// allActionConstants maps every authz.Action* constant NAME to its string
// value. Used to resolve the identifiers the drift test scans out of the
// handler + enforce-pipeline source.
var allActionConstants = map[string]string{
	"ActionToolCreate":       authz.ActionToolCreate,
	"ActionToolRead":         authz.ActionToolRead,
	"ActionToolUpdate":       authz.ActionToolUpdate,
	"ActionToolDelete":       authz.ActionToolDelete,
	"ActionToolExecute":      authz.ActionToolExecute,
	"ActionEnablementUpdate": authz.ActionEnablementUpdate,
	"ActionKillCreate":       authz.ActionKillCreate,
	"ActionKillDelete":       authz.ActionKillDelete,
	"ActionKillRead":         authz.ActionKillRead,
	"ActionBYOCreate":        authz.ActionBYOCreate,
	"ActionBYOApprove":       authz.ActionBYOApprove,
}

// TestGuardedActionsAreRegistered scans the route-guard source (this package)
// AND the enforce pipeline for every authz.Action* referenced and asserts each
// is (a) a known constant and (b) present in the registered rbac manifest.
// This closes the drift gap: a guard authorizing an action rbac never accepted
// → unknown_action → 403 for everyone.
func TestGuardedActionsAreRegistered(t *testing.T) {
	registered := map[string]bool{}
	for _, e := range authz.Manifest() {
		registered[e.Action] = true
	}

	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	// The gateway pipeline's per-invocation OPA action must be registered too.
	enforceFiles, err := filepath.Glob(filepath.Join("..", "enforce", "*.go"))
	if err != nil {
		t.Fatal(err)
	}
	files = append(files, enforceFiles...)

	re := regexp.MustCompile(`authz\.(Action\w+)`)
	seen := map[string]bool{}
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range re.FindAllStringSubmatch(string(src), -1) {
			seen[m[1]] = true
		}
	}
	if len(seen) == 0 {
		t.Fatal("no guarded actions found in handler source (scan broke)")
	}
	for name := range seen {
		val, ok := allActionConstants[name]
		if !ok {
			t.Errorf("guard references unknown/removed action constant authz.%s", name)
			continue
		}
		if !registered[val] {
			t.Errorf("guard uses %q (authz.%s) which is NOT in the rbac manifest → would 403 for everyone", val, name)
		}
	}
}

// TestEveryAPIRouteIsGuarded scans server.go and asserts every /api/v1 route
// registration goes through requireAction — no route may be JWT-only again.
func TestEveryAPIRouteIsGuarded(t *testing.T) {
	src, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	routeRe := regexp.MustCompile(`(?m)^\s*r\.(?:With\([^)]*\)\.)?(Get|Post|Put|Patch|Delete)\(`)
	guardedRe := regexp.MustCompile(`r\.With\(s\.requireAction\(`)
	lines := strings.Split(string(src), "\n")
	inAPI := false
	for i, line := range lines {
		if strings.Contains(line, `r.Route("/api/v1"`) {
			inAPI = true
			continue
		}
		if !inAPI {
			continue
		}
		if routeRe.MatchString(line) && !guardedRe.MatchString(line) {
			t.Errorf("server.go:%d: /api/v1 route registered without requireAction guard: %s", i+1, strings.TrimSpace(line))
		}
	}
}
