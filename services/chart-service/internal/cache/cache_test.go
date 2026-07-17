package cache

import (
	"testing"

	"github.com/windrose-ai/chart-service/internal/domain"
)

// TestKeyDeterminism: the same variables/filters (any order) → same key/etag.
func TestKeyDeterminism(t *testing.T) {
	in1 := KeyInput{
		Variables:  map[string]any{"a": 1, "b": 2},
		Filters:    []domain.Filter{{Field: "region", Op: "eq", Value: "EMEA"}, {Field: "year", Op: "gte", Value: 2020}},
		Aggregated: true, Page: "",
	}
	in2 := KeyInput{
		Variables:  map[string]any{"b": 2, "a": 1},
		Filters:    []domain.Filter{{Field: "year", Op: "gte", Value: 2020}, {Field: "region", Op: "eq", Value: "EMEA"}},
		Aggregated: true, Page: "",
	}
	k1 := Key("t1", "c1", 4, in1)
	k2 := Key("t1", "c1", 4, in2)
	if k1 != k2 {
		t.Fatalf("keys should match regardless of order:\n%s\n%s", k1, k2)
	}
	if ETag("t1", "c1", 4, in1) != ETag("t1", "c1", 4, in2) {
		t.Fatal("etags should match")
	}
}

// TestKeyVersionEpoch: bumping chart_version changes the key (cache epoch).
func TestKeyVersionEpoch(t *testing.T) {
	in := KeyInput{Aggregated: true}
	if Key("t1", "c1", 4, in) == Key("t1", "c1", 5, in) {
		t.Fatal("version bump must change the cache key")
	}
}
