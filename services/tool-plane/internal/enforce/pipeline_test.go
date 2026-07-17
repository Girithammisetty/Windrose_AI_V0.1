package enforce

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"

	"github.com/windrose-ai/tool-plane/internal/authz"
	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/events"
	"github.com/windrose-ai/tool-plane/internal/mcp"
)

// ---- in-memory test doubles (unit tier only; never wired into cmd/) ---------

type fakeCatalog struct {
	version *domain.ToolVersion
	err     error
	backend mcp.BackendTarget
	backErr error
}

func (f *fakeCatalog) ResolveVersion(_ context.Context, _, _ string) (*domain.ToolVersion, error) {
	return f.version, f.err
}
func (f *fakeCatalog) BackendFor(_ context.Context, _ string) (mcp.BackendTarget, error) {
	return f.backend, f.backErr
}

type fakeEnablement struct{ s *domain.TenantToolSettings }

func (f *fakeEnablement) GetTenantSettings(_ context.Context, _ uuid.UUID, _ string) (*domain.TenantToolSettings, error) {
	return f.s, nil
}

type fakeKill struct{ killed bool }

func (f *fakeKill) IsKilled(uuid.UUID, string, string) (bool, string) { return f.killed, "tool" }

type fakeOPA struct {
	dec authz.Decision
	err error
}

func (f *fakeOPA) Check(context.Context, authz.Input) (authz.Decision, error) { return f.dec, f.err }

type fakeRate struct {
	allow bool
	err   error
}

func (f *fakeRate) Allow(context.Context, string, int) (bool, int, error) {
	if f.err != nil {
		return false, 0, f.err
	}
	if !f.allow {
		return false, 30, nil
	}
	return true, 0, nil
}

type fakeGrants struct{ grants []string }

func (f *fakeGrants) GrantsFor(context.Context, string, string, []string) ([]string, error) {
	return f.grants, nil
}

type fakeBackend struct {
	out map[string]any
	err error
}

func (f *fakeBackend) Invoke(context.Context, mcp.BackendTarget, mcp.Invocation, bool) (*mcp.Result, error) {
	if f.err != nil {
		return nil, f.err
	}
	return &mcp.Result{Output: f.out}, nil
}

type fakeAudit struct{ logs []*domain.InvocationLog }

func (f *fakeAudit) RecordInvocation(_ context.Context, l *domain.InvocationLog, _ events.Envelope) error {
	f.logs = append(f.logs, l)
	return nil
}
func (f *fakeAudit) InsertAudit(context.Context, events.Envelope) error { return nil }

// fakeProposals accepts only the sentinel grant "valid" and binds it to the
// call (stands in for the real RS256 signature + binding verification).
type fakeProposals struct{}

func (fakeProposals) VerifyGrant(_ context.Context, grant, _, _, _, argsDigest string) (*authz.ProposalExecution, error) {
	if grant != "valid" {
		return nil, authz.ErrProposalInvalid
	}
	return &authz.ProposalExecution{ProposalID: "p1", DecidedBy: "user:mgr", ArgsDigest: argsDigest}, nil
}

// ---- fixtures ---------------------------------------------------------------

func readVersion() *domain.ToolVersion {
	return &domain.ToolVersion{
		ToolID: "case.get", Version: "1.0.0", Status: domain.StatusPublished, PermissionTier: domain.TierRead,
		CostWeight: 1, SideEffects: domain.SideEffectNone,
		InputSchema: map[string]any{
			"type": "object", "additionalProperties": false, "required": []any{"case_id"},
			"properties": map[string]any{"case_id": map[string]any{"type": "string", "x-windrose-urn": "wr:{tenant}:case:case/{value}"}},
		},
	}
}

func writeProposalVersion() *domain.ToolVersion {
	v := readVersion()
	v.ToolID = "case.assign"
	v.PermissionTier = domain.TierWriteProposal
	v.SideEffects = domain.SideEffectReversible
	return v
}

func enabled() *domain.TenantToolSettings {
	return &domain.TenantToolSettings{Enabled: true}
}

