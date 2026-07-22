package enforce

import (
	"context"
	"log/slog"

	"github.com/datacern-ai/tool-plane/internal/domain"
	"github.com/datacern-ai/tool-plane/internal/events"
)

// audit emits exactly one ai.tool_invoked.v1 event per attempt with the args
// DIGEST (never raw values, BR-3/MASTER-FR-042) + affected URNs, and records the
// digest-level invocation_log row (TPL-FR-037). Cross-tenant denials additionally
// emit security.cross_tenant_denied (MASTER-FR-003/AC-13).
func (p *Pipeline) audit(ctx context.Context, req Request, oc Outcome) {
	urns := oc.affectedURNs
	if urns == nil {
		urns = []string{}
	}
	digest := domain.ArgsDigest(req.Args)
	var via *domain.ViaAgent
	actor := domain.Actor{Type: "agent", ID: req.AgentID}
	if req.OboSub != "" {
		actor = domain.Actor{Type: "user", ID: req.OboSub}
		via = &domain.ViaAgent{AgentID: req.AgentID, Version: req.AgentVersion}
	}
	resourceURN := domain.ToolURN(req.TenantStr, req.ToolID, oc.Version)

	payload := map[string]any{
		"invocation_id": domain.NewID().String(),
		"tenant_id":     req.TenantStr,
		"agent_id":      req.AgentID,
		"agent_version": req.AgentVersion,
		"tool_id":       req.ToolID,
		"tool_version":  oc.Version,
		"tier":          oc.Tier,
		"decision":      oc.Decision,
		"args_digest":   digest,
		"affected_urns": urns,
		"latency_ms":    oc.LatencyMS,
		"trace_id":      req.TraceID,
	}
	if req.OboSub != "" {
		payload["obo_sub"] = req.OboSub
	}
	if oc.Code != "" {
		payload["error_code"] = oc.Code
	}
	// Persist WHY a non-allow decision happened (rego deny reason, violated
	// constraint, backend rejection message) — error_code alone made denials
	// undiagnosable from the audit trail.
	denyReason := ""
	if oc.Decision != events.DecisionAllowed && oc.Message != "" {
		denyReason = oc.Message
		payload["deny_reason"] = oc.Message
	}

	env := events.NewEnvelope(events.TopicToolInvoked, events.EvToolInvoked, req.Tenant, actor, via, resourceURN, req.TraceID, payload)

	log := &domain.InvocationLog{
		ID: domain.NewID(), TenantID: req.Tenant, AgentID: req.AgentID, AgentVersion: req.AgentVersion,
		OboSub: req.OboSub, ToolID: req.ToolID, ToolVersion: oc.Version, Tier: oc.Tier,
		Decision: oc.Decision, ErrorCode: oc.Code, DenyReason: denyReason, ArgsDigest: digest, URNs: urns,
		LatencyMS: oc.LatencyMS, TraceID: req.TraceID,
	}
	if err := p.Audit.RecordInvocation(ctx, log, env); err != nil {
		slog.Warn("audit record failed", "err", err, "tool", req.ToolID)
	}

	if oc.crossTenant {
		ct := events.NewEnvelope(events.TopicToolEvents, events.EvCrossTenantDenied, req.Tenant, actor, via, resourceURN, req.TraceID,
			map[string]any{"tool_id": req.ToolID, "reason": "cross_tenant_urn_in_args"})
		if err := p.Audit.InsertAudit(ctx, ct); err != nil {
			slog.Warn("cross-tenant audit failed", "err", err)
		}
	}
}
