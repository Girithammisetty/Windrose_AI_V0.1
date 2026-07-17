package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/usage-service/internal/budget"
	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/events"
)

// svcActor is the system actor for service-generated events.
func svcActor() domain.Actor { return domain.Actor{Type: "service", ID: "usage-service"} }

// CreateBudget inserts a budget and emits budget.created (USG-FR-030).
func (s *PG) CreateBudget(ctx context.Context, op domain.Op, b domain.Budget) (domain.Budget, error) {
	b.ID = domain.NewID()
	b.TenantID = op.Tenant
	b.Status = domain.BudgetActive
	now := time.Now().UTC()
	b.CreatedAt, b.UpdatedAt = now, now
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO budgets (id, tenant_id, scope_workspace_id, scope_user_id,
			   scope_agent_id, meter_key, budget_window, limit_value, action_at_100, status,
			   created_at, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
			b.ID, b.TenantID, b.WorkspaceID, b.UserID, b.AgentID, b.MeterKey,
			b.Window, b.LimitValue, b.ActionAt100, b.Status, b.CreatedAt, b.UpdatedAt); err != nil {
			return err
		}
		env := events.NewEnvelope(events.EvBudgetCreated, op, domain.BudgetURN(op.Tenant, b.ID), budgetPayload(b))
		return insertOutbox(ctx, tx, env)
	})
	return b, err
}

// GetBudget returns one budget or ErrNotFound (RLS makes cross-tenant reads
// return no rows → 404, AC-10).
func (s *PG) GetBudget(ctx context.Context, tenant, id uuid.UUID) (domain.Budget, error) {
	var b domain.Budget
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return scanBudget(tx.QueryRow(ctx, budgetSelect+` WHERE id=$1`, id), &b)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Budget{}, domain.ErrNotFound
	}
	return b, err
}

// ListBudgets returns active+deleted budgets for a tenant (cursor by id).
func (s *PG) ListBudgets(ctx context.Context, tenant uuid.UUID, after uuid.UUID, limit int) ([]domain.Budget, error) {
	var out []domain.Budget
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, budgetSelect+`
			WHERE ($1::uuid = '00000000-0000-0000-0000-000000000000' OR id > $1)
			ORDER BY id LIMIT $2`, after, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var b domain.Budget
			if err := scanBudget(rows, &b); err != nil {
				return err
			}
			out = append(out, b)
		}
		return rows.Err()
	})
	return out, err
}

// UpdateBudget patches limit and/or action_at_100. Raising the limit above
// current consumption emits budget.reset (USG-FR-033).
func (s *PG) UpdateBudget(ctx context.Context, op domain.Op, id uuid.UUID, newLimit *float64, newAction *string) (domain.Budget, error) {
	var b domain.Budget
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		if err := scanBudget(tx.QueryRow(ctx, budgetSelect+` WHERE id=$1 FOR UPDATE`, id), &b); err != nil {
			return err
		}
		before := b
		if newLimit != nil {
			b.LimitValue = *newLimit
		}
		if newAction != nil {
			b.ActionAt100 = *newAction
		}
		b.UpdatedAt = time.Now().UTC()
		if _, err := tx.Exec(ctx, `UPDATE budgets SET limit_value=$1, action_at_100=$2, updated_at=$3 WHERE id=$4`,
			b.LimitValue, b.ActionAt100, b.UpdatedAt, id); err != nil {
			return err
		}
		// Limit raised above current consumption → reset the window state.
		if newLimit != nil && *newLimit > before.LimitValue {
			bounds := budget.WindowBounds(b.Window, time.Now())
			consumed, _ := consumptionTx(ctx, tx, s, b, bounds)
			if consumed < *newLimit {
				if err := resetStateTx(ctx, tx, op, b, bounds.WindowStart, "limit_raised"); err != nil {
					return err
				}
			}
		}
		env := events.NewEnvelope(events.EvBudgetUpdated, op, domain.BudgetURN(op.Tenant, id), budgetPayload(b))
		return insertOutbox(ctx, tx, env)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Budget{}, domain.ErrNotFound
	}
	return b, err
}

