package enforce

import (
	"context"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/tool-plane/internal/authz"
	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/events"
	"github.com/windrose-ai/tool-plane/internal/mcp"
)

// CatalogResolver resolves the callable tool version + its backend target.
type CatalogResolver interface {
	// Resolve returns the version to invoke (published, or a specific in-range
	// version). status retired/quarantined are surfaced so the pipeline maps
	// TOOL_RETIRED / TOOL_KILLED. Returns store.ErrNotFound-equivalent as nil,err.
	ResolveVersion(ctx context.Context, toolID, version string) (*domain.ToolVersion, error)
	// BackendFor resolves the backend endpoint for a tool (owner_service prefix).
	BackendFor(ctx context.Context, toolID string) (mcp.BackendTarget, error)
}

// EnablementResolver resolves tenant enablement + constraints for a tool.
type EnablementResolver interface {
	GetTenantSettings(ctx context.Context, tenant uuid.UUID, toolID string) (*domain.TenantToolSettings, error)
}

// AuditSink records the digest-level invocation + emits ai.tool_invoked.v1.
type AuditSink interface {
	RecordInvocation(ctx context.Context, log *domain.InvocationLog, env events.Envelope) error
	InsertAudit(ctx context.Context, env events.Envelope) error
}

// KillChecker reports whether a call is killed (satisfied by *KillRegistry).
type KillChecker interface {
	IsKilled(tenant uuid.UUID, toolID, version string) (bool, string)
}

// Limiter consumes a token from a named per-minute bucket (satisfied by
// *RateLimiter).
type Limiter interface {
	Allow(ctx context.Context, key string, limit int) (bool, int, error)
}

// Request is one enforcement request (built from the MCP tools/call + JWT).
type Request struct {
	AgentID      string
	AgentVersion string
	Principal    string
	Typ          string
	OboSub       string
	Tenant       uuid.UUID
	TenantStr    string
	ToolID       string
	Version      string // requested; "" resolves the published version
	Args         map[string]any
	Toolset      []string
	// ProposalGrant is the raw RS256-signed proposal-execution grant token issued
	// by agent-runtime. It is VERIFIED (signature + issuer + exp + tenant/tool/
	// tier/args-digest binding) before it can skip the PROPOSAL_REQUIRED gate.
	// Nothing from the untrusted MCP body is trusted for authorization (TPL-FR-035).
	ProposalGrant string
	// Eval is honoured only when the gateway derived it from a trusted caller
	// claim (BR-16); a plain agent cannot self-declare eval mode.
	Eval    bool
	TraceID string
}

// Outcome is the enforcement result the MCP layer renders.
type Outcome struct {
	Decision    string         // events.Decision*
	Code        string         // domain.Code* ("" on success/proposal)
	HTTP        int            // HTTP analog
	Message     string
	Details     any            // per-field validation details
	Structured  map[string]any // PROPOSAL_REQUIRED / result structuredContent
	Output      map[string]any // backend output (allowed)
	Deprecation *domain.Deprecation
	RetryAfter  int
	Version     string
	Tier        string
	LatencyMS   int

	// internal accounting.
	crossTenant  bool
	backendKind  string
	affectedURNs []string
}

// IsError reports whether the outcome maps to an MCP isError=true result.
func (o Outcome) IsError() bool {
	return o.Code != "" && o.Decision != events.DecisionProposal
}

// Pipeline is the ordered, deny-by-default enforcement pipeline (BRD §3).
type Pipeline struct {
	Catalog    CatalogResolver
	Enablement EnablementResolver
	Kill       KillChecker
	OPA        authz.Checker
	Rate       Limiter
	Grants     GrantLoader
	Backend    mcp.BackendInvoker
	Audit      AuditSink
	// Proposals verifies signed proposal-execution grants (TPL-FR-035). Required
	// for write-tier execution; if nil, no grant can ever pass (fail closed).
	Proposals authz.GrantChecker
	// Health records per-tool-version health for SLA tracking (TPL-FR-050);
	// optional (nil-safe) so unit tests need not wire Redis.
	Health HealthSink
}

// HealthSink records the outcome of a backend-dispatched call for health/SLA
// tracking (TPL-FR-050). Satisfied by *HealthStore (Redis-backed).
type HealthSink interface {
	Record(ctx context.Context, toolID, version string, latencyMS int, ok bool, errKind string)
}

// Run executes the pipeline for one request and always emits exactly one
// ai.tool_invoked.v1 audit event (audit completeness, NFR). Any step failure
// denies — the backend is only reached after every gate passes (BR-1).
func (p *Pipeline) Run(ctx context.Context, req Request) Outcome {
	start := time.Now()
	oc := p.run(ctx, req)
	oc.LatencyMS = int(time.Since(start).Milliseconds())
	p.audit(ctx, req, oc)
	return oc
}

