package api

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/embed"
	"github.com/windrose-ai/tool-plane/internal/events"
	"github.com/windrose-ai/tool-plane/internal/store"
)

// registerToolReq is the tool registration payload (manifest, TPL-FR-001/003).
type registerToolReq struct {
	ToolID           string   `json:"tool_id"`
	DisplayName      string   `json:"display_name"`
	OwnerService     string   `json:"owner_service"`
	OwnerTeam        string   `json:"owner_team"`
	EnabledByDefault bool     `json:"enabled_by_default"`
	SideEffects      string   `json:"side_effects"`
	Tags             []string `json:"tags"`
}

func actorFromClaims(r *http.Request) domain.Actor {
	c := claims(r)
	if c == nil {
		return domain.Actor{Type: "service", ID: "unknown"}
	}
	switch c.Typ {
	case domain.TypAgentAutonomous, domain.TypAgentOBO:
		return domain.Actor{Type: "agent", ID: c.AgentID}
	case domain.TypService:
		return domain.Actor{Type: "service", ID: c.Sub}
	default:
		return domain.Actor{Type: "user", ID: c.Sub}
	}
}

// handleRegisterTool registers a catalog tool. Manifest identity binding (BR-14/
// AC-15): when a SPIFFE identity is presented (X-Spiffe-Id, set by the mesh), its
// service segment must equal owner_service, else 403 + security audit and the
// catalog is unchanged.
func (s *RegistryServer) handleRegisterTool(w http.ResponseWriter, r *http.Request) {
	var req registerToolReq
	if err := decodeJSON(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	if req.ToolID == "" || !strings.Contains(req.ToolID, ".") {
		writeErr(w, r, domain.EValidation("tool_id must be a namespaced name (e.g. case.assign)", nil))
		return
	}
	if req.OwnerService == "" {
		writeErr(w, r, domain.EValidation("owner_service is required", nil))
		return
	}
	if req.SideEffects == "" {
		req.SideEffects = domain.SideEffectNone
	}
	// BR-14 manifest identity binding.
	if spiffe := r.Header.Get("X-Spiffe-Id"); spiffe != "" {
		if svc := spiffeService(spiffe); svc != req.OwnerService {
			s.auditSecurity(r, "manifest_identity_mismatch", req.OwnerService, spiffe)
			writeErr(w, r, domain.EPermission("SPIFFE identity does not match owner_service"))
			return
		}
	}
	t := &domain.Tool{
		ToolID: req.ToolID, DisplayName: req.DisplayName, OwnerService: req.OwnerService,
		OwnerTeam: req.OwnerTeam, EnabledByDefault: req.EnabledByDefault, SideEffects: req.SideEffects, Tags: req.Tags,
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvToolRegistered, domain.PlatformTenant,
		actorFromClaims(r), nil, domain.ToolURN("platform", req.ToolID, ""), TraceID(r.Context()),
		map[string]any{"tool_id": req.ToolID, "owner_service": req.OwnerService})
	if err := s.Store.CreateTool(r.Context(), t, []events.Envelope{env}); err != nil {
		if err == store.ErrConflict {
			writeErr(w, r, domain.EConflict("tool already exists"))
			return
		}
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"data": t})
}

// addVersionReq is a new-version payload (TPL-FR-001).
type addVersionReq struct {
	Version             string          `json:"version"`
	SemanticDescription string          `json:"semantic_description"`
	InputSchema         map[string]any  `json:"input_schema"`
	OutputSchema        map[string]any  `json:"output_schema"`
	PermissionTier      string          `json:"permission_tier"`
	CostWeight          int             `json:"cost_weight"`
	DeclaredSLA         domain.DeclaredSLA `json:"declared_sla"`
	SideEffects         string          `json:"side_effects"`
	Examples            []domain.Example `json:"examples"`
}