// DeleteBudget soft-deletes; if currently exhausted emits budget.reset
// (USG-FR-034), then budget.deleted.
func (s *PG) DeleteBudget(ctx context.Context, op domain.Op, id uuid.UUID) error {
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		var b domain.Budget
		if err := scanBudget(tx.QueryRow(ctx, budgetSelect+` WHERE id=$1 FOR UPDATE`, id), &b); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `UPDATE budgets SET status='deleted', updated_at=now() WHERE id=$1`, id); err != nil {
			return err
		}
		bounds := budget.WindowBounds(b.Window, time.Now())
		var lastThreshold int
		_ = tx.QueryRow(ctx, `SELECT last_threshold FROM budget_states WHERE budget_id=$1 AND window_start=$2`,
			id, bounds.WindowStart).Scan(&lastThreshold)
		if lastThreshold >= 100 {
			if err := resetStateTx(ctx, tx, op, b, bounds.WindowStart, "budget_deleted"); err != nil {
				return err
			}
		}
		env := events.NewEnvelope(events.EvBudgetDeleted, op, domain.BudgetURN(op.Tenant, id), budgetPayload(b))
		return insertOutbox(ctx, tx, env)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.ErrNotFound
	}
	return err
}

// EvaluateAfterIngest evaluates every active budget for the tenant whose meter
// is among the just-ingested keys (or a usd_total budget) — USG-FR-031.
func (s *PG) EvaluateAfterIngest(ctx context.Context, tenant uuid.UUID, meterKeys map[string]bool) error {
	budgets, err := s.activeBudgets(ctx, tenant)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	for _, b := range budgets {
		if b.MeterKey != "usd_total" && !meterKeys[b.MeterKey] {
			continue
		}
		if err := s.evaluateBudget(ctx, tenant, b, now); err != nil {
			return err
		}
	}
	return nil
}

// EvaluateAll evaluates every active budget for a tenant (periodic sweep /
// rollup-driven path, USG-FR-031 + BR-12 fallback).
func (s *PG) EvaluateAll(ctx context.Context, tenant uuid.UUID) error {
	budgets, err := s.activeBudgets(ctx, tenant)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	for _, b := range budgets {
		if err := s.evaluateBudget(ctx, tenant, b, now); err != nil {
			return err
		}
	}
	return nil
}

