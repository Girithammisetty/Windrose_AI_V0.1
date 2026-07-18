// Package store is chart-service's real Postgres persistence layer. Every
// tenant-scoped statement runs inside a transaction that first sets the
// app.tenant_id GUC so Postgres RLS (migration 000002) enforces isolation —
// the service never filters by a tenant taken from request input
// (MASTER-FR-001/002). The runtime pool connects as the non-owner, NOBYPASSRLS
// chart_app role, so RLS is authoritative even for this code.
package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/windrose-ai/chart-service/internal/domain"
	"github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/go-common/outbox"
)

// PG is the Postgres-backed store.
type PG struct{ pool *pgxpool.Pool }

// NewPG wraps a pgx pool.
func NewPG(pool *pgxpool.Pool) *PG { return &PG{pool: pool} }

// Ping checks connectivity (readyz).
func (s *PG) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

func (s *PG) withTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return err
		}
		return fn(tx)
	})
}

func (s *PG) withPlatform(ctx context.Context, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.role', 'platform', true)`); err != nil {
			return err
		}
		return fn(tx)
	})
}

func isUnique(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

func insertOutboxTx(ctx context.Context, tx pgx.Tx, envs []event.Envelope) error {
	for _, e := range envs {
		raw, err := json.Marshal(e)
		if err != nil {
			return err
		}
		if _, err := tx.Exec(ctx,
			`INSERT INTO outbox (tenant_id, event_id, event_type, resource_urn, envelope)
			 VALUES ($1,$2,$3,$4,$5) ON CONFLICT (event_id) DO NOTHING`,
			e.TenantID, e.EventID, e.EventType, e.ResourceURN, raw); err != nil {
			return err
		}
	}
	return nil
}

// ---------- Dashboards ----------

// CreateDashboard inserts a dashboard + outbox events atomically.
func (s *PG) CreateDashboard(ctx context.Context, d *domain.Dashboard, envs []event.Envelope) error {
	return s.withTenant(ctx, d.TenantID, func(tx pgx.Tx) error {
		// tags is `TEXT[] NOT NULL DEFAULT '{}'`, but a nil Go slice binds to SQL
		// NULL (the column DEFAULT only applies when the column is omitted, not
		// when NULL is passed explicitly), which violates the constraint. Callers
		// that don't set tags — e.g. an approved dashboard-designer proposal —
		// would otherwise fail the INSERT. Coalesce to an empty array.
		tags := d.Tags
		if tags == nil {
			tags = []string{}
		}
		_, err := tx.Exec(ctx,
			`INSERT INTO dashboards (id,tenant_id,workspace_id,name,module,description,layout,meta,tags,owner_user_id,status,archived)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,false)`,
			d.ID, d.TenantID, d.WorkspaceID, d.Name, d.Module, d.Description,
			jsonOr(d.Layout, "[]"), jsonOr(d.Meta, "{}"), tags, d.OwnerUserID, statusOr(d.Status))
		if err != nil {
			if isUnique(err) {
				return domain.EConflict("dashboard name already exists in this workspace/module")
			}
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// GetDashboard loads a dashboard (RLS scopes it to the tenant → cross-tenant is
// a not-found, MASTER-FR-003) and computes last_content_updated_at.
func (s *PG) GetDashboard(ctx context.Context, tenant, id uuid.UUID) (*domain.Dashboard, error) {
	var d *domain.Dashboard
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var got domain.Dashboard
		err := tx.QueryRow(ctx,
			`SELECT id,tenant_id,workspace_id,name,module,description,layout,meta,tags,owner_user_id,status,archived,archived_at,created_at,updated_at
			 FROM dashboards WHERE id=$1 AND deleted_at IS NULL`, id).
			Scan(&got.ID, &got.TenantID, &got.WorkspaceID, &got.Name, &got.Module, &got.Description,
				&got.Layout, &got.Meta, &got.Tags, &got.OwnerUserID, &got.Status, &got.Archived,
				&got.ArchivedAt, &got.CreatedAt, &got.UpdatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.ENotFound("dashboard not found")
		}
		if err != nil {
			return err
		}
		// last_content_updated_at = max over dashboard/charts/docs (CHART-FR-007).
		last := got.UpdatedAt
		var cmax, dmax *time.Time
		_ = tx.QueryRow(ctx, `SELECT max(updated_at) FROM charts WHERE dashboard_id=$1 AND deleted_at IS NULL`, id).Scan(&cmax)
		_ = tx.QueryRow(ctx, `SELECT max(updated_at) FROM documentations WHERE documentable_type='dashboard' AND documentable_id=$1`, id).Scan(&dmax)
		if cmax != nil && cmax.After(last) {
			last = *cmax
		}
		if dmax != nil && dmax.After(last) {
			last = *dmax
		}
		got.LastContent = &last
		d = &got
		return nil
	})
	return d, err
}

// ListDashboards lists non-deleted dashboards for a workspace with filters +
// keyset pagination (MASTER-FR-022).
func (s *PG) ListDashboards(ctx context.Context, tenant, workspace uuid.UUID, module string, archived bool, tag string, limit int, after *uuid.UUID) ([]domain.Dashboard, error) {
	var out []domain.Dashboard
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := strings.Builder{}
		q.WriteString(`SELECT id,tenant_id,workspace_id,name,module,description,layout,meta,tags,owner_user_id,status,archived,archived_at,created_at,updated_at
			FROM dashboards WHERE deleted_at IS NULL AND workspace_id=$1 AND archived=$2`)
		args := []any{workspace, archived}
		if module != "" {
			args = append(args, module)
			fmt.Fprintf(&q, " AND module=$%d", len(args))
		}
		if tag != "" {
			args = append(args, tag)
			fmt.Fprintf(&q, " AND $%d = ANY(tags)", len(args))
		}
		if after != nil {
			args = append(args, *after)
			fmt.Fprintf(&q, " AND id > $%d", len(args))
		}
		args = append(args, limit)
		fmt.Fprintf(&q, " ORDER BY id ASC LIMIT $%d", len(args))
		rows, err := tx.Query(ctx, q.String(), args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var d domain.Dashboard
			if err := rows.Scan(&d.ID, &d.TenantID, &d.WorkspaceID, &d.Name, &d.Module, &d.Description,
				&d.Layout, &d.Meta, &d.Tags, &d.OwnerUserID, &d.Status, &d.Archived, &d.ArchivedAt,
				&d.CreatedAt, &d.UpdatedAt); err != nil {
				return err
			}
			out = append(out, d)
		}
		return rows.Err()
	})
	return out, err
}

// UpdateDashboard updates mutable fields + emits events.
func (s *PG) UpdateDashboard(ctx context.Context, d *domain.Dashboard, envs []event.Envelope) error {
	return s.withTenant(ctx, d.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx,
			`UPDATE dashboards SET name=$2,description=$3,layout=$4,meta=$5,tags=$6,updated_at=now()
			 WHERE id=$1 AND deleted_at IS NULL`,
			d.ID, d.Name, d.Description, jsonOr(d.Layout, "[]"), jsonOr(d.Meta, "{}"), d.Tags)
		if err != nil {
			if isUnique(err) {
				return domain.EConflict("dashboard name already exists")
			}
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("dashboard not found")
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// SetDashboardArchived archives/restores a dashboard and cascades archived to
// its documentation (CHART-FR-003).
func (s *PG) SetDashboardArchived(ctx context.Context, tenant, id uuid.UUID, archived bool, envs []event.Envelope) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var at any
		if archived {
			at = time.Now().UTC()
		}
		ct, err := tx.Exec(ctx,
			`UPDATE dashboards SET archived=$2, archived_at=$3, updated_at=now() WHERE id=$1 AND deleted_at IS NULL`,
			id, archived, at)
		if err != nil {
			if isUnique(err) {
				return domain.EConflict("a live dashboard with this name already exists")
			}
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("dashboard not found")
		}
		if _, err := tx.Exec(ctx,
			`UPDATE documentations SET archived=$2, archived_at=$3, updated_at=now()
			 WHERE documentable_type='dashboard' AND documentable_id=$1`, id, archived, at); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// DeleteDashboard soft-deletes a dashboard after the allow_cases guard passes
// (checked by the caller). Returns not-found if absent.
func (s *PG) DeleteDashboard(ctx context.Context, tenant, id uuid.UUID, envs []event.Envelope) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE dashboards SET deleted_at=now() WHERE id=$1 AND deleted_at IS NULL`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("dashboard not found")
		}
		if _, err := tx.Exec(ctx, `UPDATE charts SET deleted_at=now() WHERE dashboard_id=$1 AND deleted_at IS NULL`, id); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// ---------- Charts ----------

// CreateChart inserts a chart + its sources + outbox events atomically.
func (s *PG) CreateChart(ctx context.Context, c *domain.Chart, envs []event.Envelope) error {
	return s.withTenant(ctx, c.TenantID, func(tx pgx.Tx) error {
		// dashboard must exist and be in-tenant.
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT true FROM dashboards WHERE id=$1 AND deleted_at IS NULL`, c.DashboardID).Scan(&exists); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return domain.ENotFound("dashboard not found")
			}
			return err
		}
		_, err := tx.Exec(ctx,
			`INSERT INTO charts (id,tenant_id,dashboard_id,name,chart_type,description,config,display_meta,chart_version,custom,config_status)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'ok')`,
			c.ID, c.TenantID, c.DashboardID, c.Name, c.ChartType, c.Description,
			jsonOr(c.Config, "{}"), jsonOr(c.DisplayMeta, "{}"), c.ChartVersion, c.Custom)
		if err != nil {
			if isUnique(err) {
				return domain.EConflict("chart name already exists in this dashboard")
			}
			return err
		}
		if err := replaceSourcesTx(ctx, tx, c); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