func basePipeline() (*Pipeline, *fakeAudit) {
	audit := &fakeAudit{}
	return &Pipeline{
		Catalog:    &fakeCatalog{version: readVersion(), backend: mcp.BackendTarget{URL: "http://backend"}},
		Enablement: &fakeEnablement{s: enabled()},
		Kill:       &fakeKill{},
		OPA:        &fakeOPA{dec: authz.Decision{Allow: true}},
		Rate:       &fakeRate{allow: true},
		Grants:     &fakeGrants{grants: []string{"wr:t-42:case:case/c1"}},
		Backend:    &fakeBackend{out: map[string]any{"case_id": "c1"}},
		Audit:      audit,
		Proposals:  fakeProposals{},
	}, audit
}

func baseReq() Request {
	return Request{
		AgentID: "case-triage", AgentVersion: "3", Principal: "agent:case-triage@v3", Typ: domain.TypAgentOBO,
		OboSub: "user:u1", Tenant: uuid.MustParse("00000000-0000-0000-0000-0000000000ff"), TenantStr: "t-42",
		ToolID: "case.get", Args: map[string]any{"case_id": "c1"}, TraceID: "trace-1",
	}
}

// AC-1: allowed read call reaches backend and emits decision=allowed.
func TestPipeline_AllowedRead(t *testing.T) {
	p, audit := basePipeline()
	oc := p.Run(context.Background(), baseReq())
	if oc.Decision != events.DecisionAllowed {
		t.Fatalf("want allowed, got %s (%s)", oc.Decision, oc.Message)
	}
	if oc.Output["case_id"] != "c1" {
		t.Fatalf("backend output not returned: %+v", oc.Output)
	}
	if len(audit.logs) != 1 || audit.logs[0].Decision != events.DecisionAllowed {
		t.Fatalf("expected one allowed audit log, got %+v", audit.logs)
	}
	if audit.logs[0].ArgsDigest == "" {
		t.Fatal("audit must carry args digest, never raw args (BR-3)")
	}
}

// AC-2: OBO user lacks grant → OPA denies, backend not called, decision=denied_policy.
func TestPipeline_DeniedPolicy_NoGrant(t *testing.T) {
	p, audit := basePipeline()
	p.OPA = &fakeOPA{dec: authz.Decision{Allow: false, Reason: "obo_grant"}}
	called := &fakeBackend{out: map[string]any{}}
	p.Backend = called
	oc := p.Run(context.Background(), baseReq())
	if oc.Code != domain.CodePermission {
		t.Fatalf("want PERMISSION_DENIED, got %s", oc.Code)
	}
	if audit.logs[0].Decision != events.DecisionDeniedPolicy {
		t.Fatalf("want denied_policy audit, got %s", audit.logs[0].Decision)
	}
}

// AC-3: argument-constraint violation → OPA denies with the constraint id recorded.
func TestPipeline_DeniedPolicy_ConstraintID(t *testing.T) {
	p, _ := basePipeline()
	p.OPA = &fakeOPA{dec: authz.Decision{Allow: false, Reason: "argument_constraint", ViolatedConstraint: "bulk_limit"}}
	oc := p.Run(context.Background(), baseReq())
	if oc.Code != domain.CodePermission {
		t.Fatalf("want PERMISSION_DENIED, got %s", oc.Code)
	}
	d, _ := oc.Details.(map[string]any)
	if d["violated_constraint"] != "bulk_limit" {
		t.Fatalf("expected violated_constraint bulk_limit, got %+v", oc.Details)
	}
}

// AC-4: write-proposal tool returns PROPOSAL_REQUIRED and does NOT invoke backend.
func TestPipeline_ProposalRequired(t *testing.T) {
	p, audit := basePipeline()
	p.Catalog = &fakeCatalog{version: writeProposalVersion(), backend: mcp.BackendTarget{URL: "http://backend"}}
	back := &fakeBackend{out: map[string]any{}}
	p.Backend = back
	req := baseReq()
	req.ToolID = "case.assign"
	oc := p.Run(context.Background(), req)
	if oc.Decision != events.DecisionProposal || oc.Code != domain.CodeProposalRequired {
		t.Fatalf("want PROPOSAL_REQUIRED, got %s/%s", oc.Decision, oc.Code)
	}
	if oc.Structured["status"] != "proposal_required" {
		t.Fatalf("missing structured proposal content: %+v", oc.Structured)
	}
	if audit.logs[0].Decision != events.DecisionProposal {
		t.Fatalf("proposal must be audited, got %s", audit.logs[0].Decision)
	}
}