// TenantsWithBudgets lists tenants that have at least one active budget (sweep).
func (s *PG) TenantsWithBudgets(ctx context.Context) ([]uuid.UUID, error) {
	var out []uuid.UUID
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT DISTINCT tenant_id FROM budgets WHERE status='active'`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id uuid.UUID
			if err := rows.Scan(&id); err != nil {
				return err
			}
			out = append(out, id)
		}
		return rows.Err()
	})
	return out, err
}

func (s *PG) activeBudgets(ctx context.Context, tenant uuid.UUID) ([]domain.Budget, error) {
	var out []domain.Budget
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, budgetSelect+` WHERE status='active'`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var b domain.Budget
			if err := scanBudget(rows, &b); err != nil {
				return err
			}
			out = append(out, b)
		}
		return rows.Err()
	})
	return out, err
}

// evaluateBudget evaluates one budget within its own tx. Concurrent evaluators
// (EvaluateAfterIngest racing the periodic SweepBudgets) are serialized by
// FIRST materializing the state row with INSERT … ON CONFLICT DO NOTHING and
// THEN taking SELECT … FOR UPDATE on it. Because a concurrent INSERT of the
// same (budget_id, window_start) blocks until the first evaluator's txn commits,
// the second evaluator reads the first's committed last_threshold and sees no
// new crossing — so threshold events fire exactly once per (budget, window,
// threshold), even on the FIRST crossing when no row existed yet (BR-1, AC-3).
func (s *PG) evaluateBudget(ctx context.Context, tenant uuid.UUID, b domain.Budget, now time.Time) error {
	op := domain.Op{Tenant: tenant, Actor: svcActor()}
	bounds := budget.WindowBounds(b.Window, now)
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		// Materialize the current window instance's state row. RowsAffected==1
		// means THIS evaluator created it; a concurrent creator blocks here
		// until we commit, then reads our result (no duplicate emission).
		tag, err := tx.Exec(ctx, `
			INSERT INTO budget_states (budget_id, tenant_id, window_start, consumed, last_threshold)
			VALUES ($1,$2,$3,0,0) ON CONFLICT (budget_id, window_start) DO NOTHING`,
			b.ID, tenant, bounds.WindowStart)
		if err != nil {
			return err
		}
		createdRow := tag.RowsAffected() == 1

		// Lock the row for this window instance (now guaranteed to exist).
		var last int
		if err := tx.QueryRow(ctx, `
			SELECT last_threshold FROM budget_states
			WHERE budget_id=$1 AND window_start=$2 FOR UPDATE`,
			b.ID, bounds.WindowStart).Scan(&last); err != nil {
			return err
		}

		// New window instance while a prior instance exists → reset emitted
		// (window rollover, AC-5). Only the evaluator that created the row runs
		// this, so the reset fires exactly once.
		if createdRow {
			var priorExists bool
			_ = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM budget_states WHERE budget_id=$1 AND window_start < $2)`,
				b.ID, bounds.WindowStart).Scan(&priorExists)
			if priorExists {
				env := events.NewEnvelope(events.EvBudgetReset, op, domain.BudgetURN(tenant, b.ID),
					map[string]any{"budget_id": b.ID.String(), "window_start": bounds.WindowStart.Format(time.RFC3339), "reason": "window_rollover"})
				if err := insertOutbox(ctx, tx, env); err != nil {
					return err
				}
			}
		}

		consumed, err := consumptionTx(ctx, tx, s, b, bounds)
		if err != nil {
			return err
		}
		crossed, newLast := budget.CrossedThresholds(last, consumed, b.LimitValue)

		var exhaustedAt *time.Time
		for _, t := range crossed {
			payload := map[string]any{
				"budget_id":    b.ID.String(),
				"scope":        scopeMap(b),
				"meter_key":    b.MeterKey,
				"threshold":    t,
				"consumed":     consumed,
				"limit":        b.LimitValue,
				"window_start": bounds.WindowStart.Format(time.RFC3339),
			}
			evType := events.EvBudgetThreshold
			if t >= 100 {
				evType = events.EvBudgetExhausted
				payload["action"] = b.ActionAt100
				now := time.Now().UTC()
				exhaustedAt = &now
			}
			env := events.NewEnvelope(evType, op, domain.BudgetURN(tenant, b.ID), payload)
			if err := insertOutbox(ctx, tx, env); err != nil {
				return err
			}
		}

		// Upsert the state row.
		_, err = tx.Exec(ctx, `
			INSERT INTO budget_states (budget_id, tenant_id, window_start, consumed, last_threshold, exhausted_at, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6, now())
			ON CONFLICT (budget_id, window_start) DO UPDATE SET
			  consumed=EXCLUDED.consumed,
			  last_threshold=GREATEST(budget_states.last_threshold, EXCLUDED.last_threshold),
			  exhausted_at=COALESCE(budget_states.exhausted_at, EXCLUDED.exhausted_at),
			  updated_at=now()`,
			b.ID, tenant, bounds.WindowStart, consumed, newLast, exhaustedAt)
		return err
	})
}

// resetStateTx emits budget.reset and zeroes the current window state.
func resetStateTx(ctx context.Context, tx pgx.Tx, op domain.Op, b domain.Budget, windowStart time.Time, reason string) error {
	if _, err := tx.Exec(ctx, `
		INSERT INTO budget_states (budget_id, tenant_id, window_start, consumed, last_threshold, exhausted_at, updated_at)
		VALUES ($1,$2,$3,0,0,NULL, now())
		ON CONFLICT (budget_id, window_start) DO UPDATE SET consumed=0, last_threshold=0, exhausted_at=NULL, updated_at=now()`,
		b.ID, op.Tenant, windowStart); err != nil {
		return err
	}
	env := events.NewEnvelope(events.EvBudgetReset, op, domain.BudgetURN(op.Tenant, b.ID),
		map[string]any{"budget_id": b.ID.String(), "window_start": windowStart.Format(time.RFC3339), "reason": reason})
	return insertOutbox(ctx, tx, env)
}

