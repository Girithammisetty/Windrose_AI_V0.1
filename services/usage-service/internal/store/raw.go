package store

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/usage-service/internal/domain"
)

// InsertRaw persists raw meter records idempotently (USG-FR-011). The unique
// constraint (tenant_id, event_id, meter_key, time) makes replays no-ops even
// when Redis dedup is bypassed; returns the count actually inserted.
func (s *PG) InsertRaw(ctx context.Context, recs []domain.MeterRecord) (int, error) {
	if len(recs) == 0 {
		return 0, nil
	}
	tenant := recs[0].TenantID
	inserted := 0
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		for _, r := range recs {
			tag, err := tx.Exec(ctx, `
				INSERT INTO usage_raw
				  (time, tenant_id, meter_key, quantity, workspace_id, user_id,
				   agent_id, model, cloud, resource_urn, event_id, late)
				VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
				ON CONFLICT (tenant_id, event_id, meter_key, time) DO NOTHING`,
				r.Time, r.TenantID, r.MeterKey, r.Quantity, r.WorkspaceID, r.UserID,
				r.AgentID, r.Model, r.Cloud, r.ResourceURN, r.EventID, r.Late)
			if err != nil {
				return err
			}
			inserted += int(tag.RowsAffected())
		}
		return nil
	})
	return inserted, err
}

// RawSum returns the summed quantity for a meter within [from,to) for a tenant,
// optionally filtered by scope dims (used by budget evaluation and the Redis
// counter resync path — AC-14).
func (s *PG) RawSum(ctx context.Context, tenant uuid.UUID, meterKey string, from, to interface{}, ws, user, agent *string) (float64, error) {
	var total float64
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT COALESCE(SUM(quantity),0)::float8 FROM usage_raw
			WHERE tenant_id=$1 AND meter_key=$2 AND time >= $3 AND time < $4
			  AND ($5::text IS NULL OR workspace_id=$5)
			  AND ($6::text IS NULL OR user_id=$6)
			  AND ($7::text IS NULL OR agent_id=$7)`,
			tenant, meterKey, from, to, ws, user, agent).Scan(&total)
	})
	return total, err
}