// handleAddVersion creates a draft version, validating schema + description
// quality (BR-15) and BR-2 (destructive can never be write-direct).
func (s *RegistryServer) handleAddVersion(w http.ResponseWriter, r *http.Request) {
	toolID := chi.URLParam(r, "id")
	var req addVersionReq
	if err := decodeJSON(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	if _, err := domain.ParseSemVer(req.Version); err != nil {
		writeErr(w, r, domain.EValidation(err.Error(), nil))
		return
	}
	tool, err := s.Store.GetTool(r.Context(), toolID)
	if err == store.ErrNotFound {
		writeErr(w, r, domain.ENotFound())
		return
	} else if err != nil {
		writeErr(w, r, err)
		return
	}
	// BR-15 semantic-description quality gate.
	if len(req.SemanticDescription) < 40 || !strings.Contains(strings.ToLower(req.SemanticDescription), "use when") {
		writeErr(w, r, domain.EValidation("semantic_description must be ≥40 chars and include a usage sentence (\"Use when …\")", nil))
		return
	}
	if req.SideEffects == "" {
		req.SideEffects = tool.SideEffects
	}
	// BR-2: destructive tools can never be write-direct.
	if req.SideEffects == domain.SideEffectDestructive && req.PermissionTier == domain.TierWriteDirect {
		writeErr(w, r, domain.EValidation("destructive tools cannot be write-direct (BR-2)", nil))
		return
	}
	if req.CostWeight < 1 || req.CostWeight > 10 {
		writeErr(w, r, domain.EValidation("cost_weight must be 1..10", nil))
		return
	}
	// Schema validity (AC-7 draft can hold an invalid schema; publish rejects it,
	// but we reject obviously malformed input schemas here too for fast feedback
	// on the required object/additionalProperties shape only when present).
	v := &domain.ToolVersion{
		ToolID: toolID, Version: req.Version, SemanticDescription: req.SemanticDescription,
		InputSchema: req.InputSchema, OutputSchema: req.OutputSchema, PermissionTier: req.PermissionTier,
		CostWeight: req.CostWeight, DeclaredSLA: req.DeclaredSLA, SideEffects: req.SideEffects, Examples: req.Examples,
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvToolRegistered, domain.PlatformTenant,
		actorFromClaims(r), nil, domain.ToolURN("platform", toolID, req.Version), TraceID(r.Context()),
		map[string]any{"tool_id": toolID, "version": req.Version, "status": "draft"})
	if err := s.Store.CreateVersion(r.Context(), v, []events.Envelope{env}); err != nil {
		if err == store.ErrConflict {
			writeErr(w, r, domain.EConflict("version already exists"))
			return
		}
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"data": v})
}

// handlePublish validates schemas, computes the REAL embedding (Ollama), and
// publishes the version (AC-7: publish fails VALIDATION_FAILED on invalid schema;
// on success the embedding row is populated before the tool is discoverable).
func (s *RegistryServer) handlePublish(w http.ResponseWriter, r *http.Request) {
	toolID := chi.URLParam(r, "id")
	version := chi.URLParam(r, "v")
	v, err := s.Store.GetVersion(r.Context(), toolID, version)
	if err == store.ErrNotFound {
		writeErr(w, r, domain.ENotFound())
		return
	} else if err != nil {
		writeErr(w, r, err)
		return
	}
	if v.Status != domain.StatusDraft {
		writeErr(w, r, domain.EConflict("only draft versions can be published"))
		return
	}
	if ferrs := domain.ValidateSchemaDoc(v.InputSchema); len(ferrs) > 0 {
		writeErr(w, r, domain.EValidation("invalid input_schema", ferrs))
		return
	}
	// Real embedding at publish time (TPL-FR-020/021).
	text := embed.EmbeddingText(v.SemanticDescription, exampleDescs(v.Examples))
	vec, err := s.Embedder.Embed(r.Context(), text)
	if err != nil {
		writeErr(w, r, domain.EInternal("embedding failed: "+err.Error()))
		return
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvToolVersionPublished, domain.PlatformTenant,
		actorFromClaims(r), nil, domain.ToolURN("platform", toolID, version), TraceID(r.Context()),
		map[string]any{"tool_id": toolID, "version": version})
	if err := s.Store.PublishVersion(r.Context(), toolID, version, vec, s.Embedder.Model(), []events.Envelope{env}); err != nil {
		if err == store.ErrConflict {
			writeErr(w, r, domain.EConflict("another version is already published; deprecate it first"))
			return
		}
		writeErr(w, r, err)
		return
	}
	v.Status = domain.StatusPublished
	writeJSON(w, http.StatusOK, map[string]any{"data": v})
}

