package api

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/chart-service/internal/authz"
	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/chart-service/internal/events"
	"github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/go-common/httpx"
)

// facadeReq is tool-plane's backend-facade contract (BRD 13, TPL-FR-012): the
// mcp-gateway POSTs {tool_id, version, args, tenant, obo_sub, agent_id} to a
// tool's owning-service backend URL after the full enforcement pipeline (OPA +
// signed proposal-execution grant). chart-service hosts the backend for the
// chart.dashboard.create write-proposal tool (the dashboard-designer agent)
// here.
type facadeReq struct {
	ToolID  string         `json:"tool_id"`
	Version string         `json:"version"`
	Args    map[string]any `json:"args"`
	Tenant  string         `json:"tenant"`
	OboSub  string         `json:"obo_sub"`
	AgentID string         `json:"agent_id"`
}

// handleToolFacade is the real MCP backend facade the tool-plane federates to
// (GAP-2). It creates the dashboard + its charts through the SAME store
// methods the human-facing POST /api/v1/dashboards and
// POST /api/v1/dashboards/{id}/charts handlers use. The peer identity is the
// mesh-injected SPIFFE id (X-Spiffe-Id); authorization is re-checked against
// the real OPA sidecar for the effective human (obo_sub) — the backend never
// blindly trusts the gateway.
func (s *Server) handleToolFacade(w http.ResponseWriter, r *http.Request) {
	// Mesh peer identity (MASTER-FR-014). In prod this rides mTLS; the gateway
	// forwards the intended peer identity in X-Spiffe-Id.
	spiffe := r.Header.Get("X-Spiffe-Id")
	if allowed := os.Getenv("CHART_FACADE_ALLOWED_SPIFFE"); allowed != "" {
		ok := false
		for _, a := range strings.Split(allowed, ",") {
			if strings.TrimSpace(a) == spiffe {
				ok = true
				break
			}
		}
		if !ok {
			facadeError(w, http.StatusForbidden, "facade requires an allowed SPIFFE peer identity")
			return
		}
	} else if spiffe == "" {
		facadeError(w, http.StatusForbidden, "facade requires a mesh peer identity (X-Spiffe-Id)")
		return
	}

	var req facadeReq
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		facadeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if req.ToolID != "chart.dashboard.create" {
		facadeError(w, http.StatusNotFound, "unknown tool_id")
		return
	}
	tenant, err := uuid.Parse(req.Tenant)
	if err != nil {
		facadeError(w, http.StatusBadRequest, "invalid tenant")
		return
	}

	name, _ := req.Args["name"].(string)
	if name == "" {
		facadeError(w, http.StatusBadRequest, "args.name is required")
		return
	}
	module, _ := req.Args["module"].(string)
	switch module {
	case domain.ModuleInsights, domain.ModuleCaseManagement, domain.ModuleInspector:
	default:
		module = domain.ModuleInsights
	}
	description, _ := req.Args["description"].(string)
	wsRaw, _ := req.Args["workspace_id"].(string)
	wsID, err := uuid.Parse(wsRaw)
	if err != nil {
		facadeError(w, http.StatusBadRequest, "args.workspace_id must be a uuid")
		return
	}

	// Real governed authorization for the effective human (obo_sub) against the
	// OPA sidecar. This is the same action the human create-dashboard path
	// checks (authz.ActionDashboardCreate), scoped to the target workspace.
	if s.Authz != nil {
		in := authz.Input{
			Subject:     authz.Subject{ID: req.OboSub, Typ: "user"},
			Action:      authz.ActionDashboardCreate,
			WorkspaceID: wsID.String(),
			Tenant:      req.Tenant,
		}
		if !s.Authz.Allow(r.Context(), in) {
			facadeError(w, http.StatusForbidden, "not allowed: "+authz.ActionDashboardCreate)
			return
		}
	}

	// Dual attribution built from the federated call (not a JWT): the
	// approving human is the actor, the agent is recorded on the emitted event
	// payload (MASTER-FR-041).
	d := &domain.Dashboard{
		ID: newID(), TenantID: tenant, WorkspaceID: wsID, Name: name, Module: module,
		Description: description, OwnerUserID: req.OboSub, Status: "active",
	}
	dashURN := events.URN(tenant, "dashboard", d.ID.String())
	dashEv := events.New(events.DashboardCreated, tenant, "user", req.OboSub, dashURN, traceID(r.Context()),
		map[string]any{"dashboard_id": d.ID.String(), "module": d.Module, "via_agent": req.AgentID})
	if err := s.Store.CreateDashboard(r.Context(), d, []event.Envelope{dashEv}); err != nil {
		slog.Warn("facade create dashboard failed", "err", err, "tenant", req.Tenant)
		facadeError(w, http.StatusUnprocessableEntity, "create dashboard failed: "+err.Error())
		return
	}

	chartsRaw, _ := req.Args["charts"].([]any)
	created := make([]map[string]any, 0, len(chartsRaw))
	for _, raw := range chartsRaw {
		cm, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		cname, _ := cm["name"].(string)
		if cname == "" {
			cname = "Chart"
		}
		ctype, _ := cm["chart_type"].(string)
		measures := toStringSlice(cm["measures"])
		dimensions := toStringSlice(cm["dimensions"])
		filters := toStringSlice(cm["filters"])

		usedType, cfg := buildChartConfig(ctype, measures, dimensions)
		cfgRaw, err := json.Marshal(cfg)
		if err != nil {
			slog.Warn("facade skip chart: config marshal failed", "err", err, "name", cname)
			continue
		}
		if verr := domain.ValidateConfig(usedType, cfgRaw, nil); verr != nil {
			slog.Warn("facade skip chart: invalid config", "err", verr, "name", cname, "chart_type", usedType)
			continue
		}
		// semantic_model is REQUIRED for the chart to compile at render time
		// (resolve.modelFromChart reads DisplayMeta.semantic_model). The
		// dashboard-designer supplies the grounded model per chart; without it
		// the created dashboard renders "model is required". Carry it through.
		model, _ := cm["model"].(string)
		if model == "" {
			model, _ = cm["semantic_model"].(string)
		}
		dm := map[string]any{}
		if len(filters) > 0 {
			dm["filters"] = filters
		}
		if model != "" {
			dm["semantic_model"] = model
			// The semantic model is referenced by NAME, so the render-time
			// compile (resolve.workspaceFromChart → CompileRequest.WorkspaceID)
			// needs the owning workspace to disambiguate it — without it the
			// semantic service rejects with "workspace_id required when model is
			// named by name". Carry the dashboard's workspace through.
			dm["workspace_id"] = wsID.String()
		}
		dmRaw := json.RawMessage("{}")
		if len(dm) > 0 {
			if b, err := json.Marshal(dm); err == nil {
				dmRaw = b
			}
		}

		c := &domain.Chart{
			ID: newID(), TenantID: tenant, DashboardID: d.ID, Name: cname, ChartType: usedType,
			Config: cfgRaw, DisplayMeta: dmRaw, ChartVersion: 1, Custom: true, ConfigStatus: "ok",
		}
		chURN := events.URN(tenant, "chart", c.ID.String())
		chEv := events.New(events.ChartCreated, tenant, "user", req.OboSub, chURN, traceID(r.Context()),
			map[string]any{"chart_id": c.ID.String(), "dashboard_id": d.ID.String(),
				"chart_type": c.ChartType, "chart_version": 1, "via_agent": req.AgentID})
		if err := s.Store.CreateChart(r.Context(), c, []event.Envelope{chEv}); err != nil {
			slog.Warn("facade create chart failed", "err", err, "dashboard_id", d.ID, "name", cname)
			continue
		}
		created = append(created, map[string]any{
			"id": c.ID.String(), "name": c.Name, "chart_type": c.ChartType,
		})
	}

	facadeOutput(w, map[string]any{
		"applied": true, "dashboard_id": d.ID.String(), "chart_count": len(created), "charts": created,
	})
}

