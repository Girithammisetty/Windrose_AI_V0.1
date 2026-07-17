package reports

import (
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRender_UsesRealDataNotPlaceholders(t *testing.T) {
	digest := &DashboardDigest{
		DashboardID:   uuid.New(),
		DashboardName: "Claims by region",
		Charts: []ChartDigestItem{
			{
				Name:    "Open claims",
				Columns: []string{"region", "count"},
				Rows: [][]any{
					{"West", 42},
					{"East", 17},
				},
				RowCount: 2,
			},
		},
	}
	at := time.Date(2026, 7, 12, 9, 0, 0, 0, time.UTC)
	out := Render(digest, at)

	if !strings.Contains(out.Subject, "Claims by region") {
		t.Fatalf("subject missing dashboard name: %q", out.Subject)
	}
	for _, want := range []string{"West", "42", "East", "17", "Open claims"} {
		if !strings.Contains(out.HTML, want) {
			t.Errorf("HTML missing real data %q\nHTML:\n%s", want, out.HTML)
		}
		if !strings.Contains(out.Text, want) {
			t.Errorf("text missing real data %q", want)
		}
	}
	for _, forbidden := range []string{"lorem", "ipsum", "placeholder", "TODO"} {
		if strings.Contains(strings.ToLower(out.HTML), forbidden) {
			t.Errorf("HTML contains fabricated/placeholder marker %q", forbidden)
		}
	}
}

func TestRender_SurfacesPerChartErrorHonestly(t *testing.T) {
	digest := &DashboardDigest{
		DashboardName: "Broken dashboard",
		Charts: []ChartDigestItem{
			{Name: "Bad chart", Error: "source unreachable"},
		},
	}
	out := Render(digest, time.Now())
	if !strings.Contains(out.HTML, "source unreachable") {
		t.Fatalf("expected the real chart-service error to be surfaced, got: %s", out.HTML)
	}
}

func TestRender_EscapesHTML(t *testing.T) {
	digest := &DashboardDigest{
		DashboardName: "<script>alert(1)</script>",
		Charts: []ChartDigestItem{
			{Name: "x", Columns: []string{"a"}, Rows: [][]any{{"<img src=x>"}}},
		},
	}
	out := Render(digest, time.Now())
	if strings.Contains(out.HTML, "<script>alert(1)</script>") {
		t.Fatal("dashboard name was not HTML-escaped")
	}
	if strings.Contains(out.HTML, "<img src=x>") {
		t.Fatal("row value was not HTML-escaped")
	}
}

func TestRender_TruncatesLongResultsAndSaysSo(t *testing.T) {
	var rows [][]any
	for i := 0; i < maxRowsInEmail+10; i++ {
		rows = append(rows, []any{i})
	}
	digest := &DashboardDigest{
		DashboardName: "Big",
		Charts:        []ChartDigestItem{{Name: "big chart", Columns: []string{"n"}, Rows: rows, RowCount: len(rows)}},
	}
	out := Render(digest, time.Now())
	if !strings.Contains(out.HTML, "Showing 20 of 30 rows") {
		t.Fatalf("expected a truncation notice, got:\n%s", out.HTML)
	}
}
