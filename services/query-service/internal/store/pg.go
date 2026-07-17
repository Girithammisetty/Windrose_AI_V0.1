package store

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/events"
)

// PG is the pgx-backed Store. Every tenant-scoped operation runs inside a
// transaction that first sets the app.tenant_id GUC so Postgres RLS
// enforces isolation below the application (MASTER-FR-001).
type PG struct {
	pool *pgxpool.Pool
}

func NewPG(pool *pgxpool.Pool) *PG { return &PG{pool: pool} }

func (s *PG) Pool() *pgxpool.Pool { return s.pool }

func (s *PG) withTenant(ctx context.Context, tenant uuid.UUID, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenant.String()); err != nil {
			return fmt.Errorf("set tenant context: %w", err)
		}
		return fn(tx)
	})
}

func (s *PG) withPlatform(ctx context.Context, fn func(tx pgx.Tx) error) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT set_config('app.role', 'platform', true)`); err != nil {
			return fmt.Errorf("set platform context: %w", err)
		}
		return fn(tx)
	})
}

func (s *PG) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte("null")
	}
	return b
}

func gzipBytes(s string) []byte {
	var buf bytes.Buffer
	w := gzip.NewWriter(&buf)
	_, _ = w.Write([]byte(s))
	_ = w.Close()
	return buf.Bytes()
}

func gunzipBytes(b []byte) string {
	if len(b) == 0 {
		return ""
	}
	r, err := gzip.NewReader(bytes.NewReader(b))
	if err != nil {
		return ""
	}
	out, err := io.ReadAll(r)
	if err != nil {
		return ""
	}
	return string(out)
}

func insertOutboxTx(ctx context.Context, tx pgx.Tx, envs []events.Envelope) error {
	for _, env := range envs {
		var viaAgent []byte
		if env.ViaAgent != nil {
			viaAgent = mustJSON(env.ViaAgent)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO outbox (event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
			ON CONFLICT (event_id) DO NOTHING`,
			env.EventID, env.TenantID, env.EventType, env.Actor.Type, env.Actor.ID,
			viaAgent, env.ResourceURN, env.OccurredAt, env.TraceID, mustJSON(env.Payload)); err != nil {
			return fmt.Errorf("outbox insert: %w", err)
		}
	}
	return nil
}

// ---- Saved queries ----------------------------------------------------------