func replaceSourcesTx(ctx context.Context, tx pgx.Tx, c *domain.Chart) error {
	if _, err := tx.Exec(ctx, `DELETE FROM chart_sources WHERE chart_id=$1`, c.ID); err != nil {
		return err
	}
	for _, src := range c.Sources {
		if _, err := tx.Exec(ctx,
			`INSERT INTO chart_sources (id,tenant_id,chart_id,position,source_type,source_urn)
			 VALUES ($1,$2,$3,$4,$5,$6)`,
			uuid.New(), c.TenantID, c.ID, src.Position, src.SourceType, src.SourceURN); err != nil {
			return err
		}
	}
	return nil
}

// GetChart loads a chart + sources.
func (s *PG) GetChart(ctx context.Context, tenant, id uuid.UUID) (*domain.Chart, error) {
	var out *domain.Chart
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		c, err := scanChart(ctx, tx, `WHERE id=$1 AND deleted_at IS NULL`, id)
		if err != nil {
			return err
		}
		out = c
		return nil
	})
	return out, err
}

func scanChart(ctx context.Context, tx pgx.Tx, where string, args ...any) (*domain.Chart, error) {
	var c domain.Chart
	err := tx.QueryRow(ctx,
		`SELECT id,tenant_id,dashboard_id,name,chart_type,description,config,display_meta,chart_version,custom,config_status,link_type,linked_parent_id,created_at,updated_at
		 FROM charts `+where, args...).
		Scan(&c.ID, &c.TenantID, &c.DashboardID, &c.Name, &c.ChartType, &c.Description, &c.Config,
			&c.DisplayMeta, &c.ChartVersion, &c.Custom, &c.ConfigStatus, &c.LinkType, &c.LinkedParentID,
			&c.CreatedAt, &c.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, domain.ENotFound("chart not found")
	}
	if err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT position,source_type,source_urn FROM chart_sources WHERE chart_id=$1 ORDER BY position`, c.ID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var src domain.ChartSource
		if err := rows.Scan(&src.Position, &src.SourceType, &src.SourceURN); err != nil {
			return nil, err
		}
		c.Sources = append(c.Sources, src)
	}
	return &c, rows.Err()
}

// ListCharts lists a dashboard's live charts (with sources).
func (s *PG) ListCharts(ctx context.Context, tenant, dashboardID uuid.UUID) ([]domain.Chart, error) {
	var out []domain.Chart
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT id FROM charts WHERE dashboard_id=$1 AND deleted_at IS NULL ORDER BY id`, dashboardID)
		if err != nil {
			return err
		}
		var ids []uuid.UUID
		for rows.Next() {
			var id uuid.UUID
			if err := rows.Scan(&id); err != nil {
				rows.Close()
				return err
			}
			ids = append(ids, id)
		}
		rows.Close()
		for _, id := range ids {
			c, err := scanChart(ctx, tx, `WHERE id=$1`, id)
			if err != nil {
				return err
			}
			out = append(out, *c)
		}
		return nil
	})
	return out, err
}

