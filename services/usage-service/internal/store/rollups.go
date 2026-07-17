package store

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/usage-service/internal/domain"
)

// RefreshRollups recomputes raw→hourly→daily→monthly for all buckets touched
// since `since` (covers 48h late events, USG-FR-014). Idempotent: re-running
// yields identical totals (AC-12). Finalized daily/monthly buckets are not
// overwritten in place (USG-FR-021); late events reopen them via reconciliation.
func (s *PG) RefreshRollups(ctx context.Context, since time.Time) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO usage_hourly
			  (bucket, tenant_id, meter_key, workspace_id, user_id, agent_id, model, cloud, quantity_sum, refreshed_at)
			SELECT date_trunc('hour', time), tenant_id, meter_key,
			       COALESCE(workspace_id,''), COALESCE(user_id,''), COALESCE(agent_id,''),
			       COALESCE(model,''), COALESCE(cloud,''), SUM(quantity), now()
			FROM usage_raw WHERE time >= $1
			GROUP BY 1,2,3,4,5,6,7,8
			ON CONFLICT (tenant_id, meter_key, bucket, workspace_id, user_id, agent_id, model, cloud)
			DO UPDATE SET quantity_sum=EXCLUDED.quantity_sum, refreshed_at=now()`, since); err != nil {
			return fmt.Errorf("hourly refresh: %w", err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO usage_daily
			  (bucket, tenant_id, meter_key, workspace_id, user_id, agent_id, model, cloud, quantity_sum, refreshed_at)
			SELECT time::date, tenant_id, meter_key,
			       COALESCE(workspace_id,''), COALESCE(user_id,''), COALESCE(agent_id,''),
			       COALESCE(model,''), COALESCE(cloud,''), SUM(quantity), now()
			FROM usage_raw WHERE time >= $1
			GROUP BY 1,2,3,4,5,6,7,8
			ON CONFLICT (tenant_id, meter_key, bucket, workspace_id, user_id, agent_id, model, cloud)
			DO UPDATE SET quantity_sum=EXCLUDED.quantity_sum, refreshed_at=now()
			WHERE usage_daily.finalized_at IS NULL`, since); err != nil {
			return fmt.Errorf("daily refresh: %w", err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO usage_monthly
			  (bucket, tenant_id, meter_key, workspace_id, user_id, agent_id, model, cloud, quantity_sum, refreshed_at)
			SELECT date_trunc('month', time)::date, tenant_id, meter_key,
			       COALESCE(workspace_id,''), COALESCE(user_id,''), COALESCE(agent_id,''),
			       COALESCE(model,''), COALESCE(cloud,''), SUM(quantity), now()
			FROM usage_raw WHERE time >= $1
			GROUP BY 1,2,3,4,5,6,7,8
			ON CONFLICT (tenant_id, meter_key, bucket, workspace_id, user_id, agent_id, model, cloud)
			DO UPDATE SET quantity_sum=EXCLUDED.quantity_sum, refreshed_at=now()
			WHERE usage_monthly.finalized_at IS NULL`, since); err != nil {
			return fmt.Errorf("monthly refresh: %w", err)
		}
		return nil
	})
}

// FinalizeMonth marks a month's monthly buckets immutable (USG-FR-021). Runs
// after a full refresh. Idempotent and re-runnable (BR-10).
func (s *PG) FinalizeMonth(ctx context.Context, month string) error {
	first, err := time.Parse("2006-01", month)
	if err != nil {
		return domain.ErrValidation
	}
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE usage_monthly SET finalized_at=now() WHERE bucket=$1 AND finalized_at IS NULL`, first)
		return err
	})
}

// EnforceRetention drops rows past each tier's retention (USG-FR-022).
func (s *PG) EnforceRetention(ctx context.Context, now time.Time) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `DELETE FROM usage_raw WHERE time < $1`, now.AddDate(0, 0, -90)); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `DELETE FROM usage_hourly WHERE bucket < $1`, now.AddDate(0, -13, 0)); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `DELETE FROM usage_daily WHERE bucket < $1`, now.AddDate(-3, 0, 0)); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `DELETE FROM usage_monthly WHERE bucket < $1`, now.AddDate(-7, 0, 0)); err != nil {
			return err
		}
		return nil
	})
}

