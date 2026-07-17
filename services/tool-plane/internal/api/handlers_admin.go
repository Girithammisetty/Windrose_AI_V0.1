package api

import (
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/events"
	"github.com/windrose-ai/tool-plane/internal/store"
)

// enablementReq is the per-tenant enablement payload (TPL-FR-004).
type enablementReq struct {
	Enabled             bool                      `json:"enabled"`
	MaxTierOverride     string                    `json:"max_tier_override"`
	ArgumentConstraints map[string]any            `json:"argument_constraints"`
	RateLimitOverride   *domain.RateLimitOverride `json:"rate_limit_override"`
}

// handleEnablement upserts the caller-tenant's enablement for a tool. BR-2:
// a destructive tool can never be enabled at write-direct (checked against the
// tool's declared side effects).
func (s *RegistryServer) handleEnablement(w http.ResponseWriter, r *http.Request) {
	tenant, ok := tenantOf(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing tenant"))
		return
	}
	toolID := chi.URLParam(r, "id")
	var req enablementReq
	if err := decodeJSON(r, &req); err != nil {
		writeErr(w, r, err)
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
	if req.MaxTierOverride != "" && domain.TierRank(req.MaxTierOverride) < 0 {
		writeErr(w, r, domain.EValidation("invalid max_tier_override", nil))
		return
	}
	// BR-2: destructive tools can never be write-direct.
	if tool.SideEffects == domain.SideEffectDestructive && req.MaxTierOverride == domain.TierWriteDirect {
		writeErr(w, r, domain.EValidation("destructive tools cannot be enabled at write-direct (BR-2)", nil))
		return
	}
	st := &domain.TenantToolSettings{
		TenantID: tenant, ToolID: toolID, Enabled: req.Enabled, MaxTierOverride: req.MaxTierOverride,
		ArgumentConstraints: req.ArgumentConstraints, RateLimitOverride: req.RateLimitOverride,
	}
	evType := events.EvTenantToolEnabled
	if !req.Enabled {
		evType = events.EvTenantToolDisabled
	}
	env := events.NewEnvelope(events.TopicToolEvents, evType, tenant, actorFromClaims(r), nil,
		domain.ToolURN(tenant.String(), toolID, ""), TraceID(r.Context()),
		map[string]any{"tool_id": toolID, "enabled": req.Enabled})
	if err := s.Store.PutTenantSettings(r.Context(), st, []events.Envelope{env}); err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": st})
}

// killReq is the kill-switch payload (TPL-FR-052). reason is required.
type killReq struct {
	Scope    string  `json:"scope"`
	ToolID   string  `json:"tool_id"`
	Version  string  `json:"version"`
	TenantID *string `json:"tenant_id"`
	Reason   string  `json:"reason"`
}

// handleListKills returns every currently-active kill switch (platform-scoped
// table — ActiveKills already selects WHERE active=true; no tenant filter is
// applied here since a tenant-scoped kill's tenant_id, if set, is itself part
// of the payload the admin UI needs to see across tenants, TPL-FR-052).
func (s *RegistryServer) handleListKills(w http.ResponseWriter, r *http.Request) {
	kills, err := s.Store.ActiveKills(r.Context())
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if kills == nil {
		kills = []*domain.KillSwitch{} // never serialize `null` for an empty list
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": kills})
}

// handleCreateKill sets a kill switch and announces it via Redis pub/sub so all
// gateway replicas enforce it within ≤5s (AC-5). Reason is required (TPL-FR-053).
func (s *RegistryServer) handleCreateKill(w http.ResponseWriter, r *http.Request) {
	var req killReq
	if err := decodeJSON(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	if req.Reason == "" {
		writeErr(w, r, domain.EValidation("reason is required", nil))
		return
	}
	if req.Scope != domain.KillScopeTool && req.Scope != domain.KillScopeToolVersion && req.Scope != domain.KillScopeToolTenant {
		writeErr(w, r, domain.EValidation("invalid scope", nil))
		return
	}
	c := claims(r)
	k := &domain.KillSwitch{
		ID: domain.NewID(), Scope: req.Scope, ToolID: req.ToolID, Version: req.Version,
		Reason: req.Reason, SetBy: c.Sub, Active: true,
	}
	if req.Scope == domain.KillScopeToolTenant {
		// Tenant-scoped kill: the kill's tenant is ALWAYS the caller's verified
		// token tenant. The body tenant_id is never trusted for normal callers —
		// accepting it let tenant A kill tools for tenant B (cross-tenant kill).
		// Only a verifiable platform operator (super_admin scope on a user/service
		// token, see isPlatformOperator) may target a different tenant; any other
		// caller sending a mismatched tenant_id is REJECTED (VALIDATION_FAILED)
		// rather than silently overridden, so the client learns its request was
		// not honored as written.
		tid := c.TenantID
		if req.TenantID != nil && *req.TenantID != c.TenantID {
			if !isPlatformOperator(c) {
				writeErr(w, r, domain.EValidation("tenant_id must match the caller's tenant (cross-tenant kill is platform-operator only)", nil))
				return
			}
			tid = *req.TenantID
		}
		u, err := uuid.Parse(tid)
		if err != nil {
			writeErr(w, r, domain.EValidation("invalid tenant_id", nil))
			return
		}
		k.TenantID = &u
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvToolKilled, domain.PlatformTenant,
		actorFromClaims(r), nil, domain.ToolURN("platform", req.ToolID, req.Version), TraceID(r.Context()),
		map[string]any{"tool_id": req.ToolID, "version": req.Version, "scope": req.Scope, "reason": req.Reason, "set_by": c.Sub})
	if err := s.Store.CreateKill(r.Context(), k, []events.Envelope{env}); err != nil {
		if err == store.ErrConflict {
			writeErr(w, r, domain.EConflict("kill switch already active"))
			return
		}
		writeErr(w, r, err)
		return
	}
	if err := s.Kill.Announce(r.Context(), k, true); err != nil {
		writeErr(w, r, domain.EInternal("kill announce failed: "+err.Error()))
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"data": map[string]any{"id": k.ID, "active": true, "set_by": "user:" + c.Sub}})
}

// handleDeleteKill unsets a kill switch and announces the change (AC-5 restore).
func (s *RegistryServer) handleDeleteKill(w http.ResponseWriter, r *http.Request) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.EValidation("invalid id", nil))
		return
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvToolUnkilled, domain.PlatformTenant,
		actorFromClaims(r), nil, "", TraceID(r.Context()), map[string]any{"kill_id": id.String()})
	k, err := s.Store.DeactivateKill(r.Context(), id, []events.Envelope{env})
	if err == store.ErrNotFound {
		writeErr(w, r, domain.ENotFound())
		return
	} else if err != nil {
		writeErr(w, r, err)
		return
	}
	if err := s.Kill.Announce(r.Context(), k, false); err != nil {
		writeErr(w, r, domain.EInternal("unkill announce failed: "+err.Error()))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"id": id, "active": false}})
}