// UpdateChart updates a chart (optimistic lock via expectVersion when > 0) and
// replaces its sources; bumps chart_version when versionBump is true.
func (s *PG) UpdateChart(ctx context.Context, c *domain.Chart, versionBump bool, expectVersion int, envs []event.Envelope) error {
	return s.withTenant(ctx, c.TenantID, func(tx pgx.Tx) error {
		var cur int
		err := tx.QueryRow(ctx, `SELECT chart_version FROM charts WHERE id=$1 AND deleted_at IS NULL`, c.ID).Scan(&cur)
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.ENotFound("chart not found")
		}
		if err != nil {
			return err
		}
		if expectVersion > 0 && expectVersion != cur {
			return domain.EConflict("stale chart_version (optimistic lock)")
		}
		newVersion := cur
		if versionBump {
			newVersion = cur + 1
		}
		c.ChartVersion = newVersion
		_, err = tx.Exec(ctx,
			`UPDATE charts SET name=$2,chart_type=$3,description=$4,config=$5,display_meta=$6,chart_version=$7,config_status='ok',updated_at=now()
			 WHERE id=$1`,
			c.ID, c.Name, c.ChartType, c.Description, jsonOr(c.Config, "{}"), jsonOr(c.DisplayMeta, "{}"), newVersion)
		if err != nil {
			if isUnique(err) {
				return domain.EConflict("chart name already exists in this dashboard")
			}
			return err
		}
		if err := replaceSourcesTx(ctx, tx, c); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// DeleteChart soft-deletes a chart and cleans links in both directions
// (CHART-FR-015 / AC-10).
func (s *PG) DeleteChart(ctx context.Context, tenant, id uuid.UUID, envs []event.Envelope) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE charts SET deleted_at=now(), linked_parent_id=NULL, link_type=NULL WHERE id=$1 AND deleted_at IS NULL`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("chart not found")
		}
		// Clear back-references on children and remove link rows both ways.
		if _, err := tx.Exec(ctx, `UPDATE charts SET linked_parent_id=NULL, link_type=NULL WHERE linked_parent_id=$1`, id); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `DELETE FROM chart_links WHERE parent_chart_id=$1 OR child_chart_id=$1`, id); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// DashboardBlockingCharts returns charts under a dashboard that block deletion:
// any chart with display_meta.allow_cases=true (CHART-FR-016 / BR-4).
func (s *PG) DashboardBlockingCharts(ctx context.Context, tenant, dashboardID uuid.UUID) ([]uuid.UUID, error) {
	var ids []uuid.UUID
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx,
			`SELECT id FROM charts WHERE dashboard_id=$1 AND deleted_at IS NULL
			 AND coalesce((display_meta->>'allow_cases')::boolean,false)=true`, dashboardID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id uuid.UUID
			if err := rows.Scan(&id); err != nil {
				return err
			}
			ids = append(ids, id)
		}
		return rows.Err()
	})
	return ids, err
}

// ChartAllowsCases reports whether a chart has display_meta.allow_cases=true.
func (s *PG) ChartAllowsCases(ctx context.Context, tenant, id uuid.UUID) (bool, error) {
	var v bool
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx,
			`SELECT coalesce((display_meta->>'allow_cases')::boolean,false) FROM charts WHERE id=$1 AND deleted_at IS NULL`, id).Scan(&v)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return false, domain.ENotFound("chart not found")
	}
	return v, err
}

// ---------- Links (CHART-FR-015 / BR-9) ----------

// CreateLink writes chart_links + both charts' refs in one transaction, after a
// cycle check up to depth 10.
func (s *PG) CreateLink(ctx context.Context, tenant, parentID, childID uuid.UUID, cols []domain.ColumnPair, linkType int, envs []event.Envelope) error {
	if parentID == childID {
		return domain.ECircularLink("a chart cannot link to itself")
	}
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		for _, id := range []uuid.UUID{parentID, childID} {
			var ok bool
			if err := tx.QueryRow(ctx, `SELECT true FROM charts WHERE id=$1 AND deleted_at IS NULL`, id).Scan(&ok); err != nil {
				if errors.Is(err, pgx.ErrNoRows) {
					return domain.ENotFound("chart not found")
				}
				return err
			}
		}
		// Cycle check: would child reach parent through existing links? (depth<=10)
		cyclic, err := reachable(ctx, tx, childID, parentID, 10)
		if err != nil {
			return err
		}
		if cyclic {
			return domain.ECircularLink("link would create a cycle")
		}
		colsJSON, _ := json.Marshal(cols)
		_, err = tx.Exec(ctx,
			`INSERT INTO chart_links (id,tenant_id,parent_chart_id,child_chart_id,linked_columns)
			 VALUES ($1,$2,$3,$4,$5)`, uuid.New(), tenant, parentID, childID, colsJSON)
		if err != nil {
			if isUnique(err) {
				return domain.EConflict("link already exists")
			}
			return err
		}
		if _, err := tx.Exec(ctx, `UPDATE charts SET linked_parent_id=$2, link_type=$3, updated_at=now() WHERE id=$1`, childID, parentID, linkType); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// reachable does a bounded BFS over chart_links (parent→child edges).
func reachable(ctx context.Context, tx pgx.Tx, from, target uuid.UUID, maxDepth int) (bool, error) {
	frontier := []uuid.UUID{from}
	seen := map[uuid.UUID]bool{from: true}
	for depth := 0; depth < maxDepth && len(frontier) > 0; depth++ {
		var next []uuid.UUID
		for _, n := range frontier {
			if n == target {
				return true, nil
			}
			rows, err := tx.Query(ctx, `SELECT child_chart_id FROM chart_links WHERE parent_chart_id=$1`, n)
			if err != nil {
				return false, err
			}
			for rows.Next() {
				var c uuid.UUID
				if err := rows.Scan(&c); err != nil {
					rows.Close()
					return false, err
				}
				if c == target {
					rows.Close()
					return true, nil
				}
				if !seen[c] {
					seen[c] = true
					next = append(next, c)
				}
			}
			rows.Close()
		}
		frontier = next
	}
	return false, nil
}

// RemoveLink deletes the link and clears the child back-reference atomically.
func (s *PG) RemoveLink(ctx context.Context, tenant, parentID, childID uuid.UUID, envs []event.Envelope) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `DELETE FROM chart_links WHERE parent_chart_id=$1 AND child_chart_id=$2`, parentID, childID)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("link not found")
		}
		if _, err := tx.Exec(ctx, `UPDATE charts SET linked_parent_id=NULL, link_type=NULL, updated_at=now() WHERE id=$1 AND linked_parent_id=$2`, childID, parentID); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// ---------- Invalidation reverse-index (CHART-FR-031) ----------

// ChartsForURN returns chart ids in a tenant referencing source_urn.
func (s *PG) ChartsForURN(ctx context.Context, tenant uuid.UUID, urn string) ([]uuid.UUID, error) {
	var ids []uuid.UUID
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT DISTINCT chart_id FROM chart_sources WHERE source_urn=$1`, urn)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id uuid.UUID
			if err := rows.Scan(&id); err != nil {
				return err
			}
			ids = append(ids, id)
		}
		return rows.Err()
	})
	return ids, err
}

