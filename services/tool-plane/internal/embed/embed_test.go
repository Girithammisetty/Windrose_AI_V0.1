package embed

import (
	"math"
	"testing"
)

func TestCosine(t *testing.T) {
	a := []float32{1, 0, 0}
	if got := Cosine(a, a); math.Abs(got-1) > 1e-6 {
		t.Fatalf("identical vectors → 1, got %v", got)
	}
	if got := Cosine([]float32{1, 0}, []float32{0, 1}); math.Abs(got) > 1e-6 {
		t.Fatalf("orthogonal → 0, got %v", got)
	}
	if got := Cosine([]float32{1}, []float32{1, 2}); got != 0 {
		t.Fatalf("mismatched length → 0, got %v", got)
	}
}

func TestEmbeddingText(t *testing.T) {
	got := EmbeddingText("desc", []string{"ex1", "", "ex2"})
	want := "desc\nex1\nex2"
	if got != want {
		t.Fatalf("want %q, got %q", want, got)
	}
}
