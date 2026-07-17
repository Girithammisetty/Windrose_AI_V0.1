package api

import (
	"encoding/json"
	"net/http"

	"github.com/google/uuid"

	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/exec"
)

type sqlRunReq struct {
	SQL          string                     `json:"sql"`
	Variables    map[string]json.RawMessage `json:"variables"`
	Declarations []domain.VariableDecl      `json:"declarations"`
	// Binds are ordered positional arguments for SQL carrying $n placeholders
	// (chart-service's compiled-SQL contract: filters/drilldowns arrive as
	// prepared-statement binds). Mutually exclusive with declarations.
	Binds       []any      `json:"binds"`
	WorkspaceID *uuid.UUID `json:"workspace_id"`
	Mode        string     `json:"mode"`
	EngineHint  string     `json:"engine_hint"`
	Limit       int64      `json:"limit"`
	Cache       *bool      `json:"cache"`
}

const maxAdhocSQLBytes = 32 << 10 // MCP facade constraint: sql ≤ 32KB

func (s *Server) adhocRequest(w http.ResponseWriter, r *http.Request) (exec.RunRequest, bool) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("invalid claims"))
		return exec.RunRequest{}, false
	}
	var req sqlRunReq
	if !decodeBody(w, r, &req) {
		return exec.RunRequest{}, false
	}
	if req.SQL == "" {
		writeErr(w, r, domain.EValidation("sql is required"))
		return exec.RunRequest{}, false
	}
	if len(req.SQL) > maxAdhocSQLBytes {
		writeErr(w, r, domain.EValidation("sql exceeds 32KB"))
		return exec.RunRequest{}, false
	}
	if m := r.URL.Query().Get("mode"); m != "" && req.Mode == "" {
		req.Mode = m
	}
	rr := exec.RunRequest{
		PlanRequest: exec.PlanRequest{
			Op: op, SQLText: req.SQL, Decls: req.Declarations, Values: req.Variables,
			Binds:      req.Binds,
			EngineHint: req.EngineHint, Limit: req.Limit, Async: req.Mode != "sync",
		},
		Mode:     req.Mode,
		UseCache: req.Cache == nil || *req.Cache,
	}
	if req.WorkspaceID != nil {
		rr.WorkspaceID = *req.WorkspaceID
	}
	return rr, true
}

// handleRunSQL is ad-hoc execution (QRY-FR-006): inline SQL + inline
// declarations under the same safety rules; recorded in history but not
// saved.
func (s *Server) handleRunSQL(w http.ResponseWriter, r *http.Request) {
	req, ok := s.adhocRequest(w, r)
	if !ok {
		return
	}
	s.runAndRespond(w, r, req)
}

// handleDryRun plans + estimates without executing (QRY-FR-041).
func (s *Server) handleDryRun(w http.ResponseWriter, r *http.Request) {
	req, ok := s.adhocRequest(w, r)
	if !ok {
		return
	}
	req.PlanRequest.Async = true // dry-run estimates against the async caps
	plan, err := s.Broker.DryRun(r.Context(), req)
	if err != nil {
		writeErr(w, r, err) // 422 COST_CEILING_EXCEEDED carries the estimate
		return
	}
	writeData(w, http.StatusOK, map[string]any{
		"engine":               plan.Route.Engine,
		"routing_reason":       plan.Route.Reason,
		"estimated_scan_bytes": plan.Estimate.ScanBytes,
		"estimated_rows":       plan.Estimate.Rows,
		"partitions_pruned":    plan.Estimate.PartitionsPruned,
		"confidence":           plan.Estimate.Confidence,
		"ceiling_verdict":      plan.CeilingVerdict,
		"ceilings": map[string]any{
			"max_scan_bytes":   plan.Ceilings.MaxScanBytes,
			"max_runtime_s":    plan.Ceilings.MaxRuntimeS,
			"max_result_bytes": plan.Ceilings.MaxResultBytes,
			"max_result_rows":  plan.Ceilings.MaxResultRows,
		},
		"warnings": plan.Warnings,
	})
}
