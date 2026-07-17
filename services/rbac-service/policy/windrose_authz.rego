# Windrose platform authorization policy (RBC-FR-044).
#
# Mirrors internal/authz/decide.go EXACTLY — that Go package is the reference
# implementation; both are covered by the same table-driven cases
# (policy/policy_test.go runs this file through the OPA Go SDK).
#
# In production the OPA sidecar evaluates this bundle against the Redis
# permissions_flat projection, exposed under data.perm with the shape:
#
#   data.perm.catalog[action]           -> {"workspace_scoped": bool}
#   data.perm.tenants[t].archived_ws    -> {"<ws_id>": true}
#   data.perm.tenants[t].autonomous_enabled -> bool
#   data.perm.tenants[t].users[u].actions   -> [tenant-scoped actions]
#   data.perm.tenants[t].users[u].ws[w]     -> {"actions": [...], "archived": bool}
#   data.perm.tenants[t].users[u].res[h]    -> {"level": "...", "archived": bool}
#   data.perm.tenants[t].users[u].flags     -> {"admin": bool, "ws_admin": [...]}
#
# Input: {subject: {id, typ, obo_sub?, scopes?}, action, resource_urn?,
#         workspace_id?, tenant}
#
# Deny-by-default; additive only — no negative grants.
package windrose.authz

import rego.v1

default allow := false

# ---- input helpers -----------------------------------------------------------

ws_id := object.get(input, "workspace_id", "")

res_urn := object.get(input, "resource_urn", "")

scopes := object.get(input.subject, "scopes", [])

typ := object.get(input.subject, "typ", "user")

# OBO agents evaluate the ORIGINAL user's projection (intersection rule).
effective_user := input.subject.obo_sub if {
	typ == "agent_obo"
	object.get(input.subject, "obo_sub", "") != ""
}

effective_user := input.subject.id if {
	not typ == "agent_obo"
}

effective_user := input.subject.id if {
	typ == "agent_obo"
	object.get(input.subject, "obo_sub", "") == ""
}

# ---- catalog -----------------------------------------------------------------

catalog_entry := data.perm.catalog[input.action]

action_known if catalog_entry

scoped if catalog_entry.workspace_scoped

verb := parts[count(parts) - 1] if {
	parts := split(input.action, ".")
}

read_verbs := {"read", "list", "export"}

# Level -> verb mapping, fixed platform-wide (RBC-FR-030).
level_verbs := {
	"viewer": {"read", "list", "export"},
	"editor": {"read", "list", "export", "update", "execute", "share"},
	"owner": {"read", "list", "export", "update", "execute", "share", "delete", "admin"},
}

# ---- scopes / tenant flags ---------------------------------------------------

scope_ok if {
	some s in scopes
	s == input.action
}

scope_ok if {
	some s in scopes
	s == "*"
}

tenant_data := data.perm.tenants[input.tenant]

autonomous_enabled if object.get(tenant_data, "autonomous_enabled", false)

tenant_archived_ws := object.get(tenant_data, "archived_ws", {})

user_data := tenant_data.users[effective_user]

user_flags := object.get(user_data, "flags", {"admin": false, "ws_admin": []})

is_admin if user_flags.admin == true

# ---- shared guards -----------------------------------------------------------

# Workspace-context validation (AC-3: V1 workspace_dependent semantics).
ctx_ok if {
	scoped
	ws_id != ""
}

ctx_ok if {
	not scoped
	ws_id == ""
}

# Archived-workspace write block — applies even to the admin flag (BR-7).
archived_write_block if {
	ws_id != ""
	tenant_archived_ws[ws_id]
	not read_verbs[verb]
}

# Known principal types (fail-closed: anything else has no allow path and
# denies with unknown_principal_type, mirroring decide.go's default case).
principal_known if typ in {"user", "service", "agent_obo", "agent_autonomous"}

# Principals whose USER projection is consulted.
user_path if typ == "user"

user_path if typ == "service"

user_path if {
	typ == "agent_obo"
	scope_ok # intersection: agent scopes AND user grants (BR-6)
}

# ---- decision rules ----------------------------------------------------------

# Autonomous agents: action ∈ scopes AND tenant enablement flag (RBC-FR-044).
allow if {
	typ == "agent_autonomous"
	action_known
	scope_ok
	autonomous_enabled
}

# Admin flag short-circuit (tenant-bound; archived write block still applies).
allow if {
	user_path
	action_known
	ctx_ok
	is_admin
	not archived_write_block
}

# Workspace-scoped action via role in an assigned workspace.
allow if {
	user_path
	action_known
	scoped
	ws_id != ""
	entry := user_data.ws[ws_id]
	ws_entry_allows(entry)
}

ws_entry_allows(entry) if {
	some a in entry.actions
	a == input.action
	not ws_entry_archived_block(entry)
}

# Workspace-admin flag: every workspace-scoped action in administered
# workspaces (still archived-write blocked).
allow if {
	user_path
	action_known
	scoped
	ws_id != ""
	entry := user_data.ws[ws_id] # must be assigned
	not ws_entry_archived_block(entry)
	some w in user_flags.ws_admin
	w == ws_id
}

ws_entry_archived_block(entry) if {
	entry.archived == true
	not read_verbs[verb]
}

# Tenant-scoped action via role.
allow if {
	user_path
	action_known
	not scoped
	ws_id == ""
	some a in user_data.actions
	a == input.action
}

# Resource-grant overlay (additive; level -> verbs).
urn_hash := substring(crypto.sha256(res_urn), 0, 32) if res_urn != ""

allow if {
	user_path
	action_known
	ctx_ok
	res_urn != ""
	grant := user_data.res[urn_hash]
	level_verbs[grant.level][verb]
	not grant_archived_block(grant)
}

grant_archived_block(grant) if {
	grant.archived == true
	not read_verbs[verb]
}

# ---- deny reasons (diagnostics; mirrors authz reason codes) -------------------

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
	not autonomous_enabled
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

result := {"allow": allow, "reason": reason}