// ShowbackQuery parameterizes GET /reports/usage (USG-FR-040).
type ShowbackQuery struct {
	GroupBy     []string // subset of tenant|workspace|user|agent|meter|model|day|month
	From        time.Time
	To          time.Time
	MeterKey    string
	WorkspaceID string
	Limit       int
	Offset      int
}

var groupCols = map[string]string{
	"workspace": "NULLIF(workspace_id,'')",
	"user":      "NULLIF(user_id,'')",
	"agent":     "NULLIF(agent_id,'')",
	"meter":     "meter_key",
	"model":     "NULLIF(model,'')",
	"day":       "bucket::text",
	"month":     "to_char(bucket,'YYYY-MM')",
}

// QueryUsage runs a showback aggregation over usage_daily (USG-FR-040). Values
// are meter-unit sums; USD is layered by the API when a rate card exists.
func (s *PG) QueryUsage(ctx context.Context, tenant uuid.UUID, q ShowbackQuery) ([]domain.RollupRow, error) {
	if q.Limit <= 0 {
		q.Limit = 50
	}
	selCols := []string{}
	groupIdx := []string{}
	for i, g := range q.GroupBy {
		col, ok := groupCols[g]
		if !ok {
			return nil, domain.ErrValidation
		}
		selCols = append(selCols, col+" AS g"+fmt.Sprint(i))
		groupIdx = append(groupIdx, fmt.Sprint(i+1))
	}
	sel := strings.Join(selCols, ", ")
	if sel != "" {
		sel += ", "
	}
	sql := `SELECT ` + sel + `meter_key, SUM(quantity_sum)::float8 AS qty
		FROM usage_daily
		WHERE tenant_id=$1 AND bucket >= $2 AND bucket <= $3
		  AND ($4='' OR meter_key=$4)
		  AND ($5='' OR workspace_id=$5)
		GROUP BY ` + groupByClause(groupIdx) + `
		ORDER BY qty DESC
		LIMIT $6 OFFSET $7`

	var out []domain.RollupRow
	units := unitByMeter()
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, sql, tenant, q.From, q.To, q.MeterKey, q.WorkspaceID, q.Limit, q.Offset)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			vals, err := rows.Values()
			if err != nil {
				return err
			}
			row := domain.RollupRow{}
			// group cols first, then meter_key, then qty
			for i, g := range q.GroupBy {
				sv := toStrPtr(vals[i])
				switch g {
				case "workspace":
					row.WorkspaceID = sv
				case "user":
					row.UserID = sv
				case "agent":
					row.AgentID = sv
				case "model":
					row.Model = sv
				case "meter":
					if sv != nil {
						row.MeterKey = *sv
					}
				case "day":
					row.Day = sv
				case "month":
					row.Month = sv
				}
			}
			mk, _ := vals[len(q.GroupBy)].(string)
			if row.MeterKey == "" {
				row.MeterKey = mk
			}
			row.Quantity = toF(vals[len(q.GroupBy)+1])
			row.Unit = units[row.MeterKey]
			out = append(out, row)
		}
		return rows.Err()
	})
	return out, err
}

func groupByClause(idx []string) string {
	// Always group by the trailing meter_key column too.
	all := append([]string{}, idx...)
	all = append(all, fmt.Sprint(len(idx)+1)) // meter_key position
	return strings.Join(all, ", ")
}

// ChargebackLine is one priced monthly row (USG-FR-043).
type ChargebackLine struct {
	TenantID      uuid.UUID `json:"tenant_id"`
	WorkspaceID   *string   `json:"workspace_id,omitempty"`
	Month         string    `json:"month"`
	MeterKey      string    `json:"meter_key"`
	Quantity      float64   `json:"quantity"`
	RateCardID    string    `json:"rate_card_id"`
	PricePerUnit  float64   `json:"price_per_unit_usd"`
	USD           float64   `json:"usd"`
	AdjustmentsUSD float64  `json:"adjustments_usd"`
	TotalUSD      float64   `json:"total_usd"`
}