// ChartsForURNAllTenants returns (tenant, chart_id) pairs referencing urn across
// all tenants — used by consumers that receive an event before resolving the
// tenant context. Runs under app.role=platform.
func (s *PG) ChartsForURNAllTenants(ctx context.Context, urn string) (map[uuid.UUID][]uuid.UUID, error) {
	out := map[uuid.UUID][]uuid.UUID{}
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT tenant_id, chart_id FROM chart_sources WHERE source_urn=$1`, urn)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var t, c uuid.UUID
			if err := rows.Scan(&t, &c); err != nil {
				return err
			}
			out[t] = append(out[t], c)
		}
		return rows.Err()
	})
	return out, err
}

// MarkChartsBroken flips config_status to broken and bumps version for charts
// referencing a deleted measure/query (BR-3). Returns affected chart ids.
func (s *PG) MarkChartsBroken(ctx context.Context, tenant uuid.UUID, ids []uuid.UUID, envs []event.Envelope) error {
	if len(ids) == 0 {
		return nil
	}
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx,
			`UPDATE charts SET config_status='broken', chart_version=chart_version+1, updated_at=now() WHERE id = ANY($1)`, ids); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// ---------- Operations (exports) ----------

// CreateOperation inserts a pending operation.
func (s *PG) CreateOperation(ctx context.Context, op *domain.Operation, tenant uuid.UUID) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`INSERT INTO operations (id,tenant_id,chart_id,kind,format,status,request,created_by)
			 VALUES ($1,$2,$3,$4,$5,'pending',$6,$7)`,
			op.ID, tenant, op.ChartID, op.Kind, op.Format, jsonOr(op.Request, "{}"), op.CreatedBy)
		return err
	})
}

// ConcurrentExports counts in-flight exports for a tenant (CHART-FR-041 cap).
func (s *PG) ConcurrentExports(ctx context.Context, tenant uuid.UUID) (int, error) {
	var n int
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT count(*) FROM operations WHERE kind='export' AND status IN ('pending','running')`).Scan(&n)
	})
	return n, err
}

