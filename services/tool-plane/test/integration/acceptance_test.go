package integration

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/tool-plane/internal/api"
	"github.com/windrose-ai/tool-plane/internal/domain"
)

// ---- HTTP helpers ------------------------------------------------------------

// callMCP posts a tools/call to the gateway /mcp and returns the JSON-RPC result.
func (h *harness) callMCP(t *testing.T, token, name string, args, meta map[string]any) (map[string]any, *struct {
	Code    int
	Message string
}) {
	t.Helper()
	params := map[string]any{"name": name, "arguments": args}
	if meta != nil {
		params["_meta"] = meta
	}
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	h.gateway.Router().ServeHTTP(rec, req)
	var resp struct {
		Result map[string]any `json:"result"`
		Error  *struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode mcp response: %v (%s)", err, rec.Body.String())
	}
	var e *struct {
		Code    int
		Message string
	}
	if resp.Error != nil {
		e = &struct {
			Code    int
			Message string
		}{resp.Error.Code, resp.Error.Message}
	}
	return resp.Result, e
}

// callGateway posts a tools/call to an arbitrary GatewayServer (statelessness
// test uses a second, independently-constructed gateway replica).
func callGateway(t *testing.T, gw *api.GatewayServer, token, name string, args, meta map[string]any) map[string]any {
	t.Helper()
	params := map[string]any{"name": name, "arguments": args}
	if meta != nil {
		params["_meta"] = meta
	}
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	gw.Router().ServeHTTP(rec, req)
	var resp struct {
		Result map[string]any `json:"result"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)
	return resp.Result
}

// mcpList posts a tools/list (toolset derived from the token) and returns names.
func (h *harness) mcpList(t *testing.T, token string) []string {
	return h.mcpListRaw(t, token, nil)
}

// mcpListRaw posts a tools/list optionally including a client _meta.toolset (used
// to prove the client body cannot widen the token-authoritative toolset).
func (h *harness) mcpListRaw(t *testing.T, token string, metaToolset []string) []string {
	t.Helper()
	params := map[string]any{}
	if metaToolset != nil {
		params["_meta"] = map[string]any{"toolset": metaToolset}
	}
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": params})
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	h.gateway.Router().ServeHTTP(rec, req)
	var resp struct {
		Result struct {
			Tools []struct {
				Name string `json:"name"`
			} `json:"tools"`
		} `json:"result"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)
	names := make([]string, 0, len(resp.Result.Tools))
	for _, tl := range resp.Result.Tools {
		names = append(names, tl.Name)
	}
	return names
}

// registryDo calls a registry HTTP endpoint.
func (h *harness) registryDo(t *testing.T, token, method, path string, body any) (int, map[string]any) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rdr = bytes.NewReader(b)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	h.registry.Router().ServeHTTP(rec, req)
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec.Code, out
}

// echoBackend is a REAL HTTP MCP facade: it echoes args back as output so the
// gateway's real HTTP client has a genuine peer (not a hardcoded fake).
func echoBackend() *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var in struct {
			Args map[string]any `json:"args"`
		}
		_ = json.NewDecoder(r.Body).Decode(&in)
		out := map[string]any{"echo": in.Args}
		_ = json.NewEncoder(w).Encode(map[string]any{"output": out})
	}))
}

func resultIsError(res map[string]any) bool {
	b, _ := res["isError"].(bool)
	return b
}

func resultCode(res map[string]any) string {
	sc, _ := res["structuredContent"].(map[string]any)
	c, _ := sc["code"].(string)
	return c
}

// ---- AC-1: allowed read reaches backend + emits ai.tool_invoked{allowed} -----

