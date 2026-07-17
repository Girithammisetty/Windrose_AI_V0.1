package api

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/windrose-ai/chart-service/internal/authz"
)

// allActionConstants maps every authz.Action* constant NAME to its string
// value. Used to resolve the identifiers the drift test scans out of the
// handler source.
var allActionConstants = map[string]string{
	"ActionDashboardCreate": authz.ActionDashboardCreate,
	"ActionDashboardRead":   authz.ActionDashboardRead,
	"ActionDashboardUpdate": authz.ActionDashboardUpdate,
	"ActionDashboardDelete": authz.ActionDashboardDelete,
	"ActionDashboardShare":  authz.ActionDashboardShare,
	"ActionDashboardExport": authz.ActionDashboardExport,
	"ActionChartCreate":     authz.ActionChartCreate,
	"ActionChartRead":       authz.ActionChartRead,
	"ActionChartUpdate":     authz.ActionChartUpdate,
	"ActionChartDelete":     authz.ActionChartDelete,
	"ActionChartExport":     authz.ActionChartExport,
}

// TestGuardedActionsAreRegistered scans the handler source for every
// authz.Action* the route guards reference and asserts each is (a) a known
// constant and (b) present in the registered rbac manifest. This closes the gap
// that let the archive/link regression through: a guard authorizing an action
// rbac never accepted → 403 for everyone.
func TestGuardedActionsAreRegistered(t *testing.T) {
	registered := map[string]bool{}
	for _, e := range authz.Manifest() {
		registered[e.Action] = true
	}

	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
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