// byoReq is a BYO submission (TPL-FR-040).
type byoReq struct {
	Manifest          map[string]any `json:"manifest"`
	EndpointURL       string         `json:"endpoint_url"`
	AuthMethod        string         `json:"auth_method"`
	RequestedTier     string         `json:"requested_tier"`
	EgressDescription string         `json:"data_egress_description"`
}

// handleBYOSubmit records a pending BYO submission. External write-direct is
// forbidden (TPL-FR-040): requested tier must be ≤ write-proposal.
func (s *RegistryServer) handleBYOSubmit(w http.ResponseWriter, r *http.Request) {
	tenant, ok := tenantOf(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("missing tenant"))
		return
	}
	var req byoReq
	if err := decodeJSON(r, &req); err != nil {
		writeErr(w, r, err)
		return
	}
	if req.EndpointURL == "" {
		writeErr(w, r, domain.EValidation("endpoint_url is required", nil))
		return
	}
	if domain.TierRank(req.RequestedTier) > domain.TierRank(domain.TierWriteProposal) {
		writeErr(w, r, domain.EValidation("external tools cannot request write-direct or admin (TPL-FR-040)", nil))
		return
	}
	if req.AuthMethod == "" {
		req.AuthMethod = "api_key"
	}
	b := &domain.BYOSubmission{
		ID: domain.NewID(), Manifest: req.Manifest, EndpointURL: req.EndpointURL, AuthMethod: req.AuthMethod,
		RequestedTier: req.RequestedTier, EgressDescription: req.EgressDescription, Status: domain.BYOPending,
	}
	env := events.NewEnvelope(events.TopicToolEvents, events.EvBYOSubmitted, tenant, actorFromClaims(r), nil,
		"", TraceID(r.Context()), map[string]any{"byo_id": b.ID.String(), "endpoint_url": req.EndpointURL})
	if err := s.Store.CreateBYO(r.Context(), b, tenant, []events.Envelope{env}); err != nil {
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"data": b})
}