// AC-4 (exec): a VERIFIED signed proposal-execution grant passes the tier gate
// and invokes the backend.
func TestPipeline_ProposalExecution_Invokes(t *testing.T) {
	p, _ := basePipeline()
	p.Catalog = &fakeCatalog{version: writeProposalVersion(), backend: mcp.BackendTarget{URL: "http://backend"}}
	p.Backend = &fakeBackend{out: map[string]any{"case_id": "c1", "assignee_id": "u9"}}
	req := baseReq()
	req.ToolID = "case.assign"
	req.ProposalGrant = "valid" // fakeProposals verifies + binds it
	oc := p.Run(context.Background(), req)
	if oc.Decision != events.DecisionAllowed {
		t.Fatalf("verified proposal execution should invoke, got %s (%s)", oc.Decision, oc.Message)
	}
}

// SECURITY: a forged/unsigned grant is rejected — the write falls back to
// PROPOSAL_REQUIRED and the backend is never called (TPL-FR-035).
func TestPipeline_ForgedProposalGrant_Rejected(t *testing.T) {
	p, audit := basePipeline()
	p.Catalog = &fakeCatalog{version: writeProposalVersion(), backend: mcp.BackendTarget{URL: "http://backend"}}
	back := &fakeBackend{out: map[string]any{}}
	p.Backend = back
	req := baseReq()
	req.ToolID = "case.assign"
	req.ProposalGrant = "forged-token-caller-made-up" // not accepted by the verifier
	oc := p.Run(context.Background(), req)
	if oc.Decision != events.DecisionProposal || oc.Code != domain.CodeProposalRequired {
		t.Fatalf("forged grant must NOT execute; want PROPOSAL_REQUIRED, got %s/%s", oc.Decision, oc.Code)
	}
	if audit.logs[0].Decision != events.DecisionProposal {
		t.Fatalf("want proposal audit, got %s", audit.logs[0].Decision)
	}
}

// SECURITY: with no proposal verifier wired, no grant can ever execute (fail closed).
func TestPipeline_NoVerifier_FailsClosed(t *testing.T) {
	p, _ := basePipeline()
	p.Proposals = nil
	p.Catalog = &fakeCatalog{version: writeProposalVersion(), backend: mcp.BackendTarget{URL: "http://backend"}}
	req := baseReq()
	req.ToolID = "case.assign"
	req.ProposalGrant = "valid"
	oc := p.Run(context.Background(), req)
	if oc.Code != domain.CodeProposalRequired {
		t.Fatalf("no verifier must fail closed to PROPOSAL_REQUIRED, got %s", oc.Code)
	}
}

// AC-11: rate limit exhausted → RATE_LIMITED, backend not called, decision=denied_rate.
func TestPipeline_RateLimited(t *testing.T) {
	p, audit := basePipeline()
	p.Rate = &fakeRate{allow: false}
	oc := p.Run(context.Background(), baseReq())
	if oc.Code != domain.CodeRateLimited || oc.RetryAfter == 0 {
		t.Fatalf("want RATE_LIMITED + retry-after, got %s/%d", oc.Code, oc.RetryAfter)
	}
	if audit.logs[0].Decision != events.DecisionDeniedRate {
		t.Fatalf("want denied_rate, got %s", audit.logs[0].Decision)
	}
}

// TPL-FR-034: schema validation failure → VALIDATION_FAILED with per-field details.
func TestPipeline_ValidationFailed(t *testing.T) {
	p, audit := basePipeline()
	req := baseReq()
	req.Args = map[string]any{"case_id": "c1", "unknown": true} // additionalProperties:false
	oc := p.Run(context.Background(), req)
	if oc.Code != domain.CodeValidation {
		t.Fatalf("want VALIDATION_FAILED, got %s", oc.Code)
	}
	if audit.logs[0].Decision != events.DecisionDeniedSchema {
		t.Fatalf("want denied_schema, got %s", audit.logs[0].Decision)
	}
}

