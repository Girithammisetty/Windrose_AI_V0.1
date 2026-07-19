package api

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/authz"
	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
	"github.com/windrose-ai/case-service/internal/store"
)

// facadeReq is tool-plane's backend-facade contract (BRD 13, TPL-FR-012): the
// mcp-gateway POSTs {tool_id, version, args, tenant, obo_sub, agent_id} to a
// tool's owning-service backend URL after the full enforcement pipeline (OPA +
// signed proposal-execution grant). case-service hosts the backend for the
// case.apply_disposition write-proposal tool here.
type facadeReq struct {
	ToolID  string         `json:"tool_id"`
	Version string         `json:"version"`
	Args    map[string]any `json:"args"`
	Tenant  string         `json:"tenant"`
	OboSub  string         `json:"obo_sub"`
	AgentID string         `json:"agent_id"`
}

// handleToolFacade is the real MCP backend facade the tool-plane federates to
// (GAP-2). It applies an approved disposition through the SAME governed path as
// the human apply-proposal endpoint (dual attribution, case.disposition_applied
// learning-loop event, idempotent by proposal_urn). The peer identity is the
// mesh-injected SPIFFE id (X-Spiffe-Id); authorization is re-checked against the
// real OPA sidecar for the effective human (obo_sub) — the backend never blindly
// trusts the gateway.
func (s *Server) handleToolFacade(w http.ResponseWriter, r *http.Request) {
	// Mesh peer identity (MASTER-FR-014). In prod this rides mTLS; the gateway
	// forwards the intended peer identity in X-Spiffe-Id. This facade trusts
	// X-Spiffe-Id ONLY because it must sit behind a NetworkPolicy + mTLS (the
	// header is set by the verified in-cluster peer, not by an arbitrary client)
	// — so with no allowlist configured there is nothing to verify the header
	// against and we MUST fail closed rather than accept any non-empty value.
	spiffe := r.Header.Get("X-Spiffe-Id")
	allowed := os.Getenv("CASE_FACADE_ALLOWED_SPIFFE")
	if allowed == "" {
		facadeError(w, http.StatusForbidden, "facade disabled: CASE_FACADE_ALLOWED_SPIFFE is not configured")
		return
	}
	ok := false
	for _, a := range strings.Split(allowed, ",") {
		if a = strings.TrimSpace(a); a != "" && a == spiffe {
			ok = true
			break
		}
	}
	if !ok {
		facadeError(w, http.StatusForbidden, "facade requires an allowed SPIFFE peer identity")
		return
	}

	var req facadeReq
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		facadeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if req.ToolID != "case.apply_disposition" {
		facadeError(w, http.StatusNotFound, "unknown tool_id")
		return
	}
	tenant, err := uuid.Parse(req.Tenant)
	if err != nil {
		facadeError(w, http.StatusBadRequest, "invalid tenant")
		return
	}
	caseIDRaw, _ := req.Args["case_id"].(string)
	caseID, err := uuid.Parse(caseIDRaw)
	if err != nil {
		facadeError(w, http.StatusBadRequest, "args.case_id must be a uuid")
		return
	}
	dispIDRaw, _ := req.Args["disposition_id"].(string)
	dispID, err := uuid.Parse(dispIDRaw)
	if err != nil {
		facadeError(w, http.StatusBadRequest, "args.disposition_id must be a uuid")
		return
	}
	note, _ := req.Args["resolution_note"].(string)
	newSeverity, _ := req.Args["severity"].(string)
	proposalURN, _ := req.Args["proposal_urn"].(string)
	if proposalURN == "" {
		proposalURN = "wr:" + req.Tenant + ":ai:proposal/" + req.ToolID + "/" + caseID.String()
	}

	// Load the case for its workspace (authz context) and existence.
	c0, err := s.Store.GetCase(r.Context(), tenant, caseID)
	if err != nil {
		facadeError(w, http.StatusNotFound, "case not found")
		return
	}

	// Real governed authorization for the effective human (obo_sub) against the
	// OPA sidecar. This is the same action the human apply-proposal path checks.
	if s.Authz != nil {
		in := authz.Input{
			Subject:     authz.Subject{ID: req.OboSub, Typ: "user"},
			Action:      authz.ActionProposalApply,
			WorkspaceID: c0.WorkspaceID.String(),
			Tenant:      req.Tenant,
			ResourceURN: events.CaseURN(tenant, caseID),
		}
		if !s.Authz.Allow(r.Context(), in) {
			facadeError(w, http.StatusForbidden, "not allowed: "+authz.ActionProposalApply)
			return
		}
	}

	// Idempotent replay by proposal_urn (BR-9).
	if prev, ok, err := s.Store.GetAppliedProposal(r.Context(), tenant, proposalURN); err == nil && ok {
		facadeOutput(w, map[string]any{"applied": true, "case_id": caseID.String(), "replayed": true, "case": prev})
		return
	}

	if newSeverity != "" && !domain.ValidSeverity(newSeverity) {
		facadeError(w, http.StatusBadRequest, "invalid severity")
		return
	}
	disp, err := s.Store.GetDisposition(r.Context(), tenant, dispID)
	if err != nil {
		facadeError(w, http.StatusBadRequest, "disposition not found")
		return
	}

	// Dual attribution built from the federated call (not a JWT): the approving
	// human is the actor, the agent is via_agent (MASTER-FR-041).
	op := domain.Op{
		Tenant:      tenant,
		WorkspaceID: c0.WorkspaceID,
		Actor:       domain.Actor{Type: "user", ID: req.OboSub},
		ViaAgent:    &domain.ViaAgent{AgentID: req.AgentID},
		UserID:      req.OboSub,
		TraceID:     TraceID(r.Context()),
	}

	c, err := s.Store.MutateCase(r.Context(), op, caseID, nil, func(c *domain.Case) (store.Mutation, error) {
		urn := events.CaseURN(op.Tenant, c.ID)
		var acts []domain.Activity
		var evs []events.Envelope
		timers := store.TimerPlan{}
		if newSeverity != "" && newSeverity != c.Severity {
			old := c.Severity
			c.Severity = newSeverity
			a := mkActivity(op, events.EvSeverityChanged, map[string]any{"severity": old}, map[string]any{"severity": newSeverity})
			a.ProposalURN = proposalURN
			acts = append(acts, a)
			evs = append(evs, events.NewEnvelope(events.EvSeverityChanged, op, urn, map[string]any{"case_number": c.CaseNumber, "severity": newSeverity}))
		}
		m, err := s.resolveMutation(op, c, disp, note, proposalURN)
		if err != nil {
			return store.Mutation{}, err
		}
		acts = append(acts, m.Activities...)
		evs = append(evs, m.Events...)
		timers = m.Timers
		return store.Mutation{Activities: acts, Events: evs, Timers: timers}, nil
	})
	if err != nil {
		slog.Warn("facade apply-disposition failed", "err", err, "case_id", caseID)
		facadeError(w, http.StatusUnprocessableEntity, "apply failed: "+err.Error())
		return
	}
	view := caseView(c)
	_ = s.Store.PutAppliedProposal(r.Context(), tenant, proposalURN, c.ID, view)
	facadeOutput(w, map[string]any{
		"applied": true, "case_id": caseID.String(), "proposal_urn": proposalURN,
		"disposition_code": disp.Code, "severity": c.Severity, "case": view,
	})
}

func facadeOutput(w http.ResponseWriter, out map[string]any) {
	writeJSON(w, http.StatusOK, map[string]any{"output": out})
}

func facadeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]any{"output": map[string]any{"applied": false, "error": msg}})
}
