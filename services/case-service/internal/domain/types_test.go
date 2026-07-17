package domain

import (
	"strings"
	"testing"
)

// Dedup key is sha256(dataset_urn ‖ row_pk); keyless rows are dedup-exempt
// (CASE-FR-005, BR-2).
func TestDedupKey(t *testing.T) {
	k1, ok1 := DedupKey("wr:t:dataset:dataset/ds1", "row-1")
	if !ok1 || !strings.HasPrefix(k1, "sha256:") {
		t.Fatalf("expected sha256 key, got %q ok=%v", k1, ok1)
	}
	k2, _ := DedupKey("wr:t:dataset:dataset/ds1", "row-1")
	if k1 != k2 {
		t.Fatal("dedup key must be deterministic")
	}
	k3, _ := DedupKey("wr:t:dataset:dataset/ds1", "row-2")
	if k1 == k3 {
		t.Fatal("distinct rows must have distinct keys")
	}
	if _, ok := DedupKey("wr:t:dataset:dataset/ds1", ""); ok {
		t.Fatal("keyless (empty row_pk) rows must be dedup-exempt")
	}
}

// Display projection is capped at 12 cols × 256 chars with truncation flagged,
// never rejected (BR-11).
func TestTruncateProjection(t *testing.T) {
	in := map[string]string{}
	for i := 0; i < 20; i++ {
		in["col"+string(rune('a'+i))] = "v"
	}
	out, trunc := TruncateProjection(in)
	if len(out) != MaxProjectionCols || !trunc {
		t.Fatalf("want %d cols + truncated, got %d trunc=%v", MaxProjectionCols, len(out), trunc)
	}

	long := strings.Repeat("x", 500)
	out2, trunc2 := TruncateProjection(map[string]string{"c": long})
	if !trunc2 || len([]rune(out2["c"])) > MaxProjectionColLen {
		t.Fatalf("value not truncated: len=%d trunc=%v", len([]rune(out2["c"])), trunc2)
	}
	if !strings.HasSuffix(out2["c"], "…") {
		t.Fatal("truncated value must carry ellipsis marker")
	}

	small := map[string]string{"a": "1", "b": "2"}
	if _, trunc3 := TruncateProjection(small); trunc3 {
		t.Fatal("small projection must not be flagged truncated")
	}
}

func TestSeverityBump(t *testing.T) {
	cases := map[string]string{
		SeverityLow: SeverityMedium, SeverityMedium: SeverityHigh,
		SeverityHigh: SeverityCritical, SeverityCritical: SeverityCritical,
	}
	for in, want := range cases {
		if got := BumpSeverity(in); got != want {
			t.Fatalf("BumpSeverity(%s)=%s want %s", in, got, want)
		}
	}
}

func TestStatusRoundTrip(t *testing.T) {
	for _, s := range []Status{StatusDraft, StatusInProgress, StatusResolved, StatusUnassigned, StatusClosed} {
		got, ok := ParseStatus(s.String())
		if !ok || got != s {
			t.Fatalf("round-trip failed for %v", s)
		}
	}
}
