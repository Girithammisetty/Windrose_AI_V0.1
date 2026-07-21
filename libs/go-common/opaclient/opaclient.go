// Package opaclient is the platform's real authorization client (MASTER-FR-012).
// A service authorizes locally by (1) reading the caller's permissions_flat
// projection slice from Redis (the rbac-service key scheme, RBC-FR-040) and
// (2) POSTing it as `input` to its local OPA sidecar, which evaluates the
// windrose.authz_input Rego bundle. This never calls rbac-service synchronously
// in the request path. The decision is byte-for-byte the same one rbac's Go
// `Decide` returns for the same projection — proven by the rbac parity suite.
package opaclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// Subject mirrors the OPA input subject (MASTER-FR-011/012).
type Subject struct {
	ID     string   `json:"id"`
	Typ    string   `json:"typ"`
	OboSub string   `json:"obo_sub,omitempty"`
	Scopes []string `json:"scopes,omitempty"`
}

// Flags is the user's admin/ws-admin projection flags.
type Flags struct {
	Found   bool     `json:"found"`
	Admin   bool     `json:"admin"`
	WsAdmin []string `json:"ws_admin"`
}

// TenantActions is the user's tenant-scoped allowed action set.
type TenantActions struct {
	Found   bool     `json:"found"`
	Actions []string `json:"actions"`
}

// WorkspaceFacts is the user's entry for the request's workspace.
type WorkspaceFacts struct {
	Assigned bool     `json:"assigned"`
	Actions  []string `json:"actions"`
	Archived bool     `json:"archived"`
}

// ResourceFacts is the user's grant on the request's resource URN.
type ResourceFacts struct {
	Found    bool   `json:"found"`
	Level    string `json:"level"`
	Archived bool   `json:"archived"`
}

// Projection is the per-request slice of permissions_flat the policy evaluates.
type Projection struct {
	ActionKnown             bool           `json:"action_known"`
	ActionScoped            bool           `json:"action_scoped"`
	AutonomousEnabled       bool           `json:"autonomous_enabled"`
	Flags                   Flags          `json:"flags"`
	TenantActions           TenantActions  `json:"tenant_actions"`
	Workspace               WorkspaceFacts `json:"workspace"`
	Resource                ResourceFacts  `json:"resource"`
	WorkspaceArchivedTenant bool           `json:"workspace_archived_tenant"`
}

// Input is the OPA decision input.
type Input struct {
	Subject     Subject    `json:"subject"`
	Action      string     `json:"action"`
	ResourceURN string     `json:"resource_urn,omitempty"`
	WorkspaceID string     `json:"workspace_id,omitempty"`
	Tenant      string     `json:"tenant"`
	Projection  Projection `json:"projection"`
}

// Decision is the OPA result (mirrors rbac authz.Decision).
type Decision struct {
	Allow  bool   `json:"allow"`
	Reason string `json:"reason"`
	Miss   bool   `json:"miss"`
}

// Client calls a local OPA server's data API.
type Client struct {
	// BaseURL is the OPA server, e.g. http://localhost:8281.
	BaseURL string
	// Path is the data document evaluated (default windrose/authz_input/result).
	Path   string
	client *http.Client
	// fb is the optional Redis-miss fallback (RBC-FR-045), set via
	// EnableMissFallback. Nil means "not configured" -- CheckWithRedis then
	// behaves exactly as it always has (deny on a miss).
	fb *fallback
}

// New builds a Client for baseURL (OPA sidecar).
func New(baseURL string) *Client {
	return &Client{
		BaseURL: baseURL,
		Path:    "windrose/authz_input/result",
		client:  &http.Client{Timeout: 3 * time.Second},
	}
}

// Check evaluates in against the OPA bundle and returns the decision.
func (c *Client) Check(ctx context.Context, in Input) (Decision, error) {
	body, _ := json.Marshal(map[string]any{"input": in})
	url := fmt.Sprintf("%s/v1/data/%s", c.BaseURL, c.Path)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return Decision{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return Decision{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Decision{}, fmt.Errorf("opa: status %d", resp.StatusCode)
	}
	var out struct {
		Result *Decision `json:"result"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return Decision{}, err
	}
	if out.Result == nil {
		// Undefined result: fail closed.
		return Decision{Allow: false, Reason: "deny_default"}, nil
	}
	return *out.Result, nil
}
