package store

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// GetPreferences returns a user's preferences, or a zero-value default when
// unset (NOTIF-FR-012).
func (s *PG) GetPreferences(ctx context.Context, tenant uuid.UUID, userID string) (*domain.UserPreferences, error) {
	p := &domain.UserPreferences{
		TenantID:        tenant,
		UserID:          userID,
		ChannelOverride: map[string][]string{},
		DigestConfig:    map[string]string{},
	}
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var overrides, mutes, quiet, digest []byte
		err := tx.QueryRow(ctx, `SELECT id, channel_overrides, mutes, quiet_hours, digest_config, updated_at FROM user_preferences WHERE user_id=$1`, userID).
			Scan(&p.ID, &overrides, &mutes, &quiet, &digest, &p.UpdatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		_ = json.Unmarshal(overrides, &p.ChannelOverride)
		_ = json.Unmarshal(mutes, &p.Mutes)
		if len(quiet) > 0 && string(quiet) != "null" {
			p.QuietHours = &domain.QuietHours{}
			_ = json.Unmarshal(quiet, p.QuietHours)
		}
		_ = json.Unmarshal(digest, &p.DigestConfig)
		return nil
	})
	return p, err
}

// PutPreferences upserts a user's preferences.
func (s *PG) PutPreferences(ctx context.Context, p *domain.UserPreferences) error {
	if p.ID == uuid.Nil {
		p.ID = domain.NewID()
	}
	return s.withTenant(ctx, p.TenantID, func(tx pgx.Tx) error {
		var quiet []byte
		if p.QuietHours != nil {
			quiet = mustJSON(p.QuietHours)
		}
		_, err := tx.Exec(ctx, `
			INSERT INTO user_preferences (id, tenant_id, user_id, channel_overrides, mutes, quiet_hours, digest_config, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7, now())
			ON CONFLICT (tenant_id, user_id) DO UPDATE SET
				channel_overrides=$4, mutes=$5, quiet_hours=$6, digest_config=$7, updated_at=now()`,
			p.ID, p.TenantID, p.UserID, mustJSON(p.ChannelOverride), mustJSON(p.Mutes), quiet, mustJSON(p.DigestConfig))
		return err
	})
}
