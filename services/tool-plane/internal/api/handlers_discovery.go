package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/store"
)

// discoverySearchReq is the semantic discovery request (TPL-FR-020).
type discoverySearchReq struct {
	Query      string   `json:"query"`
	TopK       int      `json:"top_k"`
	TierFilter []string `json:"tier_filter"`
	Tags       []string `json:"tags"`
}

type discoveryHit struct {
	ToolID      string             `json:"tool_id"`
	Version     string             `json:"version"`
	Score       float64            `json:"score"`
	Tier        string             `json:"tier"`
	Description string             `json:"description"`
	InputSchema map[string]any     `json:"input_schema"`
	Deprecation *domain.Deprecation `json:"deprecation"`
}

// handleDiscovery embeds the query with the REAL model and ranks the tenant's
// enabled tools by pgvector cosine similarity (AC-6: enabled tool appears,
// disabled tool never does). Results are caller-scoped via the enablement join.
func (s *RegistryServer) handleDiscovery(w http.ResponseWriter, r *http.Request) {
	tenant, ok := tenantOf(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing tenant"))
		return
	}
	var req discoverySearchReq
	if err := decodeJSON(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	if req.Query == "" {
		writeErr(w, r, domain.EValidation("query is required", nil))
		return
	}
	if req.TopK <= 0 || req.TopK > 20 {
		req.TopK = 5
	}
	vec, err := s.Embedder.Embed(r.Context(), req.Query)
	if err != nil {
		writeErr(w, r, domain.EInternal("embedding failed: "+err.Error()))
		return
	}
	hits, err := s.Store.SearchByEmbedding(r.Context(), tenant, vec, req.TopK, req.TierFilter)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	// Filter out killed tools (caller-scoping, TPL-FR-011/020).
	out := make([]discoveryHit, 0, len(hits))
	for _, h := range hits {
		if killed, _ := s.Kill.IsKilled(tenant, h.Version.ToolID, h.Version.Version); killed {
			continue
		}
		var dep *domain.Deprecation
		if h.Version.Status == domain.StatusDeprecated && h.Version.DeprecationEndsAt != nil {
			dep = &domain.Deprecation{EndsAt: *h.Version.DeprecationEndsAt, Message: "deprecated"}
		}
		out = append(out, discoveryHit{
			ToolID: h.Version.ToolID, Version: h.Version.Version, Score: h.Score,
			Tier: h.Version.PermissionTier, Description: h.Version.SemanticDescription,
			InputSchema: h.Version.InputSchema, Deprecation: dep,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": out})
}

// handleListTools returns a cursor page of catalog tools (MASTER-FR-022).
func (s *RegistryServer) handleListTools(w http.ResponseWriter, r *http.Request) {
	f := store.ToolFilter{
		OwnerService: r.URL.Query().Get("filter[owner_service]"),
		AfterID:      r.URL.Query().Get("cursor"),
	}
	tools, next, err := s.Store.ListTools(r.Context(), f)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	page := map[string]any{"has_more": next != ""}
	if next != "" {
		page["next_cursor"] = next
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": tools, "page": page})
}

// handleGetSchema returns a version's input schema (runtime schema fetch,
// TPL-FR-022). version query param optional (defaults to published).
func (s *RegistryServer) handleGetSchema(w http.ResponseWriter, r *http.Request) {
	toolID := chi.URLParam(r, "id")
	version := r.URL.Query().Get("version")
	v, err := s.Store.GetPublishedVersion(r.Context(), toolID, version)
	if err == store.ErrNotFound {
		writeErr(w, r, domain.ENotFound())
		return
	} else if err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{
		"tool_id": toolID, "version": v.Version, "input_schema": v.InputSchema, "output_schema": v.OutputSchema,
	}})
}

// diffReq is a proposed schema for CI diffing (TPL-FR-003).
type diffReq struct {
	InputSchema map[string]any `json:"input_schema"`
}

// handleDiff reports whether the proposed input schema differs from the
// published version — CI uses this to require a version bump on schema changes.
func (s *RegistryServer) handleDiff(w http.ResponseWriter, r *http.Request) {
	toolID := chi.URLParam(r, "id")
	var req diffReq
	if err := decodeJSON(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	v, err := s.Store.GetPublishedVersion(r.Context(), toolID, "")
	if err == store.ErrNotFound {
		writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"changed": true, "reason": "no published version"}})
		return
	} else if err != nil {
		writeErr(w, r, err)
		return
	}
	changed := domain.ArgsDigest(req.InputSchema) != domain.ArgsDigest(v.InputSchema)
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{
		"changed": changed, "published_version": v.Version, "requires_version_bump": changed,
	}})
}

// handleHealth returns per-tool-version health (TPL-FR-050): the real rolling
// Redis counters (success/error taxonomy, p50/p95/p99 latency) alongside the
// declared SLA.
func (s *RegistryServer) handleHealth(w http.ResponseWriter, r *http.Request) {
	toolID := chi.URLParam(r, "id")
	versions, err := s.Store.ListVersions(r.Context(), toolID)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if len(versions) == 0 {
		writeErr(w, r, domain.ENotFound())
		return
	}
	out := make([]map[string]any, 0, len(versions))
	for _, v := range versions {
		entry := map[string]any{
			"version": v.Version, "status": v.Status, "declared_sla": v.DeclaredSLA,
		}
		if s.Health != nil {
			if snap, err := s.Health.Snapshot(r.Context(), toolID, v.Version); err == nil {
				entry["health"] = snap
			}
		}
		out = append(out, entry)
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"tool_id": toolID, "versions": out}})
}
