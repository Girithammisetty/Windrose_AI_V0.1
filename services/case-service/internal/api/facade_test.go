package api

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

// The backend facade (GAP-2) is the tool-plane federation target. These guard
// tests exercise the peer-identity and tool-id contract without a store; the
// full apply-through-facade path is covered end-to-end by `make e2e`.

func TestToolFacade_RequiresMeshPeerIdentity(t *testing.T) {
	s := &Server{}
	body := `{"tool_id":"case.apply_disposition","tenant":"t","obo_sub":"u","args":{"case_id":"c"}}`
	r := httptest.NewRequest("POST", "/internal/v1/mcp/invoke", strings.NewReader(body))
	w := httptest.NewRecorder()
	s.handleToolFacade(w, r) // no X-Spiffe-Id
	if w.Code != 403 {
		t.Fatalf("missing SPIFFE peer identity must be 403, got %d", w.Code)
	}
	var out struct {
		Output map[string]any `json:"output"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	if out.Output["applied"] != false {
		t.Fatalf("expected applied=false, got %v", out.Output)
	}
}

func TestToolFacade_UnknownToolID(t *testing.T) {
	s := &Server{}
	body := `{"tool_id":"case.something_else","tenant":"t","obo_sub":"u","args":{}}`
	r := httptest.NewRequest("POST", "/internal/v1/mcp/invoke", strings.NewReader(body))
	r.Header.Set("X-Spiffe-Id", "spiffe://windrose/ns/tools/sa/mcp-gateway")
	w := httptest.NewRecorder()
	s.handleToolFacade(w, r)
	if w.Code != 404 {
		t.Fatalf("unknown tool_id must be 404, got %d", w.Code)
	}
}

func TestToolFacade_BadTenant(t *testing.T) {
	s := &Server{}
	body := `{"tool_id":"case.apply_disposition","tenant":"not-a-uuid","obo_sub":"u","args":{"case_id":"c"}}`
	r := httptest.NewRequest("POST", "/internal/v1/mcp/invoke", strings.NewReader(body))
	r.Header.Set("X-Spiffe-Id", "spiffe://windrose/ns/tools/sa/mcp-gateway")
	w := httptest.NewRecorder()
	s.handleToolFacade(w, r)
	if w.Code != 400 {
		t.Fatalf("invalid tenant must be 400, got %d", w.Code)
	}
}
