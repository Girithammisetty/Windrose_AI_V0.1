// Package policy embeds the tool-plane Rego bundle so the gateway can load it
// into the OPA sidecar at boot in dev (the shared sidecar otherwise serves only
// the rbac bundle). In production OPA loads this file from its bundle mount.
package policy

import _ "embed"

// ToolPlaneRego is the source of policy/tool_plane.rego (TPL-FR-032).
//
//go:embed tool_plane.rego
var ToolPlaneRego string

// ToolPlaneModuleID is the OPA policy id used when uploading via the REST API.
const ToolPlaneModuleID = "windrose_tool_plane"
