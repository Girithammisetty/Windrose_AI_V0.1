// Package authz is the tool-plane's REAL authorization adapter for the
// per-invocation OPA check (TPL-FR-032). It follows the same pattern as
// libs/go-common/opaclient — POSTing an `input` document to the local OPA
// sidecar's data API and reading the decision — but carries the tool-plane
// normative input shape (BRD §3: subject × obo × tenant × tier × affected URNs ×
// argument constraints). There is NO allow-all escape hatch in the runtime path;
// the permissive fake lives only in unit tests. OPA unreachable ⇒ error ⇒ the
// pipeline fails closed with POLICY_UNAVAILABLE (BR-1).
package authz

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// Subject is the agent principal (BRD §3 OPA input subject).
type Subject struct {
	Type         string `json:"type"`
	AgentID      string `json:"agent_id"`
	AgentVersion string `json:"agent_version"`
	Principal    string `json:"principal"`
}

// ProposalExecution is the signed grant presented for a write-tier execution
// (TPL-FR-035). The args_digest must match the call's digest.
type ProposalExecution struct {
	ProposalID string `json:"proposal_id"`
	DecidedBy  string `json:"decided_by"`
	ArgsDigest string `json:"args_digest"`
}

// Input is the normative OPA input document (BRD §3). Facts (obo grants,
// compiled constraints, tenant max tier, toolset) are resolved gateway-side so
// the policy stays pure.
type Input struct {
	Subject           Subject             `json:"subject"`
	OboSub            string              `json:"obo_sub"`
	Tenant            string              `json:"tenant"`
	Action            string              `json:"action"`
	ToolID            string              `json:"tool_id"`
	ResourceURN       string              `json:"resource_urn"`
	Tier              string              `json:"tier"`
	MaxTier           string              `json:"max_tier"`
	AffectedURNs      []string            `json:"affected_urns"`
	Args              map[string]any      `json:"args"`
	Toolset           []string            `json:"toolset"`
	Constraints       map[string]any      `json:"constraints"`
	OboGrants         []string            `json:"obo_grants"`
	ArgsDigest        string              `json:"args_digest"`
	ProposalExecution *ProposalExecution  `json:"proposal_execution"`
}

// Decision is the policy result.
type Decision struct {
	Allow              bool   `json:"allow"`
	Reason             string `json:"reason"`
	ViolatedConstraint string `json:"violated_constraint"`
}

// Checker is the port the pipeline depends on (real OPA impl + unit fake).
type Checker interface {
	Check(ctx context.Context, in Input) (Decision, error)
}

// OPAClient calls a local OPA sidecar's data API (real integration).
type OPAClient struct {
	BaseURL string // e.g. http://localhost:8281
	Path    string // data document (default windrose/tool_plane/decision)
	client  *http.Client
}

// NewOPAClient builds a client for baseURL.
func NewOPAClient(baseURL string) *OPAClient {
	return &OPAClient{
		BaseURL: baseURL,
		Path:    "windrose/tool_plane/decision",
		client:  &http.Client{Timeout: 3 * time.Second},
	}
}

// Check evaluates in against the tool-plane policy and returns the decision.
// A transport/HTTP error is returned to the caller, which fails closed (BR-1).
func (c *OPAClient) Check(ctx context.Context, in Input) (Decision, error) {
	if in.Args == nil {
		in.Args = map[string]any{}
	}
	// The normative OPA input (BRD §3) models these as arrays; a nil Go slice
	// marshals to JSON `null`, and the policy's `object.get(input,"x",[])` only
	// defaults when the key is ABSENT — a present `null` stays null, and
	// `every urn in null` is NOT vacuously true, which would spuriously fail
	// obo_grant/toolset for a caller with an empty set. Emit `[]`, never null.
	if in.AffectedURNs == nil {
		in.AffectedURNs = []string{}
	}
	if in.OboGrants == nil {
		in.OboGrants = []string{}
	}
	if in.Toolset == nil {
		in.Toolset = []string{}
	}
	body, _ := json.Marshal(map[string]any{"input": in})
	url := fmt.Sprintf("%s/v1/data/%s", c.BaseURL, c.Path)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return Decision{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return Decision{}, fmt.Errorf("opa unreachable: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
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
		// Undefined result: fail closed (BR-1).
		return Decision{Allow: false, Reason: "deny_default"}, nil
	}
	return *out.Result, nil
}

// UploadPolicy PUTs a Rego module to the running OPA server's policies API. Used
// by integration tests (and dev bootstrap) to load policy/tool_plane.rego into
// the shared sidecar that otherwise serves the rbac bundle. Production loads
// policy via the OPA bundle mount.
func (c *OPAClient) UploadPolicy(ctx context.Context, id, rego string) error {
	url := fmt.Sprintf("%s/v1/policies/%s", c.BaseURL, id)
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, url, bytes.NewReader([]byte(rego)))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "text/plain")
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("opa upload policy %s: status %d", id, resp.StatusCode)
	}
	return nil
}
