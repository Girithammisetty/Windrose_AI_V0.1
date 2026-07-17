package integration

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"

	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/embed"
	"github.com/windrose-ai/tool-plane/internal/store"
)

// caseAssignSchema is the BRD example input schema (URN-annotated case_id).
func caseAssignSchema() map[string]any {
	return map[string]any{
		"$schema": "https://json-schema.org/draft/2020-12/schema",
		"type":    "object", "additionalProperties": false,
		"required": []any{"case_id", "assignee_id"},
		"properties": map[string]any{
			"case_id":     map[string]any{"type": "string", "x-windrose-urn": "wr:{tenant}:case:case/{value}"},
			"assignee_id": map[string]any{"type": "string"},
			"note":        map[string]any{"type": "string", "maxLength": float64(2000)},
			"bulk_limit":  map[string]any{"type": "integer", "maximum": float64(1000)},
		},
	}
}

func caseGetSchema() map[string]any {
	return map[string]any{
		"type": "object", "additionalProperties": false, "required": []any{"case_id"},
		"properties": map[string]any{
			"case_id": map[string]any{"type": "string", "x-windrose-urn": "wr:{tenant}:case:case/{value}"},
		},
	}
}

// publishTool registers a tool + version and publishes it with a REAL Ollama
// embedding stored in pgvector (the discovery path depends on it).
func (h *harness) publishTool(t *testing.T, toolID, desc, tier, sideEffects string, schema map[string]any) {
	h.publishToolSLA(t, toolID, desc, tier, sideEffects, schema, domain.DeclaredSLA{P95MS: 250, ErrorRatePct: 0.5})
}

// publishToolSLA is publishTool with an explicit declared SLA (health/quarantine).
func (h *harness) publishToolSLA(t *testing.T, toolID, desc, tier, sideEffects string, schema map[string]any, sla domain.DeclaredSLA) {
	t.Helper()
	ctx := context.Background()
	owner := toolID[:index(toolID, '.')] + "-service"
	tool := &domain.Tool{
		ToolID: toolID, DisplayName: toolID, OwnerService: owner, OwnerTeam: "team",
		EnabledByDefault: true, SideEffects: sideEffects,
	}
	if err := h.store.CreateTool(ctx, tool, nil); err != nil && !errors.Is(err, store.ErrConflict) {
		t.Fatalf("create tool %s: %v", toolID, err)
	}
	v := &domain.ToolVersion{
		ToolID: toolID, Version: "1.0.0", SemanticDescription: desc, InputSchema: schema,
		OutputSchema: map[string]any{"type": "object", "additionalProperties": true, "properties": map[string]any{}},
		PermissionTier: tier, CostWeight: 3, SideEffects: sideEffects,
		DeclaredSLA: sla,
	}
	if err := h.store.CreateVersion(ctx, v, nil); err != nil && !errors.Is(err, store.ErrConflict) {
		t.Fatalf("create version %s: %v", toolID, err)
	}
	// Idempotent across tests that share a tool_id: skip if already published.
	if existing, err := h.store.GetVersion(ctx, toolID, "1.0.0"); err == nil && existing.Status != domain.StatusDraft {
		return
	}
	vec, err := h.embedder.Embed(ctx, embed.EmbeddingText(desc, nil))
	if err != nil {
		t.Fatalf("embed %s: %v", toolID, err)
	}
	if len(vec) != embed.Dim {
		t.Fatalf("expected %d-dim embedding, got %d", embed.Dim, len(vec))
	}
	if err := h.store.PublishVersion(ctx, toolID, "1.0.0", vec, h.embedder.Model(), nil); err != nil {
		t.Fatalf("publish %s: %v", toolID, err)
	}
}

// enableTool sets tenant enablement with optional constraints / max-tier override.
func (h *harness) enableTool(t *testing.T, tenant uuid.UUID, toolID string, constraints map[string]any, maxTier string, rlo *domain.RateLimitOverride) {
	t.Helper()
	st := &domain.TenantToolSettings{
		TenantID: tenant, ToolID: toolID, Enabled: true, MaxTierOverride: maxTier,
		ArgumentConstraints: constraints, RateLimitOverride: rlo,
	}
	if err := h.store.PutTenantSettings(context.Background(), st, nil); err != nil {
		t.Fatalf("enable %s: %v", toolID, err)
	}
}

// registerBackend registers an MCP facade backend for an owner service.
func (h *harness) registerBackend(t *testing.T, ownerService, url string) {
	t.Helper()
	b := &domain.MCPBackend{
		Name: ownerService, InternalURL: url, SpiffeID: "spiffe://windrose/ns/prod/sa/" + ownerService,
		Kind: "internal", Status: "active",
	}
	if err := h.store.CreateBackend(context.Background(), b); err != nil {
		t.Fatalf("register backend %s: %v", ownerService, err)
	}
}

func index(s string, r byte) int {
	for i := 0; i < len(s); i++ {
		if s[i] == r {
			return i
		}
	}
	return len(s)
}