func TestAC1_AllowedRead(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()

	h.publishTool(t, "case.get", "Fetch case detail including current assignee. Use when you need a case's data.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.get", nil, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")

	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.get"})
	res, rerr := h.callMCP(t, token, "case.get", map[string]any{"case_id": "c1"}, nil)
	if rerr != nil {
		t.Fatalf("unexpected rpc error: %+v", rerr)
	}
	if resultIsError(res) {
		t.Fatalf("expected success, got error result: %+v", res)
	}
	// ai.tool_invoked.v1 with decision=allowed persisted (outbox).
	waitAudit(t, h, tenant, "allowed")
}

// ---- AC-2 + AC-3: real OPA deny (missing grant / argument constraint) --------

func TestAC2_MissingGrantDenied(t *testing.T) {
	h := mustHarness(t)
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.get", "Fetch case detail. Use when you need a case's current assignee.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.get", nil, "", nil)
	// NO grant seeded for user:u2 → OPA denies obo intersection.
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u2", []string{"case.get"})
	res, _ := h.callMCP(t, token, "case.get", map[string]any{"case_id": "c1"}, nil)
	if resultCode(res) != domain.CodePermission {
		t.Fatalf("want PERMISSION_DENIED (no grant), got %+v", res)
	}
	waitAudit(t, h, tenant, "denied_policy")
}

func TestAC3_ArgumentConstraintDenied(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user or group. Use when a case needs an owner.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())
	h.registerBackend(t, "case-service", be.URL)
	// tenant constrains bulk_limit ≤ 50.
	h.enableTool(t, tenant, "case.assign", map[string]any{"bulk_limit": map[string]any{"max": float64(50)}}, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.assign"})
	// bulk_limit 100 > 50 → real OPA denies before schema/backend, records constraint id.
	res, _ := h.callMCP(t, token, "case.assign", map[string]any{"case_id": "c1", "assignee_id": "u9", "bulk_limit": float64(100)}, nil)
	if resultCode(res) != domain.CodePermission {
		t.Fatalf("want PERMISSION_DENIED (constraint), got %+v", res)
	}
	sc, _ := res["structuredContent"].(map[string]any)
	details, _ := sc["details"].(map[string]any)
	if details["violated_constraint"] != "bulk_limit" {
		t.Fatalf("expected violated_constraint bulk_limit, got %+v", sc)
	}
}

// ---- AC-4: PROPOSAL_REQUIRED then proposal execution invokes -----------------

func TestAC4_ProposalRequiredThenExecute(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user or group. Use when a case needs an owner.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.assign", nil, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.assign"})
	args := map[string]any{"case_id": "c1", "assignee_id": "u9"}
	res, _ := h.callMCP(t, token, "case.assign", args, nil)
	if resultIsError(res) {
		t.Fatalf("proposal path must not be an error result: %+v", res)
	}
	sc, _ := res["structuredContent"].(map[string]any)
	if sc["status"] != "proposal_required" {
		t.Fatalf("want proposal_required, got %+v", sc)
	}
	// Re-call with a properly SIGNED proposal-execution grant (agent-runtime key).
	digest := domain.ArgsDigest(args)
	grant := h.signGrant(tenant.String(), "case.assign", domain.TierWriteProposal, digest, time.Now().Add(2*time.Minute), "")
	res2, _ := h.callMCP(t, token, "case.assign", args, map[string]any{"proposal_grant": grant})
	if resultIsError(res2) {
		t.Fatalf("verified proposal execution should invoke backend, got %+v", res2)
	}
}

// SECURITY: forged/unsigned grant must NOT execute — falls back to PROPOSAL_REQUIRED.
func TestSEC_ForgedGrantRejected(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user or group. Use when a case needs an owner.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.assign", nil, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.assign"})
	args := map[string]any{"case_id": "c1", "assignee_id": "u9"}
	// The demonstrated exploit: an unsigned/forged grant string the caller made up.
	forged := "eyJhbGciOiJub25lIn0.eyJwcm9wb3NhbF9pZCI6ImZha2UifQ."
	res, _ := h.callMCP(t, token, "case.assign", args, map[string]any{"proposal_grant": forged})
	sc, _ := res["structuredContent"].(map[string]any)
	if sc["status"] != "proposal_required" {
		t.Fatalf("forged grant must be rejected → PROPOSAL_REQUIRED, got %+v", res)
	}
}

// SECURITY: expired, args-mismatched, and wrong-issuer signed grants are rejected.
func TestSEC_InvalidSignedGrantsRejected(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user or group. Use when a case needs an owner.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.assign", nil, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.assign"})
	args := map[string]any{"case_id": "c1", "assignee_id": "u9"}
	digest := domain.ArgsDigest(args)

	cases := map[string]string{
		"expired":       h.signGrant(tenant.String(), "case.assign", domain.TierWriteProposal, digest, time.Now().Add(-time.Minute), ""),
		"args_mismatch": h.signGrant(tenant.String(), "case.assign", domain.TierWriteProposal, domain.ArgsDigest(map[string]any{"case_id": "c1", "assignee_id": "u-EVIL"}), time.Now().Add(2*time.Minute), ""),
		"wrong_tool":    h.signGrant(tenant.String(), "pipeline.launch_run", domain.TierWriteProposal, digest, time.Now().Add(2*time.Minute), ""),
		"wrong_tenant":  h.signGrant(newTenant().String(), "case.assign", domain.TierWriteProposal, digest, time.Now().Add(2*time.Minute), ""),
		"wrong_issuer":  h.signGrant(tenant.String(), "case.assign", domain.TierWriteProposal, digest, time.Now().Add(2*time.Minute), "evil-issuer"),
	}
	for name, grant := range cases {
		res, _ := h.callMCP(t, token, "case.assign", args, map[string]any{"proposal_grant": grant})
		sc, _ := res["structuredContent"].(map[string]any)
		if sc["status"] != "proposal_required" {
			t.Fatalf("%s grant must be rejected → PROPOSAL_REQUIRED, got %+v", name, res)
		}
	}
}