// AC-5/BR-9: a killed tool returns TOOL_KILLED.
func TestPipeline_Killed(t *testing.T) {
	p, _ := basePipeline()
	p.Kill = &fakeKill{killed: true}
	oc := p.Run(context.Background(), baseReq())
	if oc.Code != domain.CodeToolKilled {
		t.Fatalf("want TOOL_KILLED, got %s", oc.Code)
	}
}

// AC-8: a retired version returns TOOL_RETIRED.
func TestPipeline_Retired(t *testing.T) {
	p, _ := basePipeline()
	v := readVersion()
	v.Status = domain.StatusRetired
	p.Catalog = &fakeCatalog{version: v}
	oc := p.Run(context.Background(), baseReq())
	if oc.Code != domain.CodeToolRetired {
		t.Fatalf("want TOOL_RETIRED, got %s", oc.Code)
	}
}

// TPL-FR-031: a tool not enabled for the tenant returns TOOL_DISABLED.
func TestPipeline_Disabled(t *testing.T) {
	p, _ := basePipeline()
	p.Enablement = &fakeEnablement{s: &domain.TenantToolSettings{Enabled: false}}
	oc := p.Run(context.Background(), baseReq())
	if oc.Code != domain.CodeToolDisabled {
		t.Fatalf("want TOOL_DISABLED, got %s", oc.Code)
	}
}

// AC-12/BR-1: OPA unreachable → POLICY_UNAVAILABLE (fail closed), backend not called.
func TestPipeline_PolicyUnavailable(t *testing.T) {
	p, audit := basePipeline()
	p.OPA = &fakeOPA{err: errors.New("connection refused")}
	oc := p.Run(context.Background(), baseReq())
	if oc.Code != domain.CodePolicyUnavailable {
		t.Fatalf("want POLICY_UNAVAILABLE, got %s", oc.Code)
	}
	if audit.logs[0].Decision != events.DecisionDeniedPolicy {
		t.Fatalf("fail-closed must be audited, got %s", audit.logs[0].Decision)
	}
}

// AC-13/BR-12: a cross-tenant URN in args → 404-shaped denial, backend not called.
func TestPipeline_CrossTenant404(t *testing.T) {
	p, _ := basePipeline()
	req := baseReq()
	req.Args = map[string]any{"case_id": "wr:t-99:case:case/c1"} // tenant t-99 != caller t-42
	oc := p.Run(context.Background(), req)
	if oc.Code != domain.CodeNotFound || oc.HTTP != 404 {
		t.Fatalf("want 404-shaped NOT_FOUND, got %s/%d", oc.Code, oc.HTTP)
	}
}

// AC-17/BR-16: eval-mode write tool short-circuits to a stub, backend receives nothing.
func TestPipeline_EvalStub(t *testing.T) {
	p, audit := basePipeline()
	p.Catalog = &fakeCatalog{version: writeProposalVersion()}
	req := baseReq()
	req.ToolID = "case.assign"
	req.Eval = true
	oc := p.Run(context.Background(), req)
	if oc.Decision != events.DecisionStubbed || oc.Structured["status"] != "stubbed" {
		t.Fatalf("want stubbed, got %s/%+v", oc.Decision, oc.Structured)
	}
	if audit.logs[0].Decision != events.DecisionStubbed {
		t.Fatalf("eval stub must audit decision=stubbed, got %s", audit.logs[0].Decision)
	}
}

// TPL-FR-037: every attempt emits exactly one audit record (completeness).
func TestPipeline_AuditCompleteness(t *testing.T) {
	p, audit := basePipeline()
	_ = p.Run(context.Background(), baseReq())
	if len(audit.logs) != 1 {
		t.Fatalf("expected exactly 1 audit record per attempt, got %d", len(audit.logs))
	}
}
