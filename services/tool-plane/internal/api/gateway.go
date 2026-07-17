package api

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/windrose-ai/go-common/authjwt"
	"github.com/windrose-ai/go-common/metricsx"
	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/enforce"
	"github.com/windrose-ai/tool-plane/internal/mcp"
	"github.com/windrose-ai/tool-plane/internal/register"
	"github.com/windrose-ai/tool-plane/internal/store"
)

// GatewayServer is the mcp-gateway data plane: the single /mcp Streamable-HTTP
// JSON-RPC endpoint (initialize, tools/list, tools/call) that federates backend
// MCP facades behind the enforcement pipeline (BRD §3, TPL-FR-010/011). It is
// stateless per request — MCP session ids are opaque passthrough (BR-17).
type GatewayServer struct {
	Pipeline *enforce.Pipeline
	Store    *store.PG
	Verifier *authjwt.Verifier
	Kill     *enforce.KillRegistry
	// RegStatus gates /readyz on action-catalog registration (RBC-FR-022);
	// nil skips the gate (unit tests / dev wiring).
	RegStatus *register.Status
}

// Router builds the gateway router (/mcp + health + RED metrics).
func (g *GatewayServer) Router() http.Handler {
	metrics := metricsx.New("mcp-gateway")
	mux := http.NewServeMux()
	mux.Handle("/metrics", metrics.Handler())
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
		if err := g.Store.Ping(r.Context()); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		if g.RegStatus != nil {
			if ok, reason := g.RegStatus.Ready(); !ok {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusServiceUnavailable)
				_, _ = w.Write([]byte(`{"status":"unavailable","reason":` + jsonString(reason) + `}`))
				return
			}
		}
		w.WriteHeader(http.StatusOK)
	})
	mux.Handle("/mcp", traceMiddleware(http.HandlerFunc(g.handleMCP)))
	// http.ServeMux exposes no route templates, so record RED metrics under a
	// single "all" label (route func nil) rather than unbounded raw paths.
	var h http.Handler = mux
	h = metrics.Middleware(nil)(h)
	return h
}