// ---- AC-5: multi-replica kill switch (Redis pub/sub) -------------------------

func TestAC5_KillSwitch(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "pipeline.launch_run", "Launch a pipeline run. Use when a pipeline must execute.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.registerBackend(t, "pipeline-service", be.URL)
	h.enableTool(t, tenant, "pipeline.launch_run", nil, "", nil)
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")

	// A SECOND kill registry simulates another gateway replica sharing Redis.
	replica := enforceNewKill(h)
	go replica.Watch(ctx)

	opToken := h.operatorToken(tenant.String())
	code, _ := h.registryDo(t, opToken, http.MethodPost, "/api/v1/kill-switches", map[string]any{
		"scope": "tool_version", "tool_id": "pipeline.launch_run", "version": "1.0.0", "reason": "INC-2231",
	})
	if code != http.StatusCreated {
		t.Fatalf("kill create status %d", code)
	}
	// The replica must observe the kill (pub/sub propagation).
	waitKilled(t, replica, tenant, "pipeline.launch_run", "1.0.0", true)

	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"pipeline.launch_run"})
	res, _ := h.callMCP(t, token, "pipeline.launch_run", map[string]any{"case_id": "c1"}, nil)
	if resultCode(res) != domain.CodeToolKilled {
		t.Fatalf("want TOOL_KILLED, got %+v", res)
	}
}

// TestKillSwitchListAndLift covers the admin list surface added for the
// Tier-1 kill-switch UI (GET /kill-switches): a created kill shows up in the
// active list, and lifting it (DELETE) removes it from that list again — the
// real round trip the ui-web admin page and bff-graphql resolver depend on.
func TestKillSwitchListAndLift(t *testing.T) {
	h := mustHarness(t)
	tenant := newTenant()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user or group. Use when a case needs an owner or the current owner is wrong.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())

	opToken := h.operatorToken(tenant.String())
	code, created := h.registryDo(t, opToken, http.MethodPost, "/api/v1/kill-switches", map[string]any{
		"scope": "tool", "tool_id": "case.assign", "reason": "TestKillSwitchListAndLift",
	})
	if code != http.StatusCreated {
		t.Fatalf("kill create status %d: %+v", code, created)
	}
	killID := created["data"].(map[string]any)["id"].(string)

	code, listed := h.registryDo(t, opToken, http.MethodGet, "/api/v1/kill-switches", nil)
	if code != http.StatusOK {
		t.Fatalf("kill list status %d: %+v", code, listed)
	}
	rows, _ := listed["data"].([]any)
	found := false
	for _, row := range rows {
		m, _ := row.(map[string]any)
		if m != nil && m["id"] == killID {
			found = true
			if m["reason"] != "TestKillSwitchListAndLift" {
				t.Fatalf("listed kill missing/wrong reason: %+v", m)
			}
			// Regression: ActiveKills used to omit `active` from its SELECT/Scan,
			// so every listed row serialized the Go zero-value (false) for a row
			// the WHERE clause already guarantees is true.
			if m["active"] != true {
				t.Fatalf("listed kill has active=%v, want true: %+v", m["active"], m)
			}
		}
	}
	if !found {
		t.Fatalf("created kill %s not present in GET /kill-switches: %+v", killID, rows)
	}

	code, _ = h.registryDo(t, opToken, http.MethodDelete, "/api/v1/kill-switches/"+killID, nil)
	if code != http.StatusOK {
		t.Fatalf("kill delete status %d", code)
	}
	code, listedAfter := h.registryDo(t, opToken, http.MethodGet, "/api/v1/kill-switches", nil)
	if code != http.StatusOK {
		t.Fatalf("kill list (after lift) status %d: %+v", code, listedAfter)
	}
	for _, row := range listedAfter["data"].([]any) {
		m, _ := row.(map[string]any)
		if m != nil && m["id"] == killID {
			t.Fatalf("lifted kill %s still present in GET /kill-switches: %+v", killID, m)
		}
	}
}

