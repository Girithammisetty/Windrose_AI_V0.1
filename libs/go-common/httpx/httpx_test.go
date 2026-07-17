package httpx

import (
	"encoding/json"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
)

func TestParsePage(t *testing.T) {
	pr, err := ParsePage("", "")
	if err != nil || pr.Limit != DefaultPageLimit || pr.AfterID != nil {
		t.Fatalf("default: %+v err=%v", pr, err)
	}
	if pr, _ := ParsePage("500", ""); pr.Limit != MaxPageLimit {
		t.Fatalf("clamp: got %d want %d", pr.Limit, MaxPageLimit)
	}
	if _, err := ParsePage("0", ""); err != ErrBadLimit {
		t.Fatalf("want ErrBadLimit, got %v", err)
	}
	if _, err := ParsePage("", "not-base64!!"); err != ErrBadCursor {
		t.Fatalf("want ErrBadCursor, got %v", err)
	}
	id := uuid.New()
	c := EncodeCursor(id)
	pr, err = ParsePage("10", c)
	if err != nil || pr.AfterID == nil || *pr.AfterID != id {
		t.Fatalf("roundtrip cursor failed: %+v err=%v", pr, err)
	}
}

func TestBuildPage(t *testing.T) {
	type item struct{ ID uuid.UUID }
	idOf := func(i item) uuid.UUID { return i.ID }
	items := []item{{uuid.New()}, {uuid.New()}, {uuid.New()}}
	got, info := BuildPage(items, 2, idOf)
	if len(got) != 2 || !info.HasMore || info.NextCursor == nil {
		t.Fatalf("expected trimmed+hasmore, got len=%d info=%+v", len(got), info)
	}
	got, info = BuildPage(items[:1], 2, idOf)
	if len(got) != 1 || info.HasMore || info.NextCursor != nil {
		t.Fatalf("expected no more, got info=%+v", info)
	}
}

func TestWriteError(t *testing.T) {
	rec := httptest.NewRecorder()
	WriteError(rec, 429, CodeRateLimited, "slow down", "trace-1", nil, 30)
	if rec.Code != 429 {
		t.Fatalf("status=%d", rec.Code)
	}
	if ra := rec.Header().Get("Retry-After"); ra != "30" {
		t.Fatalf("retry-after=%q", ra)
	}
	var body ErrorBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Error.Code != CodeRateLimited || body.Error.TraceID != "trace-1" {
		t.Fatalf("body=%+v", body.Error)
	}
}
