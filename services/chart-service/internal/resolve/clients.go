// Package resolve implements chart data resolution (CHART-FR-020): it compiles
// a chart's semantic sources to SQL via the REAL semantic-service, executes
// that SQL via the REAL query-service, and shapes the result. All upstream
// calls are real net/http against protocol-compatible services — there are no
// hardcoded responses in this package. Test doubles live only in *_test.go.
package resolve

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/windrose-ai/chart-service/internal/domain"
)

// CompileRequest is the semantic-service POST /compile body (SEM /compile).
type CompileRequest struct {
	Model       string          `json:"model"`
	WorkspaceID string          `json:"workspace_id,omitempty"`
	Dialect     string          `json:"dialect,omitempty"`
	Metrics     []string        `json:"metrics"`
	Dimensions  []string        `json:"dimensions,omitempty"`
	Filters     []CompileFilter `json:"filters,omitempty"`
	Variables   map[string]any  `json:"variables,omitempty"`
}

// CompileFilter is a semantic filter (values exit only as bind params).
type CompileFilter struct {
	Dimension string `json:"dimension"`
	Op        string `json:"op"`
	Values    []any  `json:"values"`
}

// CompileResult is the compiled SQL + positional params + output schema.
type CompileResult struct {
	SQL          string         `json:"sql"`
	Params       []CompileParam `json:"params"`
	OutputSchema []SchemaColumn `json:"output_schema"`
	Warnings     []string       `json:"warnings"`
}

// CompileParam is one positional bind (type + value).
type CompileParam struct {
	Type  string `json:"type"`
	Value any    `json:"value"`
}

// SchemaColumn is one compiled output column with its role.
type SchemaColumn struct {
	Name string `json:"name"`
	Type string `json:"type"`
	Role string `json:"role"`
}

// SemanticCompiler compiles a chart's semantic sources to SQL.
type SemanticCompiler interface {
	Compile(ctx context.Context, token string, req CompileRequest) (CompileResult, error)
}

// HTTPSemantic is the real semantic-service client (SEM POST /compile).
type HTTPSemantic struct {
	BaseURL string
	Client  *http.Client
}

// NewHTTPSemantic builds a real client for base (e.g. http://localhost:8086).
func NewHTTPSemantic(base string) *HTTPSemantic {
	return &HTTPSemantic{BaseURL: base, Client: &http.Client{Timeout: 10 * time.Second}}
}