// ---- AC-6: real semantic discovery (Ollama + pgvector) -----------------------

func TestAC6_SemanticDiscovery(t *testing.T) {
	h := mustHarness(t)
	tenant := newTenant()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user or group. Use when a case needs an owner or the current owner is wrong.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())
	h.publishTool(t, "chart.render", "Render a chart from a dataset. Use when you need a visualization of tabular data.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.publishTool(t, "case.bulk_close", "Close many cases at once. Use when bulk-closing resolved cases.", domain.TierWriteProposal, domain.SideEffectReversible, caseGetSchema())
	// Enable case.assign + chart.render; leave case.bulk_close DISABLED for the tenant.
	h.enableTool(t, tenant, "case.assign", nil, "", nil)
	h.enableTool(t, tenant, "chart.render", nil, "", nil)

	// Discovery is guarded by tool.tool.read: the agent must carry the action in
	// scope (agent_obo scope gate) AND its OBO user must hold the tenant-scoped
	// action in the rbac projection — the real OPA windrose.authz_input decision.
	h.seedTenantAction(context.Background(), tenant.String(), "user:u1", "tool.tool.read")
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"tool.tool.read"})
	code, out := h.registryDo(t, token, http.MethodPost, "/api/v1/discovery/search", map[string]any{
		"query": "change who owns an anomaly case", "top_k": 5,
	})
	if code != http.StatusOK {
		t.Fatalf("discovery status %d: %+v", code, out)
	}
	data, _ := out["data"].([]any)
	if len(data) == 0 {
		t.Fatal("expected discovery results")
	}
	top, _ := data[0].(map[string]any)
	if top["tool_id"] != "case.assign" {
		t.Fatalf("expected case.assign ranked first by nomic-embed-text similarity, got %+v", topIDs(data))
	}
	// Disabled tool must never appear (caller-scoping).
	for _, d := range data {
		m, _ := d.(map[string]any)
		if m["tool_id"] == "case.bulk_close" {
			t.Fatal("disabled tool leaked into discovery results")
		}
	}
}

func topIDs(data []any) []string {
	var ids []string
	for _, d := range data {
		m, _ := d.(map[string]any)
		ids = append(ids, m["tool_id"].(string))
	}
	return ids
}

// ---- AC-7: publish validates schema + populates embedding --------------------

func TestAC7_PublishGate(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	opToken := h.operatorToken(uuid.NewString())
	// Register tool + invalid-schema draft, then publish → VALIDATION_FAILED.
	h.registryDo(t, opToken, http.MethodPost, "/api/v1/tools", map[string]any{
		"tool_id": "semantic.query", "display_name": "q", "owner_service": "semantic-service", "owner_team": "t",
	})
	code, _ := h.registryDo(t, opToken, http.MethodPost, "/api/v1/tools/semantic.query/versions", map[string]any{
		"version": "1.0.0", "permission_tier": "read", "cost_weight": 1,
		"semantic_description": "Run a governed semantic query. Use when you need governed metrics.",
		"input_schema":         map[string]any{"type": "string"}, // invalid: not an object
	})
	if code != http.StatusCreated {
		t.Fatalf("add version status %d", code)
	}
	code, _ = h.registryDo(t, opToken, http.MethodPost, "/api/v1/tools/semantic.query/versions/1.0.0/publish", nil)
	if code != http.StatusUnprocessableEntity {
		t.Fatalf("want 422 VALIDATION_FAILED on invalid schema, got %d", code)
	}
	// Now a valid version publishes and gets an embedding row.
	h.registryDo(t, opToken, http.MethodPost, "/api/v1/tools/semantic.query/versions", map[string]any{
		"version": "1.1.0", "permission_tier": "read", "cost_weight": 1,
		"semantic_description": "Run a governed semantic query. Use when you need governed metrics.",
		"input_schema":         caseGetSchema(),
	})
	code, _ = h.registryDo(t, opToken, http.MethodPost, "/api/v1/tools/semantic.query/versions/1.1.0/publish", nil)
	if code != http.StatusOK {
		t.Fatalf("valid publish status %d", code)
	}
	v, err := h.store.GetVersion(ctx, "semantic.query", "1.1.0")
	if err != nil || v.EmbeddingModelVer == "" {
		t.Fatalf("published version must carry embedding model version, got %+v err=%v", v, err)
	}
}