func (s *PG) CreateSavedQuery(ctx context.Context, op domain.Op, sq *domain.SavedQuery, v *domain.SavedQueryVersion, envs []events.Envelope) error {
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO saved_queries (id, tenant_id, workspace_id, name, description, current_version_no, tags, module_names, created_by, created_at, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10)`,
			sq.ID, op.Tenant, sq.WorkspaceID, sq.Name, sq.Description, sq.CurrentVersionNo, sq.Tags, sq.ModuleNames, sq.CreatedBy, sq.CreatedAt)
		if err != nil {
			return err
		}
		if err := insertVersionTx(ctx, tx, op.Tenant, v); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
	if isUniqueViolation(err) {
		return ErrNameConflict
	}
	return err
}

func insertVersionTx(ctx context.Context, tx pgx.Tx, tenant uuid.UUID, v *domain.SavedQueryVersion) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO saved_query_versions (id, tenant_id, saved_query_id, version_no, sql_text, variables, dataset_refs, created_by, created_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
		v.ID, tenant, v.SavedQueryID, v.VersionNo, v.SQLText, mustJSON(v.Variables), mustJSON(v.DatasetRefs), v.CreatedBy, v.CreatedAt)
	return err
}

const savedQueryCols = `id, tenant_id, workspace_id, name, description, current_version_no, tags, module_names, created_by, created_at, updated_at, deleted_at`

func scanSavedQuery(row pgx.Row) (*domain.SavedQuery, error) {
	var q domain.SavedQuery
	err := row.Scan(&q.ID, &q.TenantID, &q.WorkspaceID, &q.Name, &q.Description, &q.CurrentVersionNo,
		&q.Tags, &q.ModuleNames, &q.CreatedBy, &q.CreatedAt, &q.UpdatedAt, &q.DeletedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &q, nil
}

const versionCols = `id, tenant_id, saved_query_id, version_no, sql_text, variables, dataset_refs, created_by, created_at`

func scanVersion(row pgx.Row) (*domain.SavedQueryVersion, error) {
	var v domain.SavedQueryVersion
	var vars, refs []byte
	err := row.Scan(&v.ID, &v.TenantID, &v.SavedQueryID, &v.VersionNo, &v.SQLText, &vars, &refs, &v.CreatedBy, &v.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal(vars, &v.Variables)
	_ = json.Unmarshal(refs, &v.DatasetRefs)
	return &v, nil
}

func (s *PG) GetSavedQuery(ctx context.Context, tenant, id uuid.UUID) (*domain.SavedQuery, *domain.SavedQueryVersion, error) {
	var q *domain.SavedQuery
	var v *domain.SavedQueryVersion
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		q, err = scanSavedQuery(tx.QueryRow(ctx,
			`SELECT `+savedQueryCols+` FROM saved_queries WHERE id = $1 AND deleted_at IS NULL`, id))
		if err != nil {
			return err
		}
		v, err = scanVersion(tx.QueryRow(ctx,
			`SELECT `+versionCols+` FROM saved_query_versions WHERE saved_query_id = $1 AND version_no = $2`, id, q.CurrentVersionNo))
		return err
	})
	if err != nil {
		return nil, nil, err
	}
	return q, v, nil
}

func (s *PG) GetVersion(ctx context.Context, tenant, id uuid.UUID, versionNo int) (*domain.SavedQueryVersion, error) {
	var v *domain.SavedQueryVersion
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		// 404 must not leak whether the query exists cross-tenant: RLS hides
		// the head row too.
		if _, err := scanSavedQuery(tx.QueryRow(ctx,
			`SELECT `+savedQueryCols+` FROM saved_queries WHERE id = $1 AND deleted_at IS NULL`, id)); err != nil {
			return err
		}
		var err error
		v, err = scanVersion(tx.QueryRow(ctx,
			`SELECT `+versionCols+` FROM saved_query_versions WHERE saved_query_id = $1 AND version_no = $2`, id, versionNo))
		return err
	})
	return v, err
}

func (s *PG) ListSavedQueries(ctx context.Context, tenant uuid.UUID, f SavedQueryFilter) (Page[*domain.SavedQuery], error) {
	limit := ClampLimit(f.Limit)
	var page Page[*domain.SavedQuery]
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + savedQueryCols + ` FROM saved_queries WHERE deleted_at IS NULL`
		args := []any{}
		if f.WorkspaceID != nil {
			args = append(args, *f.WorkspaceID)
			q += fmt.Sprintf(" AND workspace_id = $%d", len(args))
		}
		if f.Cursor != "" {
			args = append(args, f.Cursor)
			q += fmt.Sprintf(" AND id < $%d", len(args))
		}
		args = append(args, limit+1)
		q += fmt.Sprintf(" ORDER BY id DESC LIMIT $%d", len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			sq, err := scanSavedQuery(rows)
			if err != nil {
				return err
			}
			page.Data = append(page.Data, sq)
		}
		return rows.Err()
	})
	if err != nil {
		return page, err
	}
	trimPage(&page, limit, func(q *domain.SavedQuery) string { return q.ID.String() })
	return page, nil
}

func (s *PG) ListVersions(ctx context.Context, tenant, id uuid.UUID, limit int, cursor string) (Page[*domain.SavedQueryVersion], error) {
	limit = ClampLimit(limit)
	var page Page[*domain.SavedQueryVersion]
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		if _, err := scanSavedQuery(tx.QueryRow(ctx,
			`SELECT `+savedQueryCols+` FROM saved_queries WHERE id = $1 AND deleted_at IS NULL`, id)); err != nil {
			return err
		}
		q := `SELECT ` + versionCols + ` FROM saved_query_versions WHERE saved_query_id = $1`
		args := []any{id}
		if cursor != "" {
			args = append(args, cursor)
			q += fmt.Sprintf(" AND id < $%d", len(args))
		}
		args = append(args, limit+1)
		q += fmt.Sprintf(" ORDER BY id DESC LIMIT $%d", len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			v, err := scanVersion(rows)
			if err != nil {
				return err
			}
			page.Data = append(page.Data, v)
		}
		return rows.Err()
	})
	if err != nil {
		return page, err
	}
	trimPage(&page, limit, func(v *domain.SavedQueryVersion) string { return v.ID.String() })
	return page, nil
}

func (s *PG) UpdateSavedQuery(ctx context.Context, op domain.Op, sq *domain.SavedQuery, v *domain.SavedQueryVersion, expectVersion int, envs []events.Envelope) error {
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		// Per-query advisory lock: version numbers never fork (BR-11).
		if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1::text, 42))`, sq.ID); err != nil {
			return err
		}
		cur, err := scanSavedQuery(tx.QueryRow(ctx,
			`SELECT `+savedQueryCols+` FROM saved_queries WHERE id = $1 AND deleted_at IS NULL FOR UPDATE`, sq.ID))
		if err != nil {
			return err
		}
		if cur.CurrentVersionNo != expectVersion {
			return ErrStaleVersion
		}
		if _, err := tx.Exec(ctx, `
			UPDATE saved_queries SET name=$2, description=$3, current_version_no=$4, tags=$5, module_names=$6, updated_at=$7
			WHERE id = $1`,
			sq.ID, sq.Name, sq.Description, sq.CurrentVersionNo, sq.Tags, sq.ModuleNames, sq.UpdatedAt); err != nil {
			return err
		}
		if v != nil {
			if err := insertVersionTx(ctx, tx, op.Tenant, v); err != nil {
				return err
			}
		}
		return insertOutboxTx(ctx, tx, envs)
	})
	if isUniqueViolation(err) {
		return ErrNameConflict
	}
	return err
}

