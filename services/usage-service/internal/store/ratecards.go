package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
)

// CreateRateCard inserts a draft rate card + items (USG-FR-042/044). Platform
// operator only; runs under platform scope.
func (s *PG) CreateRateCard(ctx context.Context, op domain.Op, rc domain.RateCard) (domain.RateCard, error) {
	rc.ID = domain.NewID()
	rc.Status = domain.RateCardDraft
	rc.CreatedAt = time.Now().UTC()
	if rc.Version == 0 {
		rc.Version = 1
	}
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO rate_cards (id, tenant_id, version, effective_from, status, created_at, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$6)`,
			rc.ID, rc.TenantID, rc.Version, rc.EffectiveFrom, rc.Status, rc.CreatedAt); err != nil {
			return err
		}
		for mk, price := range rc.Items {
			if _, err := tx.Exec(ctx, `
				INSERT INTO rate_card_items (rate_card_id, meter_key, price_per_unit_usd)
				VALUES ($1,$2,$3)`, rc.ID, mk, price); err != nil {
				return err
			}
		}
		return nil
	})
	return rc, err
}

// ActivateRateCard flips a draft to active, superseding the prior active card
// for the same scope, and emits ratecard.activated (USG-FR-042).
func (s *PG) ActivateRateCard(ctx context.Context, op domain.Op, id uuid.UUID) (domain.RateCard, error) {
	var rc domain.RateCard
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		if err := tx.QueryRow(ctx, `
			SELECT id, tenant_id, version, effective_from, status FROM rate_cards WHERE id=$1 FOR UPDATE`, id).
			Scan(&rc.ID, &rc.TenantID, &rc.Version, &rc.EffectiveFrom, &rc.Status); err != nil {
			return err
		}
		if rc.Status == domain.RateCardSuperseded {
			return domain.ErrConflict
		}
		if rc.Status == domain.RateCardActive {
			return nil // idempotent
		}
		// Supersede prior active card of same scope.
		if rc.TenantID == nil {
			if _, err := tx.Exec(ctx, `UPDATE rate_cards SET status='superseded', updated_at=now()
				WHERE tenant_id IS NULL AND status='active'`); err != nil {
				return err
			}
		} else {
			if _, err := tx.Exec(ctx, `UPDATE rate_cards SET status='superseded', updated_at=now()
				WHERE tenant_id=$1 AND status='active'`, *rc.TenantID); err != nil {
				return err
			}
		}
		if _, err := tx.Exec(ctx, `UPDATE rate_cards SET status='active', updated_at=now() WHERE id=$1`, id); err != nil {
			return err
		}
		rc.Status = domain.RateCardActive
		env := events.NewEnvelope(events.EvRateCardActivated, op, domain.RateCardURN(id), map[string]any{
			"rate_card_id": id.String(), "tenant_id": tenantStr(rc.TenantID),
			"version": rc.Version, "effective_from": rc.EffectiveFrom.Format("2006-01-02"),
		})
		return insertOutbox(ctx, tx, env)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.RateCard{}, domain.ErrNotFound
	}
	return rc, err
}

// ListRateCards returns cards visible to the caller (default + own tenant, or
// all under platform scope).
func (s *PG) ListRateCards(ctx context.Context, op domain.Op) ([]domain.RateCard, error) {
	var out []domain.RateCard
	run := s.withTenant
	scope := func(fn func(tx pgx.Tx) error) error { return run(ctx, op.Tenant, fn) }
	if op.Platform {
		scope = func(fn func(tx pgx.Tx) error) error { return s.withPlatform(ctx, fn) }
	}
	err := scope(func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, version, effective_from, status, created_at
			FROM rate_cards ORDER BY created_at DESC`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var rc domain.RateCard
			if err := rows.Scan(&rc.ID, &rc.TenantID, &rc.Version, &rc.EffectiveFrom, &rc.Status, &rc.CreatedAt); err != nil {
				return err
			}
			out = append(out, rc)
		}
		return rows.Err()
	})
	if err != nil {
		return nil, err
	}
	for i := range out {
		items, err := s.rateCardItems(ctx, op, out[i].ID)
		if err != nil {
			return nil, err
		}
		out[i].Items = items
	}
	return out, nil
}