// handleBYOList returns the BYO submission queue, newest first (Tier 2b admin
// surface: an approver lists pending submissions before deciding them).
// filter[status] narrows to pending_approval|approved|rejected; omitted returns
// every submission (capped). Guarded by tool.byo.approve — the queue is the
// approver's work list, not a general read.
func (s *RegistryServer) handleBYOList(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("filter[status]")
	if status != "" && status != domain.BYOPending && status != domain.BYOApproved && status != domain.BYORejected {
		writeErr(w, r, domain.EValidation("invalid filter[status]", nil))
		return
	}
	limit := 50
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 1 && n <= 200 {
			limit = n
		}
	}
	list, err := s.Store.ListBYO(r.Context(), status, limit)
	if err != nil {
		writeErr(w, r, err)
		return
	}
	if list == nil {
		list = []*domain.BYOSubmission{} // never serialize `null` for an empty list
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": list})
}

type byoDecisionReq struct {
	Message string `json:"message"`
}

func (s *RegistryServer) handleBYOApprove(w http.ResponseWriter, r *http.Request) {
	s.decideBYO(w, r, domain.BYOApproved, events.EvBYOApproved)
}

func (s *RegistryServer) handleBYOReject(w http.ResponseWriter, r *http.Request) {
	s.decideBYO(w, r, domain.BYORejected, events.EvBYORejected)
}

func (s *RegistryServer) decideBYO(w http.ResponseWriter, r *http.Request, status, evType string) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, r, domain.EValidation("invalid id", nil))
		return
	}
	var req byoDecisionReq
	_ = decodeJSON(r, &req)
	c := claims(r)
	// External write-direct is forbidden at approval too (TPL-FR-040).
	if status == domain.BYOApproved {
		b, err := s.Store.GetBYO(r.Context(), id)
		if err == store.ErrNotFound {
			writeErr(w, r, domain.ENotFound())
			return
		} else if err != nil {
			writeErr(w, r, err)
			return
		}
		if domain.TierRank(b.RequestedTier) > domain.TierRank(domain.TierWriteProposal) {
			writeErr(w, r, domain.EValidation("external write-direct/admin forbidden", nil))
			return
		}
	}
	env := events.NewEnvelope(events.TopicToolEvents, evType, domain.PlatformTenant, actorFromClaims(r), nil,
		"", TraceID(r.Context()), map[string]any{"byo_id": id.String(), "decided_by": c.Sub})
	if err := s.Store.DecideBYO(r.Context(), id, status, c.Sub, req.Message, []events.Envelope{env}); err != nil {
		if err == store.ErrNotFound {
			writeErr(w, r, domain.ENotFound())
			return
		}
		writeErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{"id": id, "status": status, "decided_by": c.Sub}})
}
