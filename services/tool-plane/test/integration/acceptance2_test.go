package integration

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/tool-plane/internal/api"
	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/enforce"
	"github.com/windrose-ai/tool-plane/internal/events"
)

// autonomousToken mints an agent_autonomous token (no OBO user) so the OBO-grant
// gate passes trivially — used where we exercise health/backend behaviour, not grants.
func (h *harness) autonomousToken(tenant, agentID, ver string, scopes []string) string {
	return h.mintToken(authjwt.Claims{
		Sub: "agent:" + agentID, TenantID: tenant, Typ: authjwt.TypAgentAutonomous,
		AgentID: agentID, AgentVersion: ver, Scopes: scopes,
	})
}

// slowBackend is a REAL HTTP facade that sleeps longer than the SLA timeout.
func slowBackend(d time.Duration) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(d)
		_, _ = w.Write([]byte(`{"output":{}}`))
	}))
}

// AC-8: a deprecated (within-window) version still serves, with a _meta.deprecation warning.
func TestAC8_DeprecationWarning(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "chart.create_draft", "Create a draft chart. Use when you need a chart draft to iterate on.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.registerBackend(t, "chart-service", be.URL)
	h.enableTool(t, tenant, "chart.create_draft", nil, "", nil)
	// Deprecate with a 90-day window (still serving).
	ends := time.Now().Add(90 * 24 * time.Hour)
	if err := h.store.SetVersionStatus(ctx, "chart.create_draft", "1.0.0", domain.StatusDeprecated, &ends, nil); err != nil {
		t.Fatalf("deprecate: %v", err)
	}
	token := h.autonomousToken(tenant.String(), "charter", "1", []string{"chart.create_draft"})
	// Agents pin the version they resolved at session start (BR-4); a deprecated
	// version is served when pinned.
	res, rerr := h.callMCP(t, token, "chart.create_draft", map[string]any{"case_id": "c1"}, map[string]any{"version": "1.0.0"})
	if rerr != nil || resultIsError(res) {
		t.Fatalf("deprecated tool should still serve, got %+v / %+v", res, rerr)
	}
	meta, _ := res["_meta"].(map[string]any)
	if meta == nil || meta["deprecation"] == nil {
		t.Fatalf("expected _meta.deprecation warning on a deprecated tool result, got %+v", res)
	}
}

// AC-9: a BYO submission is not callable until approved AND tenant-enabled; and
// once enabled for tenant A it stays invisible to tenant B (isolation).
func TestAC9_BYOCallabilityLifecycle(t *testing.T) {
	h := mustHarness(t)
	tenantA := newTenant()
	tenantB := newTenant()
	be := echoBackend()
	defer be.Close()

	// A third-party integrator submits a BYO Jira tool (pending_approval). The
	// BYO route is guarded by tool.byo.create: the agent carries the action in
	// scope and its OBO user holds it tenant-scoped in the rbac projection.
	h.seedTenantAction(context.Background(), tenantA.String(), "user:int", "tool.byo.create")
	subToken := h.agentToken(tenantA.String(), "integrator", "1", "user:int", []string{"jira.create_issue", "tool.byo.create"})
	code, out := h.registryDo(t, subToken, http.MethodPost, "/api/v1/byo", map[string]any{
		"manifest":                map[string]any{"tool_id": "jira.create_issue"},
		"endpoint_url":            be.URL,
		"requested_tier":          "read",
		"data_egress_description": "sends issue summary to Jira",
	})
	if code != http.StatusCreated {
		t.Fatalf("byo submit status %d: %+v", code, out)
	}
	byoID := out["data"].(map[string]any)["id"].(string)

	// Before approval + enablement the tool is not in the catalog → not callable.
	tokenA := h.agentToken(tenantA.String(), "agent", "1", "user:int", []string{"jira.create_issue"})
	res, _ := h.callMCP(t, tokenA, "jira.create_issue", map[string]any{"case_id": "c1"}, nil)
	if !resultIsError(res) {
		t.Fatalf("pending BYO tool must not be callable, got %+v", res)
	}
	if names := h.mcpList(t, tokenA); contains(names, "jira.create_issue") {
		t.Fatalf("pending BYO tool must not be listed")
	}

	// Operator approves; onboarding registers+publishes the tool and its backend.
	opToken := h.operatorToken(uuid.NewString())
	if code, _ := h.registryDo(t, opToken, http.MethodPost, "/api/v1/byo/"+byoID+"/approve", map[string]any{"message": "ok"}); code != http.StatusOK {
		t.Fatalf("approve status %d", code)
	}
	h.publishTool(t, "jira.create_issue", "Create a Jira issue. Use when a case must be escalated to Jira.", domain.TierRead, domain.SideEffectReversible, caseGetSchema())
	h.registerBackend(t, "jira-service", be.URL)
	// Tenant A's admin enables it; tenant B does not.
	h.enableTool(t, tenantA, "jira.create_issue", nil, "", nil)
	h.seedGrant(context.Background(), tenantA.String(), "user:int", "wr:"+tenantA.String()+":case:case/c1")

	res, _ = h.callMCP(t, tokenA, "jira.create_issue", map[string]any{"case_id": "c1"}, nil)
	if resultIsError(res) {
		t.Fatalf("approved+enabled BYO tool must be callable for tenant A, got %+v", res)
	}
	tokenB := h.agentToken(tenantB.String(), "agent", "1", "user:b", []string{"jira.create_issue"})
	res, _ = h.callMCP(t, tokenB, "jira.create_issue", map[string]any{"case_id": "c1"}, nil)
	if !resultIsError(res) {
		t.Fatalf("BYO tool must remain not-callable for tenant B (isolation), got %+v", res)
	}
}