// UpdateOperation sets terminal status/artifact.
func (s *PG) UpdateOperation(ctx context.Context, tenant, id uuid.UUID, status, url, urn, errMsg string, expires *time.Time) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`UPDATE operations SET status=$2,artifact_url=$3,artifact_urn=$4,error=$5,expires_at=$6,updated_at=now() WHERE id=$1`,
			id, status, nullStr(url), nullStr(urn), nullStr(errMsg), expires)
		return err
	})
}

// GetOperation loads an operation.
func (s *PG) GetOperation(ctx context.Context, tenant, id uuid.UUID) (*domain.Operation, error) {
	var op domain.Operation
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx,
			`SELECT id,chart_id,kind,coalesce(format,''),status,coalesce(artifact_url,''),coalesce(artifact_urn,''),coalesce(error,''),expires_at,created_at,updated_at
			 FROM operations WHERE id=$1`, id).
			Scan(&op.ID, &op.ChartID, &op.Kind, &op.Format, &op.Status, &op.ArtifactURL, &op.ArtifactURN, &op.Error, &op.ExpiresAt, &op.CreatedAt, &op.UpdatedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, domain.ENotFound("operation not found")
	}
	return &op, err
}

// ---------- Idempotency (MASTER-FR-025) ----------

// GetIdempotent returns a stored response for (tenant, key, method, path).
func (s *PG) GetIdempotent(ctx context.Context, tenant uuid.UUID, key, method, path string) (int, []byte, bool, error) {
	var status int
	var body []byte
	found := false
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx,
			`SELECT status_code, response_body FROM idempotency_keys WHERE tenant_id=$1 AND idem_key=$2 AND method=$3 AND path=$4 AND created_at > now() - interval '24 hours'`,
			tenant, key, method, path).Scan(&status, &body)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		found = true
		return nil
	})
	return status, body, found, err
}

