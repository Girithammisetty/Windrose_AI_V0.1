package api

import "sync"

// RegGate tracks the rbac action-catalog registration status (RBC-FR-022)
// for readiness gating: /readyz reports degraded until registration has
// succeeded, so a manifest rbac rejected can never be silently ignored
// (OPA would deny every guarded route with action_known=false).
type RegGate struct {
	mu     sync.Mutex
	done   bool
	reason string
}

// NewRegGate returns a gate in the "pending" state.
func NewRegGate() *RegGate {
	return &RegGate{reason: "action registration pending"}
}

// Succeed marks registration complete; /readyz stops reporting degraded.
func (g *RegGate) Succeed() {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.done = true
	g.reason = ""
}

// Fail records the failure reason (kept until a later attempt succeeds).
func (g *RegGate) Fail(reason string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.done {
		return
	}
	if reason == "" {
		reason = "action registration failed"
	}
	g.reason = "action registration failed: " + reason
}

// Reason returns "" when registration succeeded, else the degraded reason.
func (g *RegGate) Reason() string {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.done {
		return ""
	}
	return g.reason
}