func (s *PG) SoftDeleteSavedQuery(ctx context.Context, op domain.Op, id uuid.UUID, envs []events.Envelope) error {
	return s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE saved_queries SET deleted_at = now() WHERE id = $1 AND deleted_at IS NULL`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

// ---- Executions -------------------------------------------------------------

const execCols = `id, tenant_id, workspace_id, saved_query_id, query_version_no, sql_fingerprint, sql_text_compressed,
	bound_params, caller_class, engine, routing_reason, status, queue_position, estimated_scan_bytes, actual_scan_bytes,
	result_rows, result_bytes, result_uri, cache_hit, cache_key, dataset_urns, error, ceilings, warnings, duration_ms,
	started_at, finished_at, created_by, via_agent, trace_id, created_at`

func scanExecution(row pgx.Row) (*domain.Execution, error) {
	var e domain.Execution
	var sqlComp []byte
	var boundParams, routing, urns, execErr, ceilings, warnings, viaAgent []byte
	err := row.Scan(&e.ID, &e.TenantID, &e.WorkspaceID, &e.SavedQueryID, &e.QueryVersionNo, &e.SQLFingerprint, &sqlComp,
		&boundParams, &e.CallerClass, &e.Engine, &routing, &e.Status, &e.QueuePosition, &e.EstimatedScanBytes, &e.ActualScanBytes,
		&e.ResultRows, &e.ResultBytes, &e.ResultURI, &e.CacheHit, &e.CacheKey, &urns, &execErr, &ceilings, &warnings, &e.DurationMS,
		&e.StartedAt, &e.FinishedAt, &e.CreatedBy, &viaAgent, &e.TraceID, &e.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	e.SQLText = gunzipBytes(sqlComp)
	_ = json.Unmarshal(boundParams, &e.BoundParams)
	if len(routing) > 0 && string(routing) != "null" {
		e.RoutingReason = &domain.RoutingReason{}
		_ = json.Unmarshal(routing, e.RoutingReason)
	}
	_ = json.Unmarshal(urns, &e.DatasetURNs)
	if len(execErr) > 0 && string(execErr) != "null" {
		e.Error = &domain.ExecError{}
		_ = json.Unmarshal(execErr, e.Error)
	}
	if len(ceilings) > 0 && string(ceilings) != "null" {
		e.Ceilings = &domain.Ceilings{}
		_ = json.Unmarshal(ceilings, e.Ceilings)
	}
	_ = json.Unmarshal(warnings, &e.Warnings)
	if len(viaAgent) > 0 && string(viaAgent) != "null" {
		_ = json.Unmarshal(viaAgent, &e.ViaAgent)
	}
	return &e, nil
}

func execUpdateArgs(e *domain.Execution) []any {
	var viaAgent []byte
	if e.ViaAgent != nil {
		viaAgent = mustJSON(e.ViaAgent)
	}
	var routing, execErr, ceilings []byte
	if e.RoutingReason != nil {
		routing = mustJSON(e.RoutingReason)
	}
	if e.Error != nil {
		execErr = mustJSON(e.Error)
	}
	if e.Ceilings != nil {
		ceilings = mustJSON(e.Ceilings)
	}
	warnings := e.Warnings
	if warnings == nil {
		warnings = []string{}
	}
	urns := e.DatasetURNs
	if urns == nil {
		urns = []string{}
	}
	return []any{
		e.ID, e.TenantID, e.WorkspaceID, e.SavedQueryID, e.QueryVersionNo, e.SQLFingerprint, gzipBytes(e.SQLText),
		mustJSON(e.BoundParams), string(e.CallerClass), e.Engine, routing, e.Status, e.QueuePosition, e.EstimatedScanBytes,
		e.ActualScanBytes, e.ResultRows, e.ResultBytes, e.ResultURI, e.CacheHit, e.CacheKey, mustJSON(urns), execErr,
		ceilings, mustJSON(warnings), e.DurationMS, e.StartedAt, e.FinishedAt, e.CreatedBy, viaAgent, e.TraceID, e.CreatedAt,
	}
}

func (s *PG) InsertExecution(ctx context.Context, op domain.Op, e *domain.Execution, envs []events.Envelope) error {
	return s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO executions (`+execCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31)`,
			execUpdateArgs(e)...)
		if err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

func (s *PG) UpdateExecution(ctx context.Context, tenant, id uuid.UUID, apply func(e *domain.Execution) ([]events.Envelope, error)) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		e, err := scanExecution(tx.QueryRow(ctx, `SELECT `+execCols+` FROM executions WHERE id = $1 FOR UPDATE`, id))
		if err != nil {
			return err
		}
		envs, err := apply(e)
		if err != nil {
			return err
		}
		all := execUpdateArgs(e)
		// all[5:27] = sql_fingerprint .. finished_at (see execCols order).
		args := append([]any{e.ID}, all[5:27]...)
		if _, err := tx.Exec(ctx, `
			UPDATE executions SET
				sql_fingerprint=$2, sql_text_compressed=$3, bound_params=$4, caller_class=$5, engine=$6,
				routing_reason=$7, status=$8, queue_position=$9, estimated_scan_bytes=$10, actual_scan_bytes=$11,
				result_rows=$12, result_bytes=$13, result_uri=$14, cache_hit=$15, cache_key=$16, dataset_urns=$17,
				error=$18, ceilings=$19, warnings=$20, duration_ms=$21, started_at=$22, finished_at=$23
			WHERE id = $1`, args...); err != nil {
			return err
		}
		return insertOutboxTx(ctx, tx, envs)
	})
}

func (s *PG) GetExecution(ctx context.Context, tenant, id uuid.UUID) (*domain.Execution, error) {
	var e *domain.Execution
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		e, err = scanExecution(tx.QueryRow(ctx, `SELECT `+execCols+` FROM executions WHERE id = $1`, id))
		return err
	})
	return e, err
}

func (s *PG) ListExecutions(ctx context.Context, tenant uuid.UUID, f ExecutionFilter) (Page[*domain.Execution], error) {
	limit := ClampLimit(f.Limit)
	var page Page[*domain.Execution]
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + execCols + ` FROM executions WHERE true`
		args := []any{}
		add := func(cond string, v any) {
			args = append(args, v)
			q += fmt.Sprintf(" AND "+cond, len(args))
		}
		if f.Status != "" {
			add("status = $%d", f.Status)
		}
		if f.User != "" {
			add("created_by = $%d", f.User)
		}
		if f.SavedQueryID != nil {
			add("saved_query_id = $%d", *f.SavedQueryID)
		}
		if f.Since != nil {
			add("created_at >= $%d", *f.Since)
		}
		if f.SortByCost {
			offset := 0
			if f.Cursor != "" {
				fmt.Sscanf(f.Cursor, "o%d", &offset) //nolint:errcheck
			}
			args = append(args, limit+1, offset)
			q += fmt.Sprintf(" ORDER BY actual_scan_bytes DESC, id DESC LIMIT $%d OFFSET $%d", len(args)-1, len(args))
			rows, err := tx.Query(ctx, q, args...)
			if err != nil {
				return err
			}
			defer rows.Close()
			for rows.Next() {
				e, err := scanExecution(rows)
				if err != nil {
					return err
				}
				page.Data = append(page.Data, e)
			}
			if len(page.Data) > limit {
				page.Data = page.Data[:limit]
				page.HasMore = true
				page.NextCursor = fmt.Sprintf("o%d", offset+limit)
			}
			return rows.Err()
		}
		if f.Cursor != "" {
			add("id < $%d", f.Cursor)
		}
		args = append(args, limit+1)
		q += fmt.Sprintf(" ORDER BY id DESC LIMIT $%d", len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			e, err := scanExecution(rows)
			if err != nil {
				return err
			}
			page.Data = append(page.Data, e)
		}
		if err := rows.Err(); err != nil {
			return err
		}
		trimPage(&page, limit, func(e *domain.Execution) string { return e.ID.String() })
		return nil
	})
	return page, err
}

func (s *PG) FindCacheHit(ctx context.Context, tenant uuid.UUID, cacheKey string, since time.Time) (*domain.Execution, error) {
	var e *domain.Execution
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		e, err = scanExecution(tx.QueryRow(ctx, `
			SELECT `+execCols+` FROM executions
			WHERE cache_key = $1 AND status = 'succeeded' AND cache_hit = false AND result_uri <> '' AND finished_at >= $2
			ORDER BY finished_at DESC LIMIT 1`, cacheKey, since))
		return err
	})
	return e, err
}

func (s *PG) ActiveExecutions(ctx context.Context, tenant uuid.UUID) ([]*domain.Execution, error) {
	var out []*domain.Execution
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT `+execCols+` FROM executions WHERE status IN ('queued','running','streaming_results')`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			e, err := scanExecution(rows)
			if err != nil {
				return err
			}
			out = append(out, e)
		}
		return rows.Err()
	})
	return out, err
}