// Chargeback returns priced monthly rollups for a tenant (USG-FR-043). Prices
// resolve at month start (BR-5). Adjustments are folded in as distinct deltas.
func (s *PG) Chargeback(ctx context.Context, tenant uuid.UUID, month string) ([]ChargebackLine, error) {
	first, err := time.Parse("2006-01", month)
	if err != nil {
		return nil, domain.ErrValidation
	}
	prices, cardOf, err := s.ResolvePrices(ctx, tenant, first)
	if err != nil {
		return nil, err
	}
	adj, err := s.adjustmentsUSD(ctx, tenant, month)
	if err != nil {
		return nil, err
	}
	var out []ChargebackLine
	err = s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT NULLIF(workspace_id,''), meter_key, SUM(quantity_sum)::float8
			FROM usage_monthly WHERE tenant_id=$1 AND bucket=$2
			GROUP BY 1,2 ORDER BY 2`, tenant, first)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var ws *string
			var mk string
			var qty float64
			if err := rows.Scan(&ws, &mk, &qty); err != nil {
				return err
			}
			price := prices[mk]
			line := ChargebackLine{
				TenantID: tenant, WorkspaceID: ws, Month: month, MeterKey: mk,
				Quantity: qty, PricePerUnit: price, USD: qty * price,
				AdjustmentsUSD: adj[mk],
			}
			if id, ok := cardOf[mk]; ok && id != uuid.Nil {
				line.RateCardID = id.String()
			}
			line.TotalUSD = line.USD + line.AdjustmentsUSD
			out = append(out, line)
		}
		return rows.Err()
	})
	return out, err
}

// DailyTotals returns per-day totals for a (tenant, meter) over [from,to]
// (anomaly detection input, USG-FR-050).
func (s *PG) DailyTotals(ctx context.Context, tenant uuid.UUID, meter string, from, to time.Time) (map[string]float64, error) {
	out := map[string]float64{}
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT bucket, SUM(quantity_sum)::float8 FROM usage_daily
			WHERE tenant_id=$1 AND meter_key=$2 AND bucket >= $3 AND bucket <= $4
			GROUP BY bucket ORDER BY bucket`, tenant, meter, from, to)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var d time.Time
			var q float64
			if err := rows.Scan(&d, &q); err != nil {
				return err
			}
			out[d.Format("2006-01-02")] = q
		}
		return rows.Err()
	})
	return out, err
}

// TenantsWithUsage lists tenants that have daily rollup data (anomaly scan).
func (s *PG) TenantsWithUsage(ctx context.Context) ([]uuid.UUID, error) {
	var out []uuid.UUID
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT DISTINCT tenant_id FROM usage_daily`)
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

// MetersWithUsage lists meter keys a tenant has daily data for (anomaly scan).
func (s *PG) MetersWithUsage(ctx context.Context, tenant uuid.UUID) ([]string, error) {
	var out []string
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT DISTINCT meter_key FROM usage_daily WHERE tenant_id=$1`, tenant)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var mk string
			if err := rows.Scan(&mk); err != nil {
				return err
			}
			out = append(out, mk)
		}
		return rows.Err()
	})
	return out, err
}

func unitByMeter() map[string]string {
	m := map[string]string{}
	for _, e := range domain.Catalog() {
		m[e.MeterKey] = e.Unit
	}
	return m
}

func toStrPtr(v any) *string {
	if v == nil {
		return nil
	}
	switch s := v.(type) {
	case string:
		if s == "" {
			return nil
		}
		return &s
	case []byte:
		str := string(s)
		return &str
	}
	str := fmt.Sprint(v)
	return &str
}

func toF(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case float32:
		return float64(n)
	case int64:
		return float64(n)
	case int:
		return float64(n)
	}
	// pgx numeric may arrive as pgtype.Numeric-compatible; fall back via fmt.
	var f float64
	_, _ = fmt.Sscanf(fmt.Sprint(v), "%g", &f)
	return f
}
