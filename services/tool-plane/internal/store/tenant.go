package store

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/datacern-ai/tool-plane/internal/domain"
	"github.com/datacern-ai/tool-plane/internal/events"
)

// ---- Per-tenant enablement (TPL-FR-004) -------------------------------------

// PutTenantSettings upserts an enablement row (RLS: tenant session).
func (s *PG) PutTenantSettings(ctx context.Context, st *domain.TenantToolSettings, envs []events.Envelope) error {
	return s.withTenant(ctx, st.TenantID, func(tx pgx.Tx) error {
		var maxTier any
		if st.MaxTierOverride != "" {
			maxTier = st.MaxTierOverride
		}
		var rlo any
		if st.RateLimitOverride != nil {
			rlo = mustJSON(st.RateLimitOverride)
		}
		_, err := tx.Exec(ctx, `
			INSERT INTO tenant_tool_settings (tenant_id, tool_id, enabled, max_tier_override, argument_constraints, rate_limit_override)
			VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (tenant_id, tool_id) DO UPDATE SET
				enabled=$3, max_tier_override=$4, argument_constraints=$5, rate_limit_override=$6, updated_at=now()`,
			st.TenantID, st.ToolID, st.Enabled, maxTier, mustJSON(st.ArgumentConstraints), rlo)
		if err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// GetTenantSettings returns the enablement row for (tenant, tool) or nil.
func (s *PG) GetTenantSettings(ctx context.Context, tenant uuid.UUID, toolID string) (*domain.TenantToolSettings, error) {
	var out *domain.TenantToolSettings
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var st domain.TenantToolSettings
		var maxTier *string
		var constraints, rlo []byte
		err := tx.QueryRow(ctx, `
			SELECT tenant_id, tool_id, enabled, max_tier_override, argument_constraints, rate_limit_override, updated_at
			FROM tenant_tool_settings WHERE tenant_id=$1 AND tool_id=$2`, tenant, toolID).Scan(
			&st.TenantID, &st.ToolID, &st.Enabled, &maxTier, &constraints, &rlo, &st.UpdatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if maxTier != nil {
			st.MaxTierOverride = *maxTier
		}
		_ = json.Unmarshal(constraints, &st.ArgumentConstraints)
		if len(rlo) > 0 {
			st.RateLimitOverride = &domain.RateLimitOverride{}
			_ = json.Unmarshal(rlo, st.RateLimitOverride)
		}
		out = &st
		return nil
	})
	return out, err
}

// EnabledToolIDs returns the set of tool ids enabled for a tenant (tools/list
// caller-scoping, TPL-FR-011).
func (s *PG) EnabledToolIDs(ctx context.Context, tenant uuid.UUID) (map[string]bool, error) {
	out := map[string]bool{}
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT tool_id FROM tenant_tool_settings WHERE tenant_id=$1 AND enabled=true`, tenant)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				return err
			}
			out[id] = true
		}
		return rows.Err()
	})
	return out, err
}

// ---- Kill switches (TPL-FR-052) ---------------------------------------------

// CreateKill inserts an active kill switch (platform-scoped; survives restart).
func (s *PG) CreateKill(ctx context.Context, k *domain.KillSwitch, envs []events.Envelope) error {
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO kill_switches (id, tenant_id, scope, tool_id, version, kill_tenant, active, reason, set_by)
			VALUES ($1,$2,$3,$4,$5,$6,true,$7,$8)`,
			k.ID, domain.PlatformTenant, k.Scope, k.ToolID, k.Version, k.TenantID, k.Reason, k.SetBy)
		if err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
	if isUniqueViolation(err) {
		return ErrConflict
	}
	return err
}

// DeactivateKill unsets a kill switch by id.
func (s *PG) DeactivateKill(ctx context.Context, id uuid.UUID, envs []events.Envelope) (*domain.KillSwitch, error) {
	var out *domain.KillSwitch
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		var k domain.KillSwitch
		err := tx.QueryRow(ctx, `
			UPDATE kill_switches SET active=false WHERE id=$1 AND active=true
			RETURNING id, scope, tool_id, version, kill_tenant, reason, set_by`, id).Scan(
			&k.ID, &k.Scope, &k.ToolID, &k.Version, &k.TenantID, &k.Reason, &k.SetBy)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNotFound
		}
		if err != nil {
			return err
		}
		out = &k
		return insertOutboxTx(ctx, tx, envs)
	})
	return out, err
}

// ActiveKills returns all active kill tuples (loaded into Redis on boot/change,
// and served verbatim by GET /kill-switches — the admin list surface). `active`
// IS selected/scanned (not just filtered on) so callers that serialize the
// struct directly (the HTTP list handler) don't emit a false Go zero-value for
// a row the WHERE clause already guarantees is true.
func (s *PG) ActiveKills(ctx context.Context) ([]*domain.KillSwitch, error) {
	var out []*domain.KillSwitch
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, scope, tool_id, version, kill_tenant, active, reason, set_by, created_at
			FROM kill_switches WHERE active=true`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var k domain.KillSwitch
			if err := rows.Scan(&k.ID, &k.Scope, &k.ToolID, &k.Version, &k.TenantID, &k.Active, &k.Reason, &k.SetBy, &k.CreatedAt); err != nil {
				return err
			}
			out = append(out, &k)
		}
		return rows.Err()
	})
	return out, err
}

// ---- BYO submissions (TPL-FR-040) -------------------------------------------

// CreateBYO inserts a pending submission.
func (s *PG) CreateBYO(ctx context.Context, b *domain.BYOSubmission, tenant uuid.UUID, envs []events.Envelope) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO byo_submissions (id, tenant_id, manifest, endpoint_url, auth_method, requested_tier, egress_description, status)
			VALUES ($1,$2,$3,$4,$5,$6,$7,'pending_approval')`,
			b.ID, tenant, mustJSON(b.Manifest), b.EndpointURL, b.AuthMethod, b.RequestedTier, b.EgressDescription)
		if err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// GetBYO fetches a submission.
