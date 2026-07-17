package api

import (
	"testing"

	"github.com/windrose-ai/chart-service/internal/domain"
)

func TestScopeCrossFilters(t *testing.T) {
	// three charts: A and B share model "sales"; C is on model "ops".
	models := map[string]string{
		"A": "sales",
		"B": "sales",
		"C": "ops",
	}
	sel := domain.Filter{Field: "region", Op: "eq", Value: "West", Origin: "A"}

	t.Run("origin chart is not filtered by its own selection", func(t *testing.T) {
		got := scopeCrossFilters([]domain.Filter{sel}, "A", models)
		if len(got) != 0 {
			t.Fatalf("expected origin chart A to receive no filters, got %+v", got)
		}
	})

	t.Run("same-model sibling receives the filter", func(t *testing.T) {
		got := scopeCrossFilters([]domain.Filter{sel}, "B", models)
		if len(got) != 1 || got[0].Field != "region" || got[0].Value != "West" {
			t.Fatalf("expected sibling B to receive the region=West filter, got %+v", got)
		}
	})

	t.Run("cross-model chart is left unfiltered (graceful degradation)", func(t *testing.T) {
		got := scopeCrossFilters([]domain.Filter{sel}, "C", models)
		if len(got) != 0 {
			t.Fatalf("expected cross-model chart C to receive no filters, got %+v", got)
		}
	})

	t.Run("origin-less manual filter applies to every chart", func(t *testing.T) {
		manual := domain.Filter{Field: "status", Op: "eq", Value: "open"}
		for _, id := range []string{"A", "B", "C"} {
			got := scopeCrossFilters([]domain.Filter{manual}, id, models)
			if len(got) != 1 {
				t.Fatalf("expected chart %s to receive the manual filter, got %+v", id, got)
			}
		}
	})

	t.Run("nil in, nil out", func(t *testing.T) {
		if got := scopeCrossFilters(nil, "A", models); got != nil {
			t.Fatalf("expected nil, got %+v", got)
		}
	})

	t.Run("multiple origins: a chart gets siblings' selections but not its own", func(t *testing.T) {
		selB := domain.Filter{Field: "carrier", Op: "eq", Value: "Acme", Origin: "B"}
		got := scopeCrossFilters([]domain.Filter{sel, selB}, "B", models)
		// B excludes its own (selB), keeps A's (sel).
		if len(got) != 1 || got[0].Origin != "A" {
			t.Fatalf("expected B to keep only A's selection, got %+v", got)
		}
	})
}