// consumptionTx computes current window consumption for a budget. For a concrete
// meter it sums raw quantity; for usd_total it prices every meter via the
// default active rate card (BR-9, simplified: default card only).
func consumptionTx(ctx context.Context, tx pgx.Tx, s *PG, b domain.Budget, bounds budget.Bounds) (float64, error) {
	if b.MeterKey != "usd_total" {
		var total float64
		err := tx.QueryRow(ctx, `
			SELECT COALESCE(SUM(quantity),0)::float8 FROM usage_raw
			WHERE tenant_id=$1 AND meter_key=$2 AND time >= $3 AND time < $4
			  AND ($5::text IS NULL OR workspace_id=$5)
			  AND ($6::text IS NULL OR user_id=$6)
			  AND ($7::text IS NULL OR agent_id=$7)`,
			b.TenantID, b.MeterKey, bounds.RangeStart, bounds.RangeEnd,
			b.WorkspaceID, b.UserID, b.AgentID).Scan(&total)
		return total, err
	}
	// usd_total: sum quantity*price per meter.
	prices, err := defaultPricesTx(ctx, tx)
	if err != nil {
		return 0, err
	}
	rows, err := tx.Query(ctx, `
		SELECT meter_key, COALESCE(SUM(quantity),0)::float8 FROM usage_raw
		WHERE tenant_id=$1 AND time >= $2 AND time < $3
		  AND ($4::text IS NULL OR workspace_id=$4)
		  AND ($5::text IS NULL OR user_id=$5)
		  AND ($6::text IS NULL OR agent_id=$6)
		GROUP BY meter_key`,
		b.TenantID, bounds.RangeStart, bounds.RangeEnd, b.WorkspaceID, b.UserID, b.AgentID)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	var usd float64
	for rows.Next() {
		var mk string
		var qty float64
		if err := rows.Scan(&mk, &qty); err != nil {
			return 0, err
		}
		usd += qty * prices[mk]
	}
	return usd, rows.Err()
}

// GetBudgetState returns the current window state (gateway resync, USG-FR-032).
func (s *PG) GetBudgetState(ctx context.Context, tenant, id uuid.UUID) (domain.Budget, domain.BudgetState, error) {
	b, err := s.GetBudget(ctx, tenant, id)
	if err != nil {
		return domain.Budget{}, domain.BudgetState{}, err
	}
	bounds := budget.WindowBounds(b.Window, time.Now())
	st := domain.BudgetState{BudgetID: id, WindowStart: bounds.WindowStart}
	err = s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT consumed::float8, last_threshold, exhausted_at FROM budget_states
			WHERE budget_id=$1 AND window_start=$2`, id, bounds.WindowStart).
			Scan(&st.Consumed, &st.LastThreshold, &st.ExhaustedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return b, st, nil // no consumption yet this window
	}
	return b, st, err
}

// budgetSelect / scanBudget / helpers.
const budgetSelect = `SELECT id, tenant_id, scope_workspace_id, scope_user_id, scope_agent_id,
	meter_key, budget_window, limit_value, action_at_100, status, created_at, updated_at FROM budgets`

type rowScanner interface {
	Scan(dest ...any) error
}

func scanBudget(r rowScanner, b *domain.Budget) error {
	return r.Scan(&b.ID, &b.TenantID, &b.WorkspaceID, &b.UserID, &b.AgentID,
		&b.MeterKey, &b.Window, &b.LimitValue, &b.ActionAt100, &b.Status, &b.CreatedAt, &b.UpdatedAt)
}

func scopeMap(b domain.Budget) map[string]any {
	m := map[string]any{"tenant_id": b.TenantID.String()}
	if b.WorkspaceID != nil {
		m["workspace_id"] = *b.WorkspaceID
	}
	if b.UserID != nil {
		m["user_id"] = *b.UserID
	}
	if b.AgentID != nil {
		m["agent_id"] = *b.AgentID
	}
	return m
}

func budgetPayload(b domain.Budget) map[string]any {
	return map[string]any{
		"budget_id":     b.ID.String(),
		"scope":         scopeMap(b),
		"meter_key":     b.MeterKey,
		"window":        b.Window,
		"limit_value":   b.LimitValue,
		"action_at_100": b.ActionAt100,
		"status":        b.Status,
		"thresholds":    domain.Thresholds,
	}
}