func (s *PG) QueryStats(ctx context.Context, tenant uuid.UUID, since time.Time, limit int) ([]QueryStat, error) {
	var out []QueryStat
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT sql_fingerprint, count(*), coalesce(sum(actual_scan_bytes),0),
			       count(*) FILTER (WHERE status IN ('failed','rejected','ceiling_exceeded'))
			FROM executions WHERE created_at >= $1
			GROUP BY sql_fingerprint ORDER BY 3 DESC LIMIT $2`, since, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var st QueryStat
			if err := rows.Scan(&st.SQLFingerprint, &st.Executions, &st.TotalScanBytes, &st.Failures); err != nil {
				return err
			}
			out = append(out, st)
		}
		return rows.Err()
	})
	return out, err
}

// ---- Limits -----------------------------------------------------------------

func (s *PG) GetTenantLimits(ctx context.Context, tenant uuid.UUID) (*domain.TenantLimits, error) {
	var l *domain.TenantLimits
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var overrides []byte
		err := tx.QueryRow(ctx, `SELECT overrides FROM tenant_query_limits WHERE tenant_id = $1`, tenant).Scan(&overrides)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		l = &domain.TenantLimits{TenantID: tenant}
		return json.Unmarshal(overrides, l)
	})
	return l, err
}

func (s *PG) PutTenantLimits(ctx context.Context, op domain.Op, l *domain.TenantLimits) error {
	return s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO tenant_query_limits (tenant_id, overrides, updated_by)
			VALUES ($1, $2, $3)
			ON CONFLICT (tenant_id) DO UPDATE SET overrides = $2, updated_by = $3, updated_at = now()`,
			op.Tenant, mustJSON(l), op.Actor.ID)
		return err
	})
}

