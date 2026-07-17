# Seeding steps invoked during boot (sourced by run.sh).

# Idempotent DB seeds identity needs before it can verify harness tokens and
# provision a tenant: the harness signing key (so identity's KeyManager trusts
# our RS256 tokens) and at least one capacity cell per cloud.
seed_identity_prereqs() {
  say "seed identity: harness signing key + cells"
  local pub; pub="$(cat "${E2E_DIR}/keys/idp_public.pem")"
  PGPASSWORD=windrose_dev psql -h localhost -U windrose -d identity -v ON_ERROR_STOP=0 -v pub="$pub" >/dev/null 2>&1 <<'SQL'
INSERT INTO signing_keys (kid, alg, vault_ref, public_key_pem, not_before, created_at, updated_at)
VALUES ('e2e-harness-key-1','RS256','', :'pub', '2020-01-01T00:00:00Z', now(), now())
ON CONFLICT (kid) DO UPDATE SET public_key_pem = EXCLUDED.public_key_pem, retired_at = NULL;
INSERT INTO cells (id, name, cloud, region, capacity, tenant_count)
VALUES ('11111111-1111-1111-1111-111111111111','cell-aws-use1','aws','us-east-1',500,0)
ON CONFLICT (name) DO NOTHING;
INSERT INTO cells (id, name, cloud, region, capacity, tenant_count)
VALUES ('22222222-2222-2222-2222-222222222222','cell-gcp-usc1','gcp','us-central1',500,0)
ON CONFLICT (name) DO NOTHING;
INSERT INTO cells (id, name, cloud, region, capacity, tenant_count)
VALUES ('33333333-3333-3333-3333-333333333333','cell-azure-eus','azure','eastus',500,0)
ON CONFLICT (name) DO NOTHING;
SQL
  ok "identity prereqs seeded"
}

# Provision the tenant (real identity engine) and stash TENANT_ID for later phases.
seed_tenant() {
  say "provision tenant (identity-service real engine)"
  TENANT_ID="$(cd "$E2E_DIR" && "$PY" lib/seed.py tenant 2>>"$LOG_DIR/seed.log")"
  [ -n "$TENANT_ID" ] || die "tenant provisioning returned no id (see logs/seed.log)"
  echo "export TENANT_ID='$TENANT_ID'" > "$PID_DIR/../context.env"
  ok "tenant provisioned: $TENANT_ID"
}

# Seed ai-gateway deployment + mint the agent's tenant-scoped virtual key.
seed_ai_gateway() {
  ( cd "$E2E_DIR" && "$PY" lib/seed.py aigw "$TENANT_ID" 2>>"$LOG_DIR/seed.log" )
}