func (s *PG) GetBYO(ctx context.Context, id uuid.UUID) (*domain.BYOSubmission, error) {
	var out *domain.BYOSubmission
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		var b domain.BYOSubmission
		var manifest []byte
		err := tx.QueryRow(ctx, `
			SELECT id, manifest, endpoint_url, auth_method, requested_tier, egress_description, status, decided_by, decision_message, created_at
			FROM byo_submissions WHERE id=$1`, id).Scan(
			&b.ID, &manifest, &b.EndpointURL, &b.AuthMethod, &b.RequestedTier, &b.EgressDescription,
			&b.Status, &b.DecidedBy, &b.DecisionMessage, &b.CreatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNotFound
		}
		if err != nil {
			return err
		}
		_ = json.Unmarshal(manifest, &b.Manifest)
		out = &b
		return nil
	})
	return out, err
}

// ListBYO returns submissions ordered newest-first, optionally filtered by
// status (Tier 2b admin queue: the approver has to be able to FIND pending
// submissions before deciding them). Platform scope, like GetBYO/DecideBYO —
// approvers review the whole queue.
func (s *PG) ListBYO(ctx context.Context, status string, limit int) ([]*domain.BYOSubmission, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	const cols = `id, manifest, endpoint_url, auth_method, requested_tier, egress_description, status, decided_by, decision_message, created_at`
	q := `SELECT ` + cols + ` FROM byo_submissions ORDER BY created_at DESC LIMIT $1`
	args := []any{limit}
	if status != "" {
		q = `SELECT ` + cols + ` FROM byo_submissions WHERE status=$2 ORDER BY created_at DESC LIMIT $1`
		args = append(args, status)
	}
	var out []*domain.BYOSubmission
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var b domain.BYOSubmission
			var manifest []byte
			if err := rows.Scan(&b.ID, &manifest, &b.EndpointURL, &b.AuthMethod, &b.RequestedTier,
				&b.EgressDescription, &b.Status, &b.DecidedBy, &b.DecisionMessage, &b.CreatedAt); err != nil {
				return err
			}
			_ = json.Unmarshal(manifest, &b.Manifest)
			out = append(out, &b)
		}
		return rows.Err()
	})
	return out, err
}

// DecideBYO approves/rejects a pending submission.
func (s *PG) DecideBYO(ctx context.Context, id uuid.UUID, status, decidedBy, message string, envs []events.Envelope) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE byo_submissions SET status=$2, decided_by=$3, decision_message=$4
			WHERE id=$1 AND status='pending_approval'`, id, status, decidedBy, message)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// ---- Invocation log + audit (TPL-FR-037) ------------------------------------

// nullIfEmpty maps "" to SQL NULL for nullable text columns.
func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// RecordInvocation writes the digest-level invocation row AND the
// ai.tool_invoked.v1 outbox event atomically (RLS: tenant session). Every
// enforcement attempt (allow/deny/error) goes through here (audit completeness).
func (s *PG) RecordInvocation(ctx context.Context, log *domain.InvocationLog, env events.Envelope) error {
	return s.withTenant(ctx, log.TenantID, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO invocation_log (id, tenant_id, agent_id, agent_version, obo_sub, tool_id, tool_version, tier, decision, error_code, deny_reason, args_digest, urns, latency_ms, trace_id)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
			log.ID, log.TenantID, log.AgentID, log.AgentVersion, log.OboSub, log.ToolID, log.ToolVersion,
			log.Tier, log.Decision, log.ErrorCode, nullIfEmpty(log.DenyReason), log.ArgsDigest, log.URNs, log.LatencyMS, log.TraceID); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, []events.Envelope{env})
	})
}

// InsertAudit writes a lone audit envelope (e.g. security.cross_tenant_denied)
// outside a mutation tx.
func (s *PG) InsertAudit(ctx context.Context, env events.Envelope) error {
	return s.withTenant(ctx, env.TenantID, func(tx pgx.Tx) error {
		return insertOutboxTx(ctx, tx, []events.Envelope{env})
	})
}

// ---- Idempotency (MASTER-FR-025) --------------------------------------------

// IdempotencyRecord is a stored POST response.
type IdempotencyRecord struct {
	Status   int
	Response []byte
}

// GetIdempotency returns a replayable record within 24h, or nil.
func (s *PG) GetIdempotency(ctx context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error) {
	var rec IdempotencyRecord
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT status, response FROM idempotency_keys
			WHERE tenant_id=$1 AND key=$2 AND created_at > now() - interval '24 hours'`,
			tenant, key).Scan(&rec.Status, &rec.Response)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &rec, nil
}

// PutIdempotency stores a POST response for replay.
func (s *PG) PutIdempotency(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO idempotency_keys (tenant_id, key, method, path, status, response)
			VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id, key) DO NOTHING`,
			tenant, key, method, path, status, response)
		return err
	})
}