func (s *PG) rateCardItems(ctx context.Context, op domain.Op, id uuid.UUID) (map[string]float64, error) {
	items := map[string]float64{}
	fn := func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT meter_key, price_per_unit_usd FROM rate_card_items WHERE rate_card_id=$1`, id)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var mk string
			var price float64
			if err := rows.Scan(&mk, &price); err != nil {
				return err
			}
			items[mk] = price
		}
		return rows.Err()
	}
	var err error
	if op.Platform {
		err = s.withPlatform(ctx, fn)
	} else {
		err = s.withTenant(ctx, op.Tenant, fn)
	}
	return items, err
}

// defaultPricesTx returns the active default (platform) rate card's prices.
func defaultPricesTx(ctx context.Context, tx pgx.Tx) (map[string]float64, error) {
	prices := map[string]float64{}
	rows, err := tx.Query(ctx, `
		SELECT i.meter_key, i.price_per_unit_usd
		FROM rate_cards rc JOIN rate_card_items i ON i.rate_card_id = rc.id
		WHERE rc.tenant_id IS NULL AND rc.status='active'`)
	if err != nil {
		return prices, err
	}
	defer rows.Close()
	for rows.Next() {
		var mk string
		var price float64
		if err := rows.Scan(&mk, &price); err != nil {
			return prices, err
		}
		prices[mk] = price
	}
	return prices, rows.Err()
}

// ResolvePrices returns the effective price per meter for a tenant at a given
// usage time: tenant override active-at-time if present, else default
// active-at-time (USG-FR-042, BR-5). Returns the resolved prices and the
// rate_card_id used per meter.
func (s *PG) ResolvePrices(ctx context.Context, tenant uuid.UUID, at time.Time) (map[string]float64, map[string]uuid.UUID, error) {
	prices := map[string]float64{}
	cardOf := map[string]uuid.UUID{}
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		// Default card effective at `at`.
		defID, defItems, err := effectiveCard(ctx, tx, nil, at)
		if err != nil {
			return err
		}
		for mk, p := range defItems {
			prices[mk] = p
			cardOf[mk] = defID
		}
		// Tenant override effective at `at` wins.
		ovID, ovItems, err := effectiveCard(ctx, tx, &tenant, at)
		if err != nil {
			return err
		}
		for mk, p := range ovItems {
			prices[mk] = p
			cardOf[mk] = ovID
		}
		return nil
	})
	return prices, cardOf, err
}

func effectiveCard(ctx context.Context, tx pgx.Tx, tenant *uuid.UUID, at time.Time) (uuid.UUID, map[string]float64, error) {
	items := map[string]float64{}
	var id uuid.UUID
	var q string
	var args []any
	if tenant == nil {
		q = `SELECT id FROM rate_cards WHERE tenant_id IS NULL AND status IN ('active','superseded')
		     AND effective_from <= $1 ORDER BY effective_from DESC, version DESC LIMIT 1`
		args = []any{at}
	} else {
		q = `SELECT id FROM rate_cards WHERE tenant_id=$1 AND status IN ('active','superseded')
		     AND effective_from <= $2 ORDER BY effective_from DESC, version DESC LIMIT 1`
		args = []any{*tenant, at}
	}
	if err := tx.QueryRow(ctx, q, args...).Scan(&id); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return uuid.Nil, items, nil
		}
		return uuid.Nil, items, err
	}
	rows, err := tx.Query(ctx, `SELECT meter_key, price_per_unit_usd FROM rate_card_items WHERE rate_card_id=$1`, id)
	if err != nil {
		return id, items, err
	}
	defer rows.Close()
	for rows.Next() {
		var mk string
		var p float64
		if err := rows.Scan(&mk, &p); err != nil {
			return id, items, err
		}
		items[mk] = p
	}
	return id, items, rows.Err()
}

func tenantStr(t *uuid.UUID) any {
	if t == nil {
		return nil
	}
	return t.String()
}