// PutIdempotent stores a response under an idempotency key.
func (s *PG) PutIdempotent(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, body []byte) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx,
			`INSERT INTO idempotency_keys (id,tenant_id,idem_key,method,path,status_code,response_body)
			 VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING`,
			uuid.New(), tenant, key, method, path, status, body)
		return err
	})
}

// ---------- Outbox relay Source (go-common) ----------

// FetchUnpublished implements outbox.Source.
func (s *PG) FetchUnpublished(ctx context.Context, limit int) ([]outbox.Row, error) {
	var rows []outbox.Row
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		r, err := tx.Query(ctx, `SELECT id, envelope FROM outbox WHERE NOT published ORDER BY id ASC LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer r.Close()
		for r.Next() {
			var id int64
			var raw []byte
			if err := r.Scan(&id, &raw); err != nil {
				return err
			}
			var env event.Envelope
			if err := json.Unmarshal(raw, &env); err != nil {
				return err
			}
			rows = append(rows, outbox.Row{ID: id, Envelope: env})
		}
		return r.Err()
	})
	return rows, err
}

// MarkPublished implements outbox.Source.
func (s *PG) MarkPublished(ctx context.Context, ids []any) error {
	if len(ids) == 0 {
		return nil
	}
	int64s := make([]int64, 0, len(ids))
	for _, id := range ids {
		if v, ok := id.(int64); ok {
			int64s = append(int64s, v)
		}
	}
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE outbox SET published=true, published_at=now() WHERE id = ANY($1)`, int64s)
		return err
	})
}

// ---------- helpers ----------

func jsonOr(raw json.RawMessage, def string) []byte {
	if len(raw) == 0 || strings.TrimSpace(string(raw)) == "null" {
		return []byte(def)
	}
	return raw
}
func statusOr(s string) string {
	if s == "" {
		return "active"
	}
	return s
}
func nullStr(s string) any {
	if s == "" {
		return nil
	}
	return s
}