type deprecateReq struct {
	DeprecationEndsAt *time.Time `json:"deprecation_ends_at"`
}

// handleDeprecate deprecates a published version (window ≥30d, default 90d).
func (s *RegistryServer) handleDeprecate(w http.ResponseWriter, r *http.Request) {
	toolID := chi.URLParam(r, "id")
	version := chi.URLParam(r, "v")
	var req deprecateReq
	_ = decodeJSON(r, &req)
	ends := time.Now().Add(90 * 24 * time.Hour)
	if req.DeprecationEndsAt != nil {
		ends = *req.DeprecationEndsAt
	}
	if ends.Before(time.Now().Add(30 * 24 * time.Hour)) {
		writeErr(w, r, domain.EValidation("deprecation window must be ≥30 days", nil))
		return
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvToolDeprecated, domain.PlatformTenant,
		actorFromClaims(r), nil, domain.ToolURN("platform", toolID, version), TraceID(r.Context()),
		map[string]any{"tool_id": toolID, "version": version, "deprecation_ends_at": ends})
	if err := s.Store.SetVersionStatus(r.Context(), toolID, version, domain.StatusDeprecated, &ends, []events.Envelope{env}); err != nil {
		if err == store.ErrNotFound {
			writeErr(w, r, domain.ENotFound())
			return
		}
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"status": "deprecated", "deprecation_ends_at": ends}})
}

type retireReq struct {
	Force  bool   `json:"force"`
	Reason string `json:"reason"`
}

// handleRetire retires a version (guard: window elapsed OR operator force).
func (s *RegistryServer) handleRetire(w http.ResponseWriter, r *http.Request) {
	toolID := chi.URLParam(r, "id")
	version := chi.URLParam(r, "v")
	var req retireReq
	_ = decodeJSON(r, &req)
	v, err := s.Store.GetVersion(r.Context(), toolID, version)
	if err == store.ErrNotFound {
		writeErr(w, r, domain.ENotFound())
		return
	} else if err != nil {
		writeErr(w, r, err)
		return
	}
	windowElapsed := v.DeprecationEndsAt != nil && time.Now().After(*v.DeprecationEndsAt)
	if !windowElapsed && !req.Force {
		writeErr(w, r, domain.EValidation("deprecation window has not elapsed; retire requires force + reason", nil))
		return
	}
	if req.Force && req.Reason == "" {
		writeErr(w, r, domain.EValidation("force retire requires a reason", nil))
		return
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvToolRetired, domain.PlatformTenant,
		actorFromClaims(r), nil, domain.ToolURN("platform", toolID, version), TraceID(r.Context()),
		map[string]any{"tool_id": toolID, "version": version, "forced": req.Force})
	if err := s.Store.SetVersionStatus(r.Context(), toolID, version, domain.StatusRetired, nil, []events.Envelope{env}); err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"status": "retired"}})
}

func exampleDescs(examples []domain.Example) []string {
	out := make([]string, 0, len(examples))
	for _, e := range examples {
		out = append(out, e.Description)
	}
	return out
}

// spiffeService extracts the service (workload) segment from a SPIFFE id like
// spiffe://windrose/ns/prod/sa/case-service → case-service.
func spiffeService(spiffe string) string {
	i := strings.LastIndex(spiffe, "/sa/")
	if i < 0 {
		return ""
	}
	return spiffe[i+4:]
}

func (s *RegistryServer) auditSecurity(r *http.Request, reason, ownerService, spiffe string) {
	env := events.NewEnvelope(events.TopicToolEvents, events.EvCrossTenantDenied, domain.PlatformTenant,
		actorFromClaims(r), nil, "", TraceID(r.Context()),
		map[string]any{"reason": reason, "owner_service": ownerService, "spiffe_id": spiffe})
	_ = s.Store.InsertAudit(context.Background(), env)
}
