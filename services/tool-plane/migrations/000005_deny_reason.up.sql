-- Persist the OPA/policy deny REASON on invocation rows. Until now only the
-- error_code survived (e.g. PERMISSION_DENIED) while the machine reason
-- (toolset_scope / obo_grant / tier / constraint / a backend facade's 403
-- message) was dropped — diagnosing a denial required re-deriving the rego
-- evaluation by hand. Nullable: allowed invocations carry no reason.
ALTER TABLE invocation_log ADD COLUMN deny_reason TEXT;
