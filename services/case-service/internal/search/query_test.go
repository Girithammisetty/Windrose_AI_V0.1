package search

import (
	"reflect"
	"testing"

	"github.com/windrose-ai/case-service/internal/domain"
)

// filter[status]=open|closed expand to the V1 pseudo-filter sets (CASE-FR-042).
func TestExpandStatus(t *testing.T) {
	if got := ExpandStatus("open"); !reflect.DeepEqual(got, []string{"draft", "in_progress"}) {
		t.Fatalf("open expansion wrong: %v", got)
	}
	if got := ExpandStatus("closed"); !reflect.DeepEqual(got, []string{"resolved", "closed"}) {
		t.Fatalf("closed expansion wrong: %v", got)
	}
	if got := ExpandStatus("in_progress"); !reflect.DeepEqual(got, []string{"in_progress"}) {
		t.Fatalf("concrete status wrong: %v", got)
	}
	if ExpandStatus("") != nil {
		t.Fatal("empty must expand to nil")
	}
}

func TestFacetFieldMapping(t *testing.T) {
	// assigned_to_id matches the REST detail field so list+detail agree
	// (bff Case.assignee hydration).
	if facetField("assignee") != "assigned_to_id" {
		t.Fatal("assignee facet must map to assigned_to_id keyword")
	}
	if facetField("bogus") != "" {
		t.Fatal("unknown facet must be dropped")
	}
}

// DocFromCase carries the tenant filter field and case_version for external
// versioning (CASE-FR-040/041).
func TestDocFromCase(t *testing.T) {
	c := &domain.Case{Status: domain.StatusInProgress, Severity: domain.SeverityHigh, CaseVersion: 7,
		DisplayProjection: map[string]string{"merchant": "ACME"}}
	d := DocFromCase(c, "some comment")
	if d.Status != "in_progress" || d.CaseVersion != 7 || d.CommentText != "some comment" {
		t.Fatalf("doc projection wrong: %+v", d)
	}
}