// buildChartConfig maps the dashboard-designer's grounded measure/dimension
// names onto the real per-family config shape chart-service's own
// domain.ValidateConfig expects (CHART-FR-012), keyed off the REAL chart-type
// catalog (domain.LookupType) — never a guessed/invented field name. When the
// requested chart_type is unknown, or the grounded refs don't satisfy that
// family's required fields, it degrades to a grid_chart over whatever refs
// are available (never drops the write, never invents a semantic ref).
func buildChartConfig(chartType string, measures, dimensions []string) (string, map[string]any) {
	fam := ""
	if ct, ok := domain.LookupType(chartType); ok {
		fam = ct.Family
	}
	switch fam {
	case domain.FamilyAxis:
		if len(dimensions) >= 1 && len(measures) >= 1 {
			cfg := map[string]any{
				"x": map[string]any{"dimension": dimensions[0]},
				"y": measureRefs(measures),
			}
			if len(dimensions) >= 2 {
				cfg["dataseries"] = map[string]any{"dimension": dimensions[1]}
			}
			return chartType, cfg
		}
	case domain.FamilyYOnly:
		if len(measures) >= 1 {
			cfg := map[string]any{"y": measureRefs(measures)}
			if len(dimensions) >= 1 {
				cfg["x"] = map[string]any{"dimension": dimensions[0]}
			}
			return chartType, cfg
		}
	case domain.FamilyHeatmap:
		if len(dimensions) >= 3 {
			return chartType, map[string]any{
				"x":          map[string]any{"dimension": dimensions[0]},
				"y":          map[string]any{"dimension": dimensions[1]},
				"dataseries": map[string]any{"dimension": dimensions[2]},
			}
		}
	case domain.FamilyNetwork:
		if len(dimensions) >= 2 {
			return chartType, map[string]any{"nodes": dimensions[0], "children": dimensions[1]}
		}
	case domain.FamilyGrid:
		cols := append(append([]string{}, dimensions...), measures...)
		if len(cols) > 0 {
			return chartType, map[string]any{"columns": cols}
		}
	case domain.FamilyMetric:
		return chartType, map[string]any{}
	}
	cols := append(append([]string{}, dimensions...), measures...)
	if len(cols) == 0 {
		cols = []string{"value"}
	}
	return "grid_chart", map[string]any{"columns": cols}
}

func measureRefs(measures []string) []map[string]any {
	out := make([]map[string]any, 0, len(measures))
	for _, m := range measures {
		out = append(out, map[string]any{"measure": m})
	}
	return out
}

func toStringSlice(v any) []string {
	arr, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(arr))
	for _, x := range arr {
		if s, ok := x.(string); ok && s != "" {
			out = append(out, s)
		}
	}
	return out
}

func facadeOutput(w http.ResponseWriter, out map[string]any) {
	httpx.WriteJSON(w, http.StatusOK, map[string]any{"output": out})
}

func facadeError(w http.ResponseWriter, status int, msg string) {
	httpx.WriteJSON(w, status, map[string]any{"output": map[string]any{"applied": false, "error": msg}})
}