func (p *Pipeline) run(ctx context.Context, req Request) Outcome {
	// Step 1: resolve tool version (kill/enablement need its metadata).
	tv, err := p.Catalog.ResolveVersion(ctx, req.ToolID, req.Version)
	if err != nil {
		return Outcome{Decision: events.DecisionDeniedPolicy, Code: domain.CodeNotFound, HTTP: 404, Message: "tool not found"}
	}
	if tv == nil {
		return Outcome{Decision: events.DecisionDeniedPolicy, Code: domain.CodeNotFound, HTTP: 404, Message: "tool not found"}
	}
	oc := Outcome{Version: tv.Version, Tier: tv.PermissionTier}
	switch tv.Status {
	case domain.StatusRetired:
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodeToolRetired, 410, "tool version retired"
		return oc
	case domain.StatusQuarantined:
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionKilled, domain.CodeToolKilled, 423, "tool version quarantined"
		return oc
	case domain.StatusDraft:
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodeNotFound, 404, "tool not published"
		return oc
	}
	if tv.Status == domain.StatusDeprecated && tv.DeprecationEndsAt != nil {
		oc.Deprecation = &domain.Deprecation{EndsAt: *tv.DeprecationEndsAt, Message: "tool version deprecated; migrate before " + tv.DeprecationEndsAt.Format(time.RFC3339)}
	}

	// Step 2: kill/enablement gate.
	if killed, scope := p.Kill.IsKilled(req.Tenant, req.ToolID, tv.Version); killed {
		oc.Decision, oc.Code, oc.HTTP = events.DecisionKilled, domain.CodeToolKilled, 423
		oc.Message = "tool killed (" + scope + ")"
		return oc
	}
	settings, err := p.Enablement.GetTenantSettings(ctx, req.Tenant, req.ToolID)
	if err != nil {
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodePolicyUnavailable, 503, "enablement lookup failed"
		return oc
	}
	if settings == nil || !settings.Enabled {
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodeToolDisabled, 410, "tool not enabled for tenant"
		return oc
	}

	// Effective tier = min(tool tier, tenant max_tier_override) (TPL-FR-004).
	tier := tv.PermissionTier
	if settings.MaxTierOverride != "" && domain.TierRank(settings.MaxTierOverride) < domain.TierRank(tier) {
		tier = settings.MaxTierOverride
	}
	oc.Tier = tier

	// Verify the signed proposal-execution grant (TPL-FR-035). A grant only
	// counts if it is a valid RS256 token from agent-runtime bound to THIS
	// tenant/tool/tier and args digest — a forged, unsigned, expired, or
	// mismatched grant yields verifiedProposal=nil so the write falls back to the
	// PROPOSAL_REQUIRED gate. Nothing from the untrusted MCP body is trusted here.
	argsDigest := domain.ArgsDigest(req.Args)
	var verifiedProposal *authz.ProposalExecution
	if req.ProposalGrant != "" && p.Proposals != nil {
		if vp, verr := p.Proposals.VerifyGrant(ctx, req.ProposalGrant, req.TenantStr, req.ToolID, tier, argsDigest); verr == nil {
			verifiedProposal = vp
		}
	}

	// Affected URNs from schema annotations. The FULL set drives the cross-tenant
	// guard + audit; the obo-eligible SUBSET (role-governed resources opt out via
	// x-windrose-urn-obo:false) drives the per-resource OPA obo-grant intersection
	// (BR-12 / TPL-FR-032).
	affected := domain.AffectedURNs(tv.InputSchema, req.Args, req.TenantStr)
	oboURNs := domain.AffectedOboURNs(tv.InputSchema, req.Args, req.TenantStr)
	oc.affectedURNs = affected

	// Step 3a: cross-tenant URN guard (BR-12/AC-13): a URN whose tenant segment
	// != caller tenant → 404-shaped denial + security.cross_tenant_denied. Applies
	// to ALL annotated URNs, including obo-opted-out ones (a model version still
	// may not be cross-tenant even though its authz is role-based).
	for _, urn := range affected {
		if t := domain.URNTenant(urn); t != "" && t != req.TenantStr {
			oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodeNotFound, 404, "not found"
			oc.crossTenant = true
			return oc
		}
	}

	// Step 3b: OPA check (real sidecar). Fail-closed on infra error (BR-1). The
	// obo-grant intersection only considers obo-eligible URNs — role-governed
	// resources (model versions) are authorized by the action capability at the
	// owning service's facade, not a per-user resource grant.
	constraints := mergeConstraints(tv.InputSchema, settings.ArgumentConstraints)
	var grants []string
	if req.OboSub != "" && len(oboURNs) > 0 {
		grants, err = p.Grants.GrantsFor(ctx, req.TenantStr, req.OboSub, oboURNs)
		if err != nil {
			oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodePolicyUnavailable, 503, "grant lookup failed"
			return oc
		}
	}
	in := authz.Input{
		Subject:      authz.Subject{Type: "agent", AgentID: req.AgentID, AgentVersion: req.AgentVersion, Principal: req.Principal},
		OboSub:       req.OboSub,
		Tenant:       req.TenantStr,
		Action:       authz.ActionToolExecute,
		ToolID:       req.ToolID,
		ResourceURN:  domain.ToolURN(req.TenantStr, req.ToolID, tv.Version),
		Tier:         tier,
		MaxTier:      tier,
		AffectedURNs: oboURNs,
		Args:         req.Args,
		Toolset:      req.Toolset,
		Constraints:  constraints,
		OboGrants:    grants,
		ArgsDigest:   argsDigest,
		ProposalExecution: verifiedProposal,
	}
	dec, err := p.OPA.Check(ctx, in)
	if err != nil {
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodePolicyUnavailable, 503, "policy engine unavailable"
		return oc
	}
	if !dec.Allow {
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodePermission, 403, "permission denied: "+dec.Reason
		if dec.ViolatedConstraint != "" {
			oc.Details = map[string]any{"violated_constraint": dec.ViolatedConstraint}
		}
		return oc
	}

	// Step 4: rate limit — stricter of (tenant × tool) and (agent × tool) (BR-6).
	limit := RateForWeight(tv.CostWeight)
	if settings.RateLimitOverride != nil && settings.RateLimitOverride.PerMin > 0 {
		limit = settings.RateLimitOverride.PerMin
	}
	tenantKey := req.TenantStr + ":" + req.ToolID
	allowed, retry, err := p.Rate.Allow(ctx, tenantKey, limit)
	if err != nil {
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodePolicyUnavailable, 503, "rate store unavailable"
		return oc
	}
	if !allowed {
		oc.Decision, oc.Code, oc.HTTP, oc.Message, oc.RetryAfter = events.DecisionDeniedRate, domain.CodeRateLimited, 429, "rate limited", retry
		return oc
	}
	// Agent bucket (verified proposal executions bypass the agent bucket, BR-6).
	if verifiedProposal == nil {
		agentKey := "agent:" + req.Principal + ":" + req.ToolID
		allowed, retry, err = p.Rate.Allow(ctx, agentKey, limit)
		if err != nil {
			oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodePolicyUnavailable, 503, "rate store unavailable"
			return oc
		}
		if !allowed {
			oc.Decision, oc.Code, oc.HTTP, oc.Message, oc.RetryAfter = events.DecisionDeniedRate, domain.CodeRateLimited, 429, "rate limited", retry
			return oc
		}
	}

	// Step 5: schema validation (TPL-FR-034).
	if ferrs := domain.ValidateArgs(tv.InputSchema, req.Args); len(ferrs) > 0 {
		oc.Decision, oc.Code, oc.HTTP, oc.Message, oc.Details = events.DecisionDeniedSchema, domain.CodeValidation, 422, "argument validation failed", ferrs
		return oc
	}

	// Step 6: tier gate.
	// Eval mode (BR-16): read invokes normally; write tiers short-circuit to a
	// stub result and are audited as decision=stubbed — eval never mutates.
	if req.Eval && tier != domain.TierRead {
		oc.Decision = events.DecisionStubbed
		oc.Structured = map[string]any{"status": "stubbed"}
		oc.Output = map[string]any{"status": "stubbed"}
		return oc
	}
	if (tier == domain.TierWriteProposal || tier == domain.TierAdmin) && verifiedProposal == nil {
		// PROPOSAL_REQUIRED: return validated args + affected URNs, do not invoke.
		// A missing/forged/expired grant lands here — writes can never execute
		// without a verified, human-approved grant (TPL-FR-035).
		oc.Decision = events.DecisionProposal
		oc.Code = domain.CodeProposalRequired
		oc.HTTP = 200
		oc.Structured = map[string]any{
			"status":         "proposal_required",
			"tool_id":        req.ToolID,
			"version":        tv.Version,
			"validated_args": req.Args,
			"affected_urns":  affected,
			"side_effects":   tv.SideEffects,
		}
		return oc
	}

	// Step 7: kill recheck immediately before dispatch (BR-9).
	if killed, scope := p.Kill.IsKilled(req.Tenant, req.ToolID, tv.Version); killed {
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionKilled, domain.CodeToolKilled, 423, "tool killed ("+scope+")"
		return oc
	}

	// Step 8: invoke backend over real HTTP.
	target, err := p.Catalog.BackendFor(ctx, req.ToolID)
	if err != nil {
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, domain.CodeToolBackendError, 502, "no backend registered"
		return oc
	}
	target.P95MS = tv.DeclaredSLA.P95MS
	backendStart := time.Now()
	res, err := p.Backend.Invoke(ctx, target, mcp.Invocation{
		ToolID: req.ToolID, Version: tv.Version, Args: req.Args, Tenant: req.TenantStr,
		OboSub: req.OboSub, AgentID: req.AgentID, TraceID: req.TraceID,
	}, tier == domain.TierRead)
	backendMS := int(time.Since(backendStart).Milliseconds())
	if err != nil {
		code := domain.CodeToolBackendError
		httpCode := 502
		message := "backend error"
		if be, ok := err.(*mcp.BackendError); ok {
			switch be.Kind {
			case "timeout":
				code, httpCode = domain.CodeToolBackendTimeout, 504
			case "output_invalid":
				code, httpCode = domain.CodeToolOutputInvalid, 502
			case "backend_rejected":
				// The backend facade REJECTED the call (e.g. case-service's
				// "not allowed: case.disposition.approve") -- surface its real
				// status + message instead of masking it as a generic 502, and
				// critically, oc.Decision stays denied_policy (NOT allowed) so
				// this is never mistaken for a successful execution.
				httpCode = be.StatusCode
				message = be.Error()
				switch {
				case be.StatusCode == 403:
					code = domain.CodePermission
				case be.StatusCode == 404:
					code = domain.CodeNotFound
				case be.StatusCode == 400 || be.StatusCode == 422:
					code = domain.CodeValidation
				}
			}
		}
		oc.Decision, oc.Code, oc.HTTP, oc.Message = events.DecisionDeniedPolicy, code, httpCode, message
		oc.backendKind = backendKind(err)
		p.recordHealth(ctx, req.ToolID, tv.Version, backendMS, false, oc.backendKind)
		return oc
	}
	// Output schema validation (TPL-FR-036).
	if len(tv.OutputSchema) > 0 {
		if ferrs := domain.ValidateArgs(tv.OutputSchema, res.Output); len(ferrs) > 0 {
			oc.Decision, oc.Code, oc.HTTP, oc.Message, oc.Details = events.DecisionDeniedPolicy, domain.CodeToolOutputInvalid, 502, "tool output invalid", ferrs
			oc.backendKind = "output_invalid"
			p.recordHealth(ctx, req.ToolID, tv.Version, backendMS, false, "output_invalid")
			return oc
		}
	}
	oc.Decision = events.DecisionAllowed
	oc.Output = res.Output
	p.recordHealth(ctx, req.ToolID, tv.Version, backendMS, true, "")
	return oc
}

