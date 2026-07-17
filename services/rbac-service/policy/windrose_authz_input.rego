# Windrose authorization policy — INPUT-projection variant (MASTER-FR-012).
#
# The canonical policy (windrose_authz.rego, package windrose.authz) evaluates
# against the Redis permissions_flat projection exposed as `data.perm`. This
# sibling package evaluates the SAME decision logic against a per-request
# projection slice carried in `input.projection`, so a caller (libs/go-common
# opaclient) can read exactly the projection entries it needs from Redis and
# POST them with the request. Both packages mirror internal/authz/decide.go and
# are held in cross-engine parity by the rbac integration suite.
#
# input = {
#   subject: {id, typ, obo_sub?, scopes?},
#   action, resource_urn?, workspace_id?, tenant,
#   projection: {
#     action_known: bool, action_scoped: bool, autonomous_enabled: bool,
#     flags: {found: bool, admin: bool, ws_admin: [ws_id]},
#     tenant_actions: {found: bool, actions: [action]},
#     workspace: {assigned: bool, actions: [action], archived: bool},
#     resource: {found: bool, level: str, archived: bool},
#     workspace_archived_tenant: bool
#   }
# }
package windrose.authz_input

import rego.v1

default allow := false

proj := input.projection

ws_id := object.get(input, "workspace_id", "")

res_urn := object.get(input, "resource_urn", "")

scopes := object.get(input.subject, "scopes", [])

typ := object.get(input.subject, "typ", "user")

action_known if proj.action_known

scoped if proj.action_scoped

verb := parts[count(parts) - 1] if {
	parts := split(input.action, ".")
}

read_verbs := {"read", "list", "export"}

level_verbs := {
	"viewer": {"read", "list", "export"},
	"editor": {"read", "list", "export", "update", "execute", "share"},
	"owner": {"read", "list", "export", "update", "execute", "share", "delete", "admin"},
}

scope_ok if {
	some s in scopes
	s == input.action
}

scope_ok if {
	some s in scopes
	s == "*"
}

flags := object.get(proj, "flags", {"found": false, "admin": false, "ws_admin": []})

is_admin if {
	flags.found
	flags.admin == true
}

# ---- context validation ------------------------------------------------------

ctx_ok if {
	scoped
	ws_id != ""
}

ctx_ok if {
	not scoped
	ws_id == ""
}

archived_write_block if {
	ws_id != ""
	proj.workspace_archived_tenant == true
	not read_verbs[verb]
}

principal_known if typ in {"user", "service", "agent_obo", "agent_autonomous"}

user_path if typ == "user"

user_path if typ == "service"

user_path if {
	typ == "agent_obo"
	scope_ok
}

# ---- decision rules ----------------------------------------------------------

# Autonomous agents.
allow if {
	typ == "agent_autonomous"
	action_known
	scope_ok
	proj.autonomous_enabled == true
}

# Admin flag short-circuit.
allow if {
	user_path
	action_known
	ctx_ok
	is_admin
	not archived_write_block
}

# Workspace-scoped role action in an assigned workspace.
allow if {
	user_path
	action_known
	scoped
	ws_id != ""
	proj.workspace.assigned == true
	not ws_archived_block
	some a in proj.workspace.actions
	a == input.action
}

# Workspace-admin flag on an assigned workspace.
allow if {
	user_path
	action_known
	scoped
	ws_id != ""
	proj.workspace.assigned == true
	not ws_archived_block
	some w in flags.ws_admin
	w == ws_id
}

ws_archived_block if {
	proj.workspace.archived == true
	not read_verbs[verb]
}

# Tenant-scoped role action.
allow if {
	user_path
	action_known
	not scoped
	ws_id == ""
	proj.tenant_actions.found
	some a in proj.tenant_actions.actions
	a == input.action
}

# Resource-grant overlay (additive; level -> verbs).
allow if {
	user_path
	action_known
	ctx_ok
	res_urn != ""
	proj.resource.found == true
	level_verbs[proj.resource.level][verb]
	not grant_archived_block
}

grant_archived_block if {
	proj.resource.archived == true
	not read_verbs[verb]
}

# ---- reasons (mirror internal/authz reason codes) ----------------------------

default reason := "deny_default"

reason := "allowed" if allow

reason := "unknown_action" if {
	not allow
	not action_known
}

reason := "unknown_principal_type" if {
	not allow
	action_known
	not principal_known
}

reason := "scope_excluded" if {
	not allow
	action_known
	typ in {"agent_obo", "agent_autonomous"}
	not scope_ok
}

reason := "autonomous_disabled" if {
	not allow
	action_known
	typ == "agent_autonomous"
	scope_ok
	not proj.autonomous_enabled
}

reason := "WORKSPACE_CONTEXT_REQUIRED" if {
	not allow
	action_known
	user_path
	scoped
	ws_id == ""
}

reason := "WORKSPACE_CONTEXT_FORBIDDEN" if {
	not allow
	action_known
	user_path
	not scoped
	ws_id != ""
}

reason := "workspace_archived" if {
	not allow
	action_known
	user_path
	ctx_ok
	ws_id != ""
	proj.workspace_archived_tenant == true
	is_admin
	not read_verbs[verb]
}

reason := "workspace_archived" if {
	not allow
	action_known
	user_path
	ctx_ok
	scoped
	proj.workspace.assigned == true
	proj.workspace.archived == true
	not read_verbs[verb]
}

# projection miss: context valid, user's flags absent, no assignment/actions.
# ctx_ok gates these so WORKSPACE_CONTEXT_* reasons win when context is invalid
# (mirrors decide.go, which validates context before the miss/assignment logic).
default miss := false

miss if {
	action_known
	user_path
	ctx_ok
	not is_admin
	not flags.found
	scoped
	not proj.workspace.assigned
}

miss if {
	action_known
	user_path
	ctx_ok
	not is_admin
	not flags.found
	not scoped
	not proj.tenant_actions.found
}

reason := "projection_miss" if {
	not allow
	miss
}

reason := "workspace_not_assigned" if {
	not allow
	action_known
	user_path
	ctx_ok
	scoped
	not proj.workspace.assigned
	flags.found
}

result := {"allow": allow, "reason": reason, "miss": miss}
