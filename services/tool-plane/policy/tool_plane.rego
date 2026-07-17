# Windrose tool-plane authorization policy (BRD 13 §3, TPL-FR-032).
#
# The mcp-gateway enforcement pipeline POSTs the normative OPA input document
# (BRD §3) to the local OPA sidecar; this policy composes the decision:
#
#   allow = agent_toolset_scope
#         ∧ (obo_sub == "" ∨ obo_grant_on_all(affected_urns))
#         ∧ tenant_tier_allows
#         ∧ constraints_satisfied(args)
#
# Argument constraints are the tenant matrix + tool-declared bounds compiled into
# data by the gateway (BR-11: no free-form Rego from tenants — only max, maxLength,
# enum_subset, maxItems). For proposal executions the signed grant's args_digest
# must match. The decision carries a machine reason and the violated constraint id
# so the audit event can record it (AC-3).
package windrose.tool_plane

import rego.v1

default decision := {"allow": false, "reason": "deny_default", "violated_constraint": ""}

decision := {"allow": true, "reason": "allow", "violated_constraint": ""} if allow

decision := {"allow": false, "reason": first_reason, "violated_constraint": violated_constraint} if {
	not allow
	count(deny_reasons) > 0
	first_reason := deny_reasons[0]
}

# ---- top-level allow ---------------------------------------------------------

allow if {
	toolset_ok
	obo_grant_ok
	tier_ok
	constraints_ok
	proposal_ok
}

# ---- toolset scope (agent's pinned toolset includes the tool) -----------------

toolset := object.get(input, "toolset", [])

toolset_ok if count(toolset) == 0

toolset_ok if {
	some t in toolset
	t == input.tool_id
}

# ---- OBO grant intersection (user's grant on every affected resource) --------

obo_sub := object.get(input, "obo_sub", "")

grants := object.get(input, "obo_grants", [])

affected := object.get(input, "affected_urns", [])

obo_grant_ok if obo_sub == ""

obo_grant_ok if {
	obo_sub != ""
	every urn in affected {
		some g in grants
		g == urn
	}
}

# ---- tenant tier gate --------------------------------------------------------

tier_rank := {"read": 0, "write-proposal": 1, "write-direct": 2, "admin": 3}

req_tier := object.get(tier_rank, input.tier, 99)

max_tier := object.get(tier_rank, object.get(input, "max_tier", input.tier), 99)

tier_ok if req_tier <= max_tier

# ---- argument constraints (compiled bounds, BR-11) ---------------------------

constraints := object.get(input, "constraints", {})

args := object.get(input, "args", {})

# A field violates if any of its declared bounds is exceeded.
field_violation(field, spec) if {
	maxlen := spec.maxLength
	v := args[field]
	is_string(v)
	count(v) > maxlen
}

field_violation(field, spec) if {
	m := spec.max
	v := args[field]
	is_number(v)
	v > m
}

field_violation(field, spec) if {
	mi := spec.maxItems
	v := args[field]
	is_array(v)
	count(v) > mi
}

field_violation(field, spec) if {
	allowed := spec.enum_subset
	v := args[field]
	not v_in(v, allowed)
}

v_in(v, allowed) if {
	some a in allowed
	a == v
}

violating_fields contains field if {
	some field, spec in constraints
	field_violation(field, spec)
}

constraints_ok if count(violating_fields) == 0

sorted_violations := sort([f | some f in violating_fields])

violated_constraint := sorted_violations[0] if count(violating_fields) > 0

violated_constraint := "" if count(violating_fields) == 0

# ---- proposal execution grant (write tiers, TPL-FR-035) ----------------------

pe := object.get(input, "proposal_execution", null)

proposal_ok if pe == null

proposal_ok if {
	pe != null
	pe.args_digest == object.get(input, "args_digest", "")
}

# ---- ordered deny reasons (first applicable maps to the audit code) ----------

deny_reasons := array.concat(r_toolset, array.concat(r_obo, array.concat(r_tier, array.concat(r_constr, r_prop))))

r_toolset := ["toolset_scope"] if not toolset_ok
r_toolset := [] if toolset_ok

r_obo := ["obo_grant"] if not obo_grant_ok
r_obo := [] if obo_grant_ok

r_tier := ["tenant_tier"] if not tier_ok
r_tier := [] if tier_ok

r_constr := ["argument_constraint"] if not constraints_ok
r_constr := [] if constraints_ok

r_prop := ["proposal_grant"] if not proposal_ok
r_prop := [] if proposal_ok