// AC-10: backend exceeding 3×p95 times out and records a timeout health error;
// 10 consecutive SLA-breach evaluations with auto-quarantine move the version to
// quarantined (served as TOOL_KILLED) and fire tool.sla_breached.
func TestAC10_SLABreachAutoQuarantine(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	slow := slowBackend(500 * time.Millisecond)
	defer slow.Close()
	// Dedicated tool id (this test quarantines it — must not collide with others).
	// declared p95 = 50ms → timeout budget = max(3×50, 250ms) = 250ms < 500ms sleep.
	h.publishToolSLA(t, "sla.probe", "Probe tool for SLA testing. Use when validating SLA breach handling.", domain.TierRead, domain.SideEffectNone, caseGetSchema(), domain.DeclaredSLA{P95MS: 50, ErrorRatePct: 0.5})
	h.registerBackend(t, "sla-service", slow.URL)
	h.enableTool(t, tenant, "sla.probe", nil, "", nil)

	token := h.autonomousToken(tenant.String(), "prober", "1", []string{"sla.probe"})
	res, _ := h.callMCP(t, token, "sla.probe", map[string]any{"case_id": "c1"}, nil)
	if resultCode(res) != domain.CodeToolBackendTimeout {
		t.Fatalf("want TOOL_BACKEND_TIMEOUT, got %+v", res)
	}
	// Health recorded a timeout.
	snap, err := h.health.Snapshot(ctx, "sla.probe", "1.0.0")
	if err != nil || snap.ErrorsByKind["timeout"] == 0 {
		t.Fatalf("expected a timeout health error, got %+v err=%v", snap, err)
	}

	// Drive the SLA detector to threshold with auto-quarantine.
	q := &enforce.Quarantiner{Health: h.health, Store: h.store, Threshold: 1}
	breached, quarantined, err := q.Evaluate(ctx, "sla.probe", "1.0.0", domain.DeclaredSLA{P95MS: 50, ErrorRatePct: 0.5}, true)
	if err != nil || !breached || !quarantined {
		t.Fatalf("expected SLA breach + quarantine, got breached=%v quarantined=%v err=%v", breached, quarantined, err)
	}
	// tool.sla_breached emitted.
	evs, _ := h.store.OutboxByType(ctx, domain.PlatformTenant, events.EvToolSLABreached)
	if len(evs) == 0 {
		t.Fatal("expected a tool.sla_breached event")
	}
	// Quarantined version is served as TOOL_KILLED.
	res, _ = h.callMCP(t, token, "sla.probe", map[string]any{"case_id": "c1"}, map[string]any{"version": "1.0.0"})
	if resultCode(res) != domain.CodeToolKilled {
		t.Fatalf("quarantined version must serve TOOL_KILLED, got %+v", res)
	}
}

// AC-16: the gateway holds no per-session state — a second, independently
// constructed gateway (fresh kill registry + rate limiter over the same infra)
// serves the same client session with no affinity requirement (BR-17).
func TestAC16_Statelessness(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.get", "Fetch case detail. Use when you need a case's assignee.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.get", nil, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.get"})

	// gw1 is the harness gateway. gw2 is a second, independently-built gateway
	// (its own KillRegistry) sharing the same store/Redis/OPA — a different replica.
	kill2 := enforce.NewKillRegistry(h.rc)
	_ = kill2.SyncFromStore(ctx, h.store)
	pipe2 := &enforce.Pipeline{
		Catalog: api.NewCatalogResolver(h.store), Enablement: h.store, Kill: kill2, OPA: h.opa,
		Rate: enforce.NewRateLimiter(h.rc), Grants: enforce.NewRedisGrantLoader(h.rc),
		Backend: h.pipeline.Backend, Audit: h.store, Proposals: h.pipeline.Proposals, Health: h.health,
	}
	gw2 := &api.GatewayServer{Pipeline: pipe2, Store: h.store, Verifier: h.verifier, Kill: kill2}

	// Same session hits gw1 then gw2; both succeed with no session affinity.
	res1, _ := h.callMCP(t, token, "case.get", map[string]any{"case_id": "c1"}, nil)
	if resultIsError(res1) {
		t.Fatalf("gw1 call failed: %+v", res1)
	}
	res2 := callGateway(t, gw2, token, "case.get", map[string]any{"case_id": "c1"}, nil)
	if resultIsError(res2) {
		t.Fatalf("gw2 (different replica) call failed: %+v", res2)
	}
}