// ---- JSON-RPC types ----------------------------------------------------------

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Result  any             `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

func (g *GatewayServer) writeRPC(w http.ResponseWriter, id json.RawMessage, result any, rerr *rpcError) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(rpcResponse{JSONRPC: "2.0", ID: id, Result: result, Error: rerr})
}

// handleMCP authenticates the agent JWT then dispatches the JSON-RPC method.
func (g *GatewayServer) handleMCP(w http.ResponseWriter, r *http.Request) {
	// AuthN (step 1): verify the platform JWT (typ=agent_*). authN failures map to
	// JSON-RPC error -32001 (BRD §3 error map).
	tok := bearer(r)
	var cl *authjwt.Claims
	if tok != "" {
		c, err := g.Verifier.Verify(r.Context(), tok)
		if err == nil {
			cl = c
		}
	}
	if cl == nil {
		g.writeRPC(w, nil, nil, &rpcError{Code: -32001, Message: "TOKEN_INVALID"})
		return
	}

	var req rpcRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		g.writeRPC(w, nil, nil, &rpcError{Code: -32700, Message: "parse error"})
		return
	}
	switch req.Method {
	case "initialize":
		g.writeRPC(w, req.ID, map[string]any{
			"protocolVersion": mcp.SpecVersion,
			"capabilities":    map[string]any{"tools": map[string]any{"listChanged": true}},
			"serverInfo":      map[string]any{"name": "windrose-mcp-gateway", "version": "1.0.0"},
		}, nil)
	case "tools/list":
		g.handleToolsList(w, r, req, cl)
	case "tools/call":
		g.handleToolsCall(w, r, req, cl)
	default:
		g.writeRPC(w, req.ID, nil, &rpcError{Code: -32601, Message: "method not found"})
	}
}

func bearer(r *http.Request) string {
	h := r.Header.Get("Authorization")
	const p = "Bearer "
	if len(h) > len(p) && (h[:len(p)] == p) {
		return h[len(p):]
	}
	return ""
}

// handleToolsList returns the caller-scoped intersection (pinned toolset ∩ enabled
// ∩ published/deprecated ∩ not killed ∩ not retired) with deprecation warnings
// (TPL-FR-011/AC-14). Capped at 100 (BR-10).
func (g *GatewayServer) handleToolsList(w http.ResponseWriter, r *http.Request, req rpcRequest, cl *authjwt.Claims) {
	tenant, err := cl.Tenant()
	if err != nil {
		g.writeRPC(w, req.ID, nil, &rpcError{Code: -32001, Message: "invalid tenant"})
		return
	}
	// The pinned toolset is authoritative from the verified token (TPL-FR-031);
	// a client cannot widen its own scope via _meta.
	toolset := tokenToolset(cl)

	callables, err := g.Store.ListEnabledVersions(r.Context(), tenant)
	if err != nil {
		g.writeRPC(w, req.ID, nil, &rpcError{Code: -32603, Message: "internal error"})
		return
	}
	tools := make([]map[string]any, 0, len(callables))
	for _, c := range callables {
		if len(toolset) > 0 && !contains(toolset, c.Version.ToolID) {
			continue
		}
		if killed, _ := g.Kill.IsKilled(tenant, c.Version.ToolID, c.Version.Version); killed {
			continue
		}
		entry := map[string]any{
			"name":        c.Version.ToolID,
			"description": c.Version.SemanticDescription,
			"inputSchema": c.Version.InputSchema,
			"_meta":       map[string]any{"version": c.Version.Version, "tier": c.Version.PermissionTier},
		}
		if c.Version.Status == domain.StatusDeprecated && c.Version.DeprecationEndsAt != nil {
			entry["_meta"].(map[string]any)["deprecation"] = map[string]any{"ends_at": c.Version.DeprecationEndsAt}
		}
		tools = append(tools, entry)
	}
	g.writeRPC(w, req.ID, map[string]any{"tools": tools}, nil)
}

// handleToolsCall runs the enforcement pipeline and maps the outcome to the MCP
// result / JSON-RPC error per the BRD §3 error table.
func (g *GatewayServer) handleToolsCall(w http.ResponseWriter, r *http.Request, req rpcRequest, cl *authjwt.Claims) {
	tenant, err := cl.Tenant()
	if err != nil {
		g.writeRPC(w, req.ID, nil, &rpcError{Code: -32001, Message: "invalid tenant"})
		return
	}
	var params struct {
		Name      string         `json:"name"`
		Arguments map[string]any `json:"arguments"`
		Meta      struct {
			Version string `json:"version"`
			// ProposalGrant is a raw RS256-signed grant token from agent-runtime.
			// It is only accepted after the pipeline VERIFIES it; the untrusted
			// value here can never itself authorize a write (TPL-FR-035).
			ProposalGrant string `json:"proposal_grant"`
		} `json:"_meta"`
	}
	if err := json.Unmarshal(req.Params, &params); err != nil {
		g.writeRPC(w, req.ID, nil, &rpcError{Code: -32602, Message: "invalid params"})
		return
	}
	if params.Arguments == nil {
		params.Arguments = map[string]any{}
	}
	er := enforce.Request{
		AgentID: cl.AgentID, AgentVersion: cl.AgentVersion, Principal: agentPrincipal(cl), Typ: cl.Typ,
		OboSub: cl.OboSub, Tenant: tenant, TenantStr: cl.TenantID, ToolID: params.Name, Version: params.Meta.Version,
		Args: params.Arguments,
		// Authoritative pinned toolset comes from the VERIFIED token, never the
		// client body (TPL-FR-031).
		Toolset: tokenToolset(cl),
		// Eval mode is honoured only for callers holding the trusted eval scope
		// (eval-service / agent-runtime replay), never on a plain agent's say-so
		// via _meta or a header (BR-16).
		Eval:          evalAuthorized(cl, r),
		ProposalGrant: params.Meta.ProposalGrant,
		TraceID:       TraceID(r.Context()),
	}
	oc := g.Pipeline.Run(r.Context(), er)

	// POLICY_UNAVAILABLE → JSON-RPC error -32002 (fail-closed infra, BRD §3).
	if oc.Code == domain.CodePolicyUnavailable {
		g.writeRPC(w, req.ID, nil, &rpcError{Code: -32002, Message: domain.CodePolicyUnavailable})
		return
	}
	g.writeRPC(w, req.ID, mcpResult(oc), nil)
}

// mcpResult renders an Outcome as an MCP tools/call result (isError + structured
// content per the BRD §3 mapping table).
func mcpResult(oc enforce.Outcome) map[string]any {
	meta := map[string]any{}
	if oc.Deprecation != nil {
		meta["deprecation"] = map[string]any{"ends_at": oc.Deprecation.EndsAt, "message": oc.Deprecation.Message}
	}
	switch oc.Decision {
	case "allowed":
		res := map[string]any{
			"content":           []map[string]any{{"type": "text", "text": "ok"}},
			"structuredContent": oc.Output,
			"isError":           false,
		}
		if len(meta) > 0 {
			res["_meta"] = meta
		}
		return res
	case "proposal_required":
		return map[string]any{
			"content":           []map[string]any{{"type": "text", "text": "PROPOSAL_REQUIRED"}},
			"structuredContent": oc.Structured,
			"isError":           false,
		}
	case "stubbed":
		return map[string]any{
			"content":           []map[string]any{{"type": "text", "text": "stubbed"}},
			"structuredContent": map[string]any{"status": "stubbed"},
			"isError":           false,
		}
	default:
		sc := map[string]any{"code": oc.Code, "message": oc.Message}
		if oc.RetryAfter > 0 {
			sc["retry_after_s"] = oc.RetryAfter
		}
		if oc.Details != nil {
			sc["details"] = oc.Details
		}
		return map[string]any{
			"content":           []map[string]any{{"type": "text", "text": oc.Code}},
			"structuredContent": sc,
			"isError":           true,
		}
	}
}

func agentPrincipal(cl *authjwt.Claims) string {
	if cl.AgentID != "" {
		v := cl.AgentVersion
		if v == "" {
			v = "0"
		}
		return "agent:" + cl.AgentID + "@v" + v
	}
	return "agent:" + cl.Sub
}

// tokenToolset derives the agent's authoritative pinned toolset from the VERIFIED
// token scopes (TPL-FR-031). Scopes that name a namespaced tool id are the pinned
// toolset; a wildcard scope means "no restriction". The client body never
// contributes — it cannot widen scope.
func tokenToolset(cl *authjwt.Claims) []string {
	var out []string
	for _, s := range cl.Scopes {
		if s != "*" && containsRune(s, '.') {
			out = append(out, s)
		}
	}
	return out
}

// evalScope is the scope issued only to eval-service / agent-runtime replay that
// authorizes eval mode (BR-16).
const evalScope = "tool.eval"

// evalAuthorized reports whether eval mode is permitted for this caller. Eval is
// honoured only when the VERIFIED token carries the eval scope; a plain agent
// cannot turn its own write calls into audited no-op stubs via _meta or a header.
func evalAuthorized(cl *authjwt.Claims, r *http.Request) bool {
	requested := r.Header.Get("x-windrose-eval") == "true"
	if !requested {
		return false
	}
	return cl.HasScope(evalScope)
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func containsRune(s string, r rune) bool {
	for _, c := range s {
		if c == r {
			return true
		}
	}
	return false
}

// catalogAdapter adapts *store.PG to enforce.CatalogResolver, converting the
// stored backend row into the mcp.BackendTarget the pipeline dispatches to. It
// lives here (not in store) so store never imports mcp (avoids an import cycle).
type catalogAdapter struct {
	store *store.PG
}

// NewCatalogResolver builds the enforce.CatalogResolver over the store.
func NewCatalogResolver(s *store.PG) enforce.CatalogResolver { return &catalogAdapter{store: s} }

func (a *catalogAdapter) ResolveVersion(ctx context.Context, toolID, version string) (*domain.ToolVersion, error) {
	return a.store.ResolveVersion(ctx, toolID, version)
}

func (a *catalogAdapter) BackendFor(ctx context.Context, toolID string) (mcp.BackendTarget, error) {
	tool, err := a.store.GetTool(ctx, toolID)
	if err != nil {
		return mcp.BackendTarget{}, err
	}
	b, err := a.store.GetBackendByService(ctx, tool.OwnerService)
	if err != nil {
		return mcp.BackendTarget{}, err
	}
	if b == nil {
		return mcp.BackendTarget{}, store.ErrNotFound
	}
	return mcp.BackendTarget{URL: b.InternalURL, SpiffeID: b.SpiffeID}, nil
}
