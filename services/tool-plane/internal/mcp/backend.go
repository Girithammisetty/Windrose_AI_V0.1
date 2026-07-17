// Package mcp implements the gateway's MCP hosting/federation surface: the
// pinned MCP spec version, the single /mcp JSON-RPC endpoint (initialize,
// tools/list, tools/call), and the REAL HTTP client that federates calls to
// registered backend MCP facades. Where a downstream domain service isn't
// running in a test, the gateway calls it via this real HTTP client pointed at a
// stood-up test HTTP server — the client is real, never a hardcoded fake.
package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"
)

// SpecVersion is the pinned MCP spec version (TPL-FR-010). The adapter layer is
// isolated here for the 2026 stateless-core migration.
const SpecVersion = "2025-06-18"

// MaxResponseBytes caps a backend/BYO response (TPL-FR-013: 1MB).
const MaxResponseBytes = 1 << 20

// BackendError distinguishes timeout from other backend failures for health
// accounting (TPL-FR-036/BR-7).
type BackendError struct {
	Kind string // timeout | backend_error | output_invalid
	Err  error
}

func (e *BackendError) Error() string { return e.Kind + ": " + e.Err.Error() }

// Invocation is the gateway→backend request (validated args + attribution).
type Invocation struct {
	ToolID   string
	Version  string
	Args     map[string]any
	Tenant   string
	OboSub   string
	AgentID  string
	TraceID  string
}

// Result is the backend's tool output.
type Result struct {
	Output map[string]any `json:"output"`
}

// Backendinvoker is the port the pipeline depends on (real HTTP impl + fake in
// unit tests only).
type BackendInvoker interface {
	Invoke(ctx context.Context, target BackendTarget, in Invocation, readTier bool) (*Result, error)
}

// BackendTarget is the resolved backend endpoint + SLA-derived timeout.
type BackendTarget struct {
	URL       string
	SpiffeID  string
	P95MS     int
}

// HTTPBackend is the real HTTP MCP-facade client (net/http). Per-backend
// timeouts derive from the tool's declared SLA (3 × p95, cap 60s, TPL-FR-012).
type HTTPBackend struct {
	client *http.Client
}

// NewHTTPBackend builds a real HTTP backend client.
func NewHTTPBackend() *HTTPBackend {
	return &HTTPBackend{client: &http.Client{}}
}

// timeoutFor derives the per-call deadline from the declared p95 (TPL-FR-012).
func timeoutFor(p95ms int) time.Duration {
	if p95ms <= 0 {
		p95ms = 1000
	}
	d := time.Duration(3*p95ms) * time.Millisecond
	if d > 60*time.Second {
		d = 60 * time.Second
	}
	if d < 250*time.Millisecond {
		d = 250 * time.Millisecond
	}
	return d
}

// Invoke calls the backend facade over real HTTP with an SLA-derived deadline.
// read-tier calls retry once on timeout; write tiers never retry (BR-7).
func (b *HTTPBackend) Invoke(ctx context.Context, target BackendTarget, in Invocation, readTier bool) (*Result, error) {
	attempts := 1
	if readTier {
		attempts = 2
	}
	var lastErr error
	for i := 0; i < attempts; i++ {
		res, err := b.once(ctx, target, in)
		if err == nil {
			return res, nil
		}
		lastErr = err
		var be *BackendError
		if errors.As(err, &be) && be.Kind == "timeout" && readTier && i+1 < attempts {
			continue // retry read once
		}
		break
	}
	return nil, lastErr
}

func (b *HTTPBackend) once(ctx context.Context, target BackendTarget, in Invocation) (*Result, error) {
	to := timeoutFor(target.P95MS)
	cctx, cancel := context.WithTimeout(ctx, to)
	defer cancel()

	body, _ := json.Marshal(map[string]any{
		"tool_id": in.ToolID, "version": in.Version, "args": in.Args,
		"tenant": in.Tenant, "obo_sub": in.OboSub, "agent_id": in.AgentID,
	})
	req, err := http.NewRequestWithContext(cctx, http.MethodPost, target.URL, bytes.NewReader(body))
	if err != nil {
		return nil, &BackendError{Kind: "backend_error", Err: err}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Trace-Id", in.TraceID)
	// SPIFFE identity would ride mTLS client cert in production; the header
	// documents the intended peer identity for the facade. Sent under BOTH
	// names: X-Spiffe-Id (case-service's Go facade, the original convention)
	// and X-Client-Spiffe-Id (the Python-ecosystem-wide internal-auth header
	// every FastAPI service's require_internal reads — see e.g.
	// dataset-service/semantic-service/pipeline-orchestrator config.py
	// spiffe_header). Two names for one identity beats forcing every new
	// Python backend facade to invent a bespoke header check.
	if target.SpiffeID != "" {
		req.Header.Set("X-Spiffe-Id", target.SpiffeID)
		req.Header.Set("X-Client-Spiffe-Id", target.SpiffeID)
	}
	resp, err := b.client.Do(req)
	if err != nil {
		if cctx.Err() == context.DeadlineExceeded {
			return nil, &BackendError{Kind: "timeout", Err: err}
		}
		return nil, &BackendError{Kind: "backend_error", Err: err}
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 500 {
		return nil, &BackendError{Kind: "backend_error", Err: fmt.Errorf("backend status %d", resp.StatusCode)}
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, MaxResponseBytes))
	if err != nil {
		return nil, &BackendError{Kind: "backend_error", Err: err}
	}
	var res Result
	if err := json.Unmarshal(raw, &res); err != nil {
		return nil, &BackendError{Kind: "output_invalid", Err: err}
	}
	if res.Output == nil {
		res.Output = map[string]any{}
	}
	return &res, nil
}