func (p *Pipeline) recordHealth(ctx context.Context, toolID, version string, latencyMS int, ok bool, errKind string) {
	if p.Health != nil {
		p.Health.Record(ctx, toolID, version, latencyMS, ok, errKind)
	}
}

func backendKind(err error) string {
	if be, ok := err.(*mcp.BackendError); ok {
		return be.Kind
	}
	return "backend_error"
}

// mergeConstraints compiles tool-declared bounds + tenant matrix constraints into
// the OPA constraints doc (BR-11 vocabulary: max, maxLength, enum_subset,
// maxItems). Tenant matrix overrides/augments tool bounds.
func mergeConstraints(schema map[string]any, tenant map[string]any) map[string]any {
	out := map[string]any{}
	// Tool-declared per-field bounds from the input schema.
	if props, ok := schema["properties"].(map[string]any); ok {
		for field, raw := range props {
			p, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			spec := map[string]any{}
			if v, ok := p["maxLength"]; ok {
				spec["maxLength"] = v
			}
			if v, ok := p["maximum"]; ok {
				spec["max"] = v
			}
			if v, ok := p["maxItems"]; ok {
				spec["maxItems"] = v
			}
			if len(spec) > 0 {
				out[field] = spec
			}
		}
	}
	// Tenant matrix constraints (BR-11 shape): {field: {maxLength|max|maxItems|enum_subset}}.
	for field, raw := range tenant {
		spec, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		merged := map[string]any{}
		if existing, ok := out[field].(map[string]any); ok {
			for k, v := range existing {
				merged[k] = v
			}
		}
		for k, v := range spec {
			merged[k] = v
		}
		out[field] = merged
	}
	return out
}
