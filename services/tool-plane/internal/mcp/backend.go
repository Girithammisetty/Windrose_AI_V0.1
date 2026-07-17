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
	Kind string // timeout | backend_error | output_invalid | backend_rejected
	Err  error
	// StatusCode/Body carry the real backend response through for "backend_rejected"
	// (4xx) so the caller sees the ACTUAL reason (e.g. case-service's "not allowed:
	// case.disposition.approve") instead of a generic 502. Confirmed live
	// 2026-07-17: every case-service 403 from handleToolFacade was previously
	// swallowed here and reported to the caller as decision=allowed — a real
	// authorization failure was being recorded as a successful tool call.
	StatusCode int
	Body       map[string]any
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
	if resp.StatusCode >= 400 {
		// The backend REJECTED the call (auth/validation/not-found) — this is a
		// real failure, not a successful invocation. Previously only >=500 was
		// treated as an error, so every 4xx from the backend facade (e.g.
		// case-service's "not allowed: case.disposition.approve") unmarshalled
		// cleanly into a Result and was reported to the caller as decision=allowed.
		var body map[string]any
		_ = json.Unmarshal(raw, &body) // best-effort: surface the real message if JSON
		msg := fmt.Sprintf("backend status %d", resp.StatusCode)
		if body != nil {
			if out, ok := body["output"].(map[string]any); ok {
				if e, ok := out["error"].(string); ok && e != "" {
					msg = e
				}
			}
		}
		return nil, &BackendError{Kind: "backend_rejected", Err: fmt.Errorf("%s", msg),
			StatusCode: resp.StatusCode, Body: body}
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