// Compile POSTs to {base}/api/v1/compile and decodes the CompileResponse.
func (h *HTTPSemantic) Compile(ctx context.Context, token string, req CompileRequest) (CompileResult, error) {
	body, _ := json.Marshal(req)
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, h.BaseURL+"/api/v1/compile", bytes.NewReader(body))
	if err != nil {
		return CompileResult{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if token != "" {
		httpReq.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := h.Client.Do(httpReq)
	if err != nil {
		return CompileResult{}, domain.EUpstream("semantic-service unreachable: " + err.Error())
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode == http.StatusUnprocessableEntity {
		return CompileResult{}, domain.ESourceBroken("semantic compile rejected: " + string(raw))
	}
	if resp.StatusCode != http.StatusOK {
		return CompileResult{}, domain.EUpstream(fmt.Sprintf("semantic compile status %d: %s", resp.StatusCode, string(raw)))
	}
	var out struct {
		Data CompileResult `json:"data"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return CompileResult{}, domain.EUpstream("semantic compile: bad response body")
	}
	return out.Data, nil
}

// ExecResult is the executed columns + rows + optional next cursor.
type ExecResult struct {
	Columns    []domain.ExecColumn
	Rows       [][]any
	NextCursor string
}

// QueryExecutor executes SQL (with positional binds) via query-service.
type QueryExecutor interface {
	RunSQL(ctx context.Context, token, sql string, binds []any, limit int) (ExecResult, error)
	RunSQLPaged(ctx context.Context, token, sql string, binds []any, cursor string, limit int) (ExecResult, error)
	RunSavedQuery(ctx context.Context, token, queryID string, variables map[string]any, limit int) (ExecResult, error)
	// SavedQuerySQL fetches a saved query's SQL text (for drilldown wrapping).
	SavedQuerySQL(ctx context.Context, token, queryID string) (string, error)
}

// HTTPQuery is the real query-service client. It runs SQL synchronously
// (POST /sql/run mode=sync) then fetches the paginated result page
// (GET /executions/{id}/results), matching the query-service contract.
type HTTPQuery struct {
	BaseURL string
	Client  *http.Client
}

// NewHTTPQuery builds a real client for base (e.g. http://localhost:8085).
func NewHTTPQuery(base string) *HTTPQuery {
	return &HTTPQuery{BaseURL: base, Client: &http.Client{Timeout: 20 * time.Second}}
}

func (h *HTTPQuery) post(ctx context.Context, token, path string, body any) ([]byte, int, error) {
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.BaseURL+path, bytes.NewReader(b))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := h.Client.Do(req)
	if err != nil {
		return nil, 0, domain.EUpstream("query-service unreachable: " + err.Error())
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	return raw, resp.StatusCode, nil
}

func (h *HTTPQuery) get(ctx context.Context, token, path string) ([]byte, int, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, h.BaseURL+path, nil)
	if err != nil {
		return nil, 0, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := h.Client.Do(req)
	if err != nil {
		return nil, 0, domain.EUpstream("query-service unreachable: " + err.Error())
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	return raw, resp.StatusCode, nil
}

type runResp struct {
	Data struct {
		ExecutionID string `json:"execution_id"`
		Status      string `json:"status"`
	} `json:"data"`
}

type resultsResp struct {
	Data struct {
		Columns []domain.ExecColumn `json:"columns"`
		Rows    [][]any             `json:"rows"`
		Page    struct {
			NextCursor *string `json:"next_cursor"`
			HasMore    bool    `json:"has_more"`
		} `json:"page"`
	} `json:"data"`
}

func (h *HTTPQuery) runAndFetch(ctx context.Context, token string, body map[string]any, cursor string, limit int) (ExecResult, error) {
	raw, status, err := h.post(ctx, token, "/api/v1/sql/run", body)
	if err != nil {
		return ExecResult{}, err
	}
	if status != http.StatusOK && status != http.StatusAccepted {
		return ExecResult{}, domain.EUpstream(fmt.Sprintf("query run status %d: %s", status, string(raw)))
	}
	var rr runResp
	if err := json.Unmarshal(raw, &rr); err != nil || rr.Data.ExecutionID == "" {
		return ExecResult{}, domain.EUpstream("query run: bad response body")
	}
	path := fmt.Sprintf("/api/v1/executions/%s/results?limit=%d", rr.Data.ExecutionID, limit)
	if cursor != "" {
		path += "&cursor=" + cursor
	}
	rraw, rstatus, err := h.get(ctx, token, path)
	if err != nil {
		return ExecResult{}, err
	}
	if rstatus != http.StatusOK {
		return ExecResult{}, domain.EUpstream(fmt.Sprintf("query results status %d: %s", rstatus, string(rraw)))
	}
	var res resultsResp
	if err := json.Unmarshal(rraw, &res); err != nil {
		return ExecResult{}, domain.EUpstream("query results: bad response body")
	}
	out := ExecResult{Columns: res.Data.Columns, Rows: res.Data.Rows}
	if res.Data.Page.NextCursor != nil {
		out.NextCursor = *res.Data.Page.NextCursor
	}
	return out, nil
}

// RunSQL executes SQL synchronously and returns the first result page.
func (h *HTTPQuery) RunSQL(ctx context.Context, token, sql string, binds []any, limit int) (ExecResult, error) {
	return h.runAndFetch(ctx, token, map[string]any{"sql": sql, "binds": binds, "mode": "sync", "limit": limit}, "", limit)
}

// RunSQLPaged executes SQL and returns a specific page (drilldown).
func (h *HTTPQuery) RunSQLPaged(ctx context.Context, token, sql string, binds []any, cursor string, limit int) (ExecResult, error) {
	return h.runAndFetch(ctx, token, map[string]any{"sql": sql, "binds": binds, "mode": "sync", "limit": limit}, cursor, limit)
}

// RunSavedQuery runs a saved query by id with variable substitution done
// server-side by query-service (POST /queries/:id/run).
func (h *HTTPQuery) RunSavedQuery(ctx context.Context, token, queryID string, variables map[string]any, limit int) (ExecResult, error) {
	body := map[string]any{"variables": variables, "mode": "sync", "limit": limit}
	raw, status, err := h.post(ctx, token, "/api/v1/queries/"+queryID+"/run", body)
	if err != nil {
		return ExecResult{}, err
	}
	if status != http.StatusOK && status != http.StatusAccepted {
		return ExecResult{}, domain.EUpstream(fmt.Sprintf("saved query run status %d: %s", status, string(raw)))
	}
	var rr runResp
	if err := json.Unmarshal(raw, &rr); err != nil || rr.Data.ExecutionID == "" {
		return ExecResult{}, domain.EUpstream("saved query run: bad response body")
	}
	rraw, rstatus, err := h.get(ctx, token, fmt.Sprintf("/api/v1/executions/%s/results?limit=%d", rr.Data.ExecutionID, limit))
	if err != nil {
		return ExecResult{}, err
	}
	if rstatus != http.StatusOK {
		return ExecResult{}, domain.EUpstream(fmt.Sprintf("saved query results status %d", rstatus))
	}
	var res resultsResp
	if err := json.Unmarshal(rraw, &res); err != nil {
		return ExecResult{}, domain.EUpstream("saved query results: bad response body")
	}
	out := ExecResult{Columns: res.Data.Columns, Rows: res.Data.Rows}
	if res.Data.Page.NextCursor != nil {
		out.NextCursor = *res.Data.Page.NextCursor
	}
	return out, nil
}

// SavedQuerySQL fetches a saved query's SQL text (GET /queries/{id}).
func (h *HTTPQuery) SavedQuerySQL(ctx context.Context, token, queryID string) (string, error) {
	raw, status, err := h.get(ctx, token, "/api/v1/queries/"+queryID)
	if err != nil {
		return "", err
	}
	if status != http.StatusOK {
		return "", domain.EUpstream(fmt.Sprintf("saved query fetch status %d", status))
	}
	var out struct {
		Data struct {
			SQL            string `json:"sql"`
			CurrentVersion struct {
				SQLText string `json:"sql_text"`
			} `json:"current_version"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return "", domain.EUpstream("saved query fetch: bad response body")
	}
	if out.Data.SQL != "" {
		return out.Data.SQL, nil
	}
	return out.Data.CurrentVersion.SQLText, nil
}

// ArtifactFetcher fetches run/dataset artifacts (experiment-/dataset-service).
type ArtifactFetcher interface {
	FetchArtifact(ctx context.Context, token, urn string) (json.RawMessage, error)
}

// HTTPArtifacts is the real experiment/dataset artifact client.
type HTTPArtifacts struct {
	ExperimentURL string
	DatasetURL    string
	Client        *http.Client
}

// NewHTTPArtifacts builds a real artifact client.
func NewHTTPArtifacts(experimentURL, datasetURL string) *HTTPArtifacts {
	return &HTTPArtifacts{ExperimentURL: experimentURL, DatasetURL: datasetURL, Client: &http.Client{Timeout: 10 * time.Second}}
}

// FetchArtifact GETs the artifact JSON for a run/dataset URN.
func (h *HTTPArtifacts) FetchArtifact(ctx context.Context, token, urn string) (json.RawMessage, error) {
	base := h.ExperimentURL
	if len(urn) > 0 && contains(urn, ":dataset:") {
		base = h.DatasetURL
	}
	if base == "" {
		return nil, domain.EUpstream("artifact service not configured for " + urn)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/api/v1/artifacts?urn="+urn, nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := h.Client.Do(req)
	if err != nil {
		return nil, domain.EUpstream("artifact service unreachable: " + err.Error())
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, domain.EUpstream(fmt.Sprintf("artifact status %d", resp.StatusCode))
	}
	return json.RawMessage(raw), nil
}

func contains(s, sub string) bool { return bytes.Contains([]byte(s), []byte(sub)) }