// ---- AC-11: real Redis rate limit --------------------------------------------

func TestAC11_RateLimited(t *testing.T) {
	h := mustHarness(t)
	ctx := context.Background()
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.get", "Fetch case detail. Use when you need a case's assignee.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.get", nil, "", &domain.RateLimitOverride{PerMin: 1})
	h.seedGrant(ctx, tenant.String(), "user:u1", "wr:"+tenant.String()+":case:case/c1")
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.get"})
	// First call allowed, second exhausts the (tenant × tool) bucket.
	_, _ = h.callMCP(t, token, "case.get", map[string]any{"case_id": "c1"}, nil)
	res, _ := h.callMCP(t, token, "case.get", map[string]any{"case_id": "c1"}, nil)
	if resultCode(res) != domain.CodeRateLimited {
		t.Fatalf("want RATE_LIMITED on 2nd call, got %+v", res)
	}
}

// ---- AC-13: cross-tenant URN in args → 404-shaped ----------------------------

func TestAC13_CrossTenantDenied(t *testing.T) {
	h := mustHarness(t)
	tenant := newTenant()
	be := echoBackend()
	defer be.Close()
	h.publishTool(t, "case.get", "Fetch case detail. Use when you need a case's assignee.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.registerBackend(t, "case-service", be.URL)
	h.enableTool(t, tenant, "case.get", nil, "", nil)
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.get"})
	// case_id carries a URN of a DIFFERENT tenant.
	res, _ := h.callMCP(t, token, "case.get", map[string]any{"case_id": "wr:" + newTenant().String() + ":case:case/c1"}, nil)
	if resultCode(res) != domain.CodeNotFound {
		t.Fatalf("want 404-shaped NOT_FOUND for cross-tenant urn, got %+v", res)
	}
}

// ---- AC-14: caller-scoped tools/list -----------------------------------------

func TestAC14_ToolsListScoped(t *testing.T) {
	h := mustHarness(t)
	tenant := newTenant()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user. Use when a case needs an owner.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())
	h.publishTool(t, "case.get", "Fetch case detail. Use when you need a case's assignee.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.enableTool(t, tenant, "case.assign", nil, "", nil)
	h.enableTool(t, tenant, "case.get", nil, "", nil)
	// Pinned toolset is AUTHORITATIVE from the verified token scopes (TPL-FR-031):
	// only [case.assign] → intersection excludes case.get.
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.assign"})
	names := h.mcpList(t, token)
	if len(names) != 1 || names[0] != "case.assign" {
		t.Fatalf("want exactly [case.assign], got %+v", names)
	}
}

// SECURITY: a client cannot widen its toolset via _meta — the token is authoritative.
func TestSEC_ToolsetNotWidenableByMeta(t *testing.T) {
	h := mustHarness(t)
	tenant := newTenant()
	h.publishTool(t, "case.assign", "Assign an anomaly case to a user. Use when a case needs an owner.", domain.TierWriteProposal, domain.SideEffectReversible, caseAssignSchema())
	h.publishTool(t, "case.get", "Fetch case detail. Use when you need a case's assignee.", domain.TierRead, domain.SideEffectNone, caseGetSchema())
	h.enableTool(t, tenant, "case.assign", nil, "", nil)
	h.enableTool(t, tenant, "case.get", nil, "", nil)
	// Token pins only case.assign; the body tries to add case.get via _meta.toolset.
	token := h.agentToken(tenant.String(), "case-triage", "3", "user:u1", []string{"case.assign"})
	names := h.mcpListRaw(t, token, []string{"case.assign", "case.get"})
	if contains(names, "case.get") {
		t.Fatalf("_meta must not widen toolset; case.get leaked: %+v", names)
	}
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// ---- AC-15: manifest identity mismatch (BR-14) -------------------------------

func TestAC15_ManifestIdentityMismatch(t *testing.T) {
	h := mustHarness(t)
	opToken := h.operatorToken(uuid.NewString())
	body, _ := json.Marshal(map[string]any{"tool_id": "case.spoof", "display_name": "x", "owner_service": "case-service", "owner_team": "t"})
	req := httptest.NewRequest(http.MethodPost, "/api/v1/tools", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+opToken)
	req.Header.Set("X-Spiffe-Id", "spiffe://windrose/ns/prod/sa/chart-service") // != owner_service
	rec := httptest.NewRecorder()
	h.registry.Router().ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("want 403 on SPIFFE/owner mismatch, got %d", rec.Code)
	}
}
