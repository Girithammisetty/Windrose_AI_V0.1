package api

import (
	"bytes"
	"compress/gzip"
	"encoding/csv"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
)

func TestIfMatchVersion(t *testing.T) {
	r := httptest.NewRequest("PATCH", "/x", nil)
	if ifMatchVersion(r) != nil {
		t.Fatal("no header → nil")
	}
	r.Header.Set("If-Match", `"7"`)
	if v := ifMatchVersion(r); v == nil || *v != 7 {
		t.Fatalf("want 7, got %v", v)
	}
	r.Header.Set("If-Match", "nope")
	if ifMatchVersion(r) != nil {
		t.Fatal("non-numeric → nil")
	}
}

func TestAtoiDefaultAndSplitComma(t *testing.T) {
	if atoiDefault("", 50) != 50 || atoiDefault("bad", 9) != 9 || atoiDefault("12", 1) != 12 {
		t.Fatal("atoiDefault wrong")
	}
	got := splitComma(" status , severity ,, assignee")
	if len(got) != 3 || got[0] != "status" || got[2] != "assignee" {
		t.Fatalf("splitComma wrong: %v", got)
	}
}

// The copilot proposal field whitelist (CASE-FR-052).
func TestAllowedProposalFields(t *testing.T) {
	for _, f := range []string{"severity", "assigned_to_id", "disposition"} {
		if !allowedProposalFields[f] {
			t.Fatalf("%s must be allowed", f)
		}
	}
	for _, f := range []string{"due_date", "description", "status", "custom_fields"} {
		if allowedProposalFields[f] {
			t.Fatalf("%s must NOT be allowed", f)
		}
	}
}

func TestCatalogValidators(t *testing.T) {
	if !validCategory("true_positive") || validCategory("bogus") {
		t.Fatal("validCategory wrong")
	}
	if !validDataType("enum") || validDataType("blob") {
		t.Fatal("validDataType wrong")
	}
	if parsePurpose("create") != domain.PurposeCreate || parsePurpose("update") != domain.PurposeUpdate || parsePurpose("x") != domain.PurposeBoth {
		t.Fatal("parsePurpose wrong")
	}
}

// filter[status] → concrete statuses for export selection (CASE-FR-044).
func TestStatusesFromFilter(t *testing.T) {
	got := statusesFromFilter("open")
	if len(got) != 2 || got[0] != domain.StatusDraft || got[1] != domain.StatusInProgress {
		t.Fatalf("open expansion wrong: %v", got)
	}
	if len(statusesFromFilter("")) != 0 {
		t.Fatal("empty → no statuses")
	}
}

func TestParamsFromFilterMap(t *testing.T) {
	p := paramsFromFilterMap(map[string]string{"status": "closed", "assignee": "me", "severity": "high"}, "alice")
	if p.AssigneeID != "alice" {
		t.Fatalf("assignee 'me' must resolve to effective user, got %q", p.AssigneeID)
	}
	if p.Severity != "high" || len(p.Statuses) != 2 {
		t.Fatalf("filter mapping wrong: %+v", p)
	}
}

// gzipCSV emits a valid gzip CSV with header + one row per case (CASE-FR-044).
func TestGzipCSV(t *testing.T) {
	assignee := uuid.New()
	cases := []*domain.Case{{
		CaseNumber: 42, Status: domain.StatusResolved, Severity: domain.SeverityHigh, AssignedToID: &assignee,
		DatasetURN: "wr:t:dataset:dataset/x", RowPK: "row-1", ResolutionNote: "done",
		DueDate: time.Now(), CreatedAt: time.Now(), CaseVersion: 3,
	}}
	raw := gzipCSV(cases)
	gz, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		t.Fatalf("not gzip: %v", err)
	}
	recs, err := csv.NewReader(gz).ReadAll()
	if err != nil {
		t.Fatalf("bad csv: %v", err)
	}
	if len(recs) != 2 {
		t.Fatalf("want header + 1 row, got %d", len(recs))
	}
	if recs[0][0] != "case_number" || recs[1][0] != "42" || recs[1][1] != "resolved" {
		t.Fatalf("csv content wrong: %v", recs)
	}
}

// The bulk concurrency gate is a no-op (fail-open) when Redis is not wired.
func TestAcquireBulkSlotNoRedis(t *testing.T) {
	s := &Server{}
	release, ok := s.acquireBulkSlot(nil, uuid.New()) //nolint:staticcheck
	if !ok || release == nil {
		t.Fatal("nil Redis must fail open")
	}
	release()
}