// ---- Idempotency ------------------------------------------------------------

func (s *PG) GetIdempotency(ctx context.Context, tenant uuid.UUID, key string) (*IdempotencyRecord, error) {
	var rec IdempotencyRecord
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT status, response FROM idempotency_keys
			WHERE tenant_id = $1 AND key = $2 AND created_at > now() - interval '24 hours'`,
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

func (s *PG) PutIdempotency(ctx context.Context, tenant uuid.UUID, key, method, path string, status int, response []byte) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO idempotency_keys (tenant_id, key, method, path, status, response)
			VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (tenant_id, key) DO NOTHING`,
			tenant, key, method, path, status, response)
		return err
	})
}

// ---- Audit + outbox ---------------------------------------------------------

func (s *PG) InsertAudit(ctx context.Context, env events.Envelope) error {
	return s.withTenant(ctx, env.TenantID, func(tx pgx.Tx) error {
		return insertOutboxTx(ctx, tx, []events.Envelope{env})
	})
}

func (s *PG) FetchUnpublished(ctx context.Context, limit int) ([]events.OutboxRow, error) {
	var out []events.OutboxRow
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, event_id, tenant_id, event_type, actor_type, actor_id, via_agent, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var row events.OutboxRow
			var viaAgent, payload []byte
			if err := rows.Scan(&row.ID, &row.Envelope.EventID, &row.Envelope.TenantID, &row.Envelope.EventType,
				&row.Envelope.Actor.Type, &row.Envelope.Actor.ID, &viaAgent, &row.Envelope.ResourceURN,
				&row.Envelope.OccurredAt, &row.Envelope.TraceID, &payload); err != nil {
				return err
			}
			if len(viaAgent) > 0 {
				var va domain.ViaAgent
				if json.Unmarshal(viaAgent, &va) == nil {
					row.Envelope.ViaAgent = &va
				}
			}
			_ = json.Unmarshal(payload, &row.Envelope.Payload)
			out = append(out, row)
		}
		return rows.Err()
	})
	return out, err
}

