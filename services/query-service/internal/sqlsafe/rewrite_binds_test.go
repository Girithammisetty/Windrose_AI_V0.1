package sqlsafe

import (
	"strings"
	"testing"
)

// Binds mode (chart-service /sql/run contract): raw $n placeholders with the
// ordered values passed through as prepared-statement args (never spliced).
func TestRewriteBindsPassthrough(t *testing.T) {
	rw, err := RewriteBinds(`SELECT * FROM "main"."claims" WHERE claim_type = $1 AND amount > $2`,
		[]any{"auto", 1000}, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(rw.SQL, "$1") || !strings.Contains(rw.SQL, "$2") {
		t.Fatalf("positional placeholders must survive verbatim: %s", rw.SQL)
	}
	if len(rw.Args) != 2 || rw.Args[0] != "auto" || rw.Args[1] != 1000 {
		t.Fatalf("args must be the binds in order: %#v", rw.Args)
	}
}

func TestRewriteBindsDatasetRef(t *testing.T) {
	rw, err := RewriteBinds(`SELECT * FROM {{dataset('claims')}} WHERE vendor = $1`,
		[]any{"ACME"}, map[string]string{"claims@0": `"main"."claims"`})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(rw.SQL, `"main"."claims"`) {
		t.Fatalf("dataset ref must resolve to the engine-quoted ident: %s", rw.SQL)
	}
}

func TestRewriteBindsCountMismatch(t *testing.T) {
	// placeholder index beyond binds
	if _, err := RewriteBinds(`SELECT $1, $2`, []any{"only-one"}, nil); err == nil {
		t.Fatal("expected bind count mismatch error ($2 with 1 bind)")
	}
	// unreferenced bind
	if _, err := RewriteBinds(`SELECT $1`, []any{"a", "b"}, nil); err == nil {
		t.Fatal("expected bind count mismatch error (2 binds, only $1 referenced)")
	}
}

func TestRewriteBindsRejectsMixedStyles(t *testing.T) {
	if _, err := RewriteBinds(`SELECT $1 WHERE x = :named`, []any{"v"}, nil); err == nil {
		t.Fatal("expected rejection of mixed $n + :name styles")
	}
}

func TestScanStillRejectsPositionalWithoutBinds(t *testing.T) {
	if _, err := Scan(`SELECT $1`); err == nil {
		t.Fatal("plain Scan must keep rejecting $n (named-variable mode owns numbering)")
	}
	if _, err := ScanBinds(`SELECT ?`); err == nil {
		t.Fatal("? placeholders stay rejected even in binds mode")
	}
}