func (s *PG) MarkPublished(ctx context.Context, ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE outbox SET published_at = now() WHERE id = ANY($1)`, ids)
		return err
	})
}

func (s *PG) OutboxEventsByType(ctx context.Context, tenant uuid.UUID, eventType string) ([]events.Envelope, error) {
	var out []events.Envelope
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT event_id, tenant_id, event_type, actor_type, actor_id, resource_urn, occurred_at, trace_id, payload
			FROM outbox WHERE tenant_id = $1 AND event_type = $2 ORDER BY id`, tenant, eventType)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var env events.Envelope
			var payload []byte
			if err := rows.Scan(&env.EventID, &env.TenantID, &env.EventType, &env.Actor.Type, &env.Actor.ID,
				&env.ResourceURN, &env.OccurredAt, &env.TraceID, &payload); err != nil {
				return err
			}
			_ = json.Unmarshal(payload, &env.Payload)
			out = append(out, env)
		}
		return rows.Err()
	})
	return out, err
}

// trimPage applies the limit+1 overfetch pattern.
func trimPage[T any](p *Page[T], limit int, key func(T) string) {
	if len(p.Data) > limit {
		p.Data = p.Data[:limit]
		p.HasMore = true
		p.NextCursor = key(p.Data[len(p.Data)-1])
	}
}
