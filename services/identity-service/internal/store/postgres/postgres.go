// Package postgres is the pgx implementation of domain.Store. Tenant-scoped
// operations run inside a transaction that sets app.tenant_id (RLS,
// MASTER-FR-001); platform operations (registry tables, outbox poller,
// pre-auth invitation lookup) set app.role=platform instead.
package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/windrose-ai/identity-service/internal/domain"
)

type Store struct {
	pool *pgxpool.Pool
}

func New(pool *pgxpool.Pool) *Store { return &Store{pool: pool} }

// --- transaction helpers ---

func (s *Store) inTx(ctx context.Context, setup string, arg string, fn func(pgx.Tx) error) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	if setup != "" {
		if _, err := tx.Exec(ctx, "SELECT set_config($1, $2, true)", setup, arg); err != nil {
			return err
		}
	}
	if err := fn(tx); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// tenantTx sets app.tenant_id for RLS-scoped work.
func (s *Store) tenantTx(ctx context.Context, tenantID uuid.UUID, fn func(pgx.Tx) error) error {
	return s.inTx(ctx, "app.tenant_id", tenantID.String(), fn)
}

// platformTx sets app.role=platform (outbox poller, pre-auth lookups,
// provisioning seed work executed by identity-service itself).
func (s *Store) platformTx(ctx context.Context, fn func(pgx.Tx) error) error {
	return s.inTx(ctx, "app.role", "platform", fn)
}

// plainTx touches only RLS-exempt platform tables.
func (s *Store) plainTx(ctx context.Context, fn func(pgx.Tx) error) error {
	return s.inTx(ctx, "", "", fn)
}

func insertOutbox(ctx context.Context, tx pgx.Tx, evs []domain.OutboxEvent) error {
	for _, ev := range evs {
		actor, _ := json.Marshal(ev.Actor)
		payload, _ := json.Marshal(ev.Payload)
		var via any
		if ev.ViaAgent != nil {
			via, _ = json.Marshal(ev.ViaAgent)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO outbox (event_id, event_type, tenant_id, actor, via_agent, resource_urn, occurred_at, trace_id, payload)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
			ev.EventID, ev.EventType, ev.TenantID, actor, via, ev.ResourceURN, ev.OccurredAt, ev.TraceID, payload); err != nil {
			return err
		}
	}
	return nil
}

func isUnique(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

// --- tenants ---

const tenantCols = `id, name, display_name, owner_email, tier, cell_id, cloud, status, quotas,
	platform_version, subdomain, k8s_namespace, schema_prefix, auto_upgrade, modules, created_by,
	created_at, updated_at, deleted_at, deletion_scheduled_at`

func scanTenant(row pgx.Row) (*domain.Tenant, error) {
	var t domain.Tenant
	var quotas []byte
	var status string
	err := row.Scan(&t.ID, &t.Name, &t.DisplayName, &t.OwnerEmail, &t.Tier, &t.CellID, &t.Cloud,
		&status, &quotas, &t.PlatformVersion, &t.Subdomain, &t.K8sNamespace, &t.SchemaPrefix,
		&t.AutoUpgrade, &t.Modules, &t.CreatedBy, &t.CreatedAt, &t.UpdatedAt, &t.DeletedAt, &t.DeletionScheduledAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, domain.ENotFound("tenant")
	}
	if err != nil {
		return nil, err
	}
	t.Status = domain.TenantStatus(status)
	if err := json.Unmarshal(quotas, &t.Quotas); err != nil {
		return nil, err
	}
	return &t, nil
}

func (s *Store) CreateTenant(ctx context.Context, t *domain.Tenant, evs ...domain.OutboxEvent) error {
	quotas, _ := json.Marshal(t.Quotas)
	err := s.plainTx(ctx, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO tenants (`+tenantCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)`,
			t.ID, t.Name, t.DisplayName, t.OwnerEmail, t.Tier, t.CellID, t.Cloud, string(t.Status), quotas,
			t.PlatformVersion, t.Subdomain, t.K8sNamespace, t.SchemaPrefix, t.AutoUpgrade, t.Modules,
			t.CreatedBy, t.CreatedAt, t.UpdatedAt, t.DeletedAt, t.DeletionScheduledAt); err != nil {
			return err
		}
		return insertOutbox(ctx, tx, evs)
	})
	if isUnique(err) {
		// AC-4 / BR-1: single transaction — nothing was created.
		return domain.EValidation("tenant name (or a derived identifier) already exists",
			domain.FieldError{Field: "name", Message: "already in use"})
	}
	return err
}

func (s *Store) GetTenant(ctx context.Context, id uuid.UUID) (*domain.Tenant, error) {
	return scanTenant(s.pool.QueryRow(ctx, `SELECT `+tenantCols+` FROM tenants WHERE id = $1`, id))
}

func (s *Store) GetTenantByName(ctx context.Context, name string) (*domain.Tenant, error) {
	return scanTenant(s.pool.QueryRow(ctx, `SELECT `+tenantCols+` FROM tenants WHERE name = $1`, name))
}

func (s *Store) GetTenantEmbedConfig(ctx context.Context, tenantID uuid.UUID) (*domain.TenantEmbedConfig, error) {
	var c domain.TenantEmbedConfig
	err := s.pool.QueryRow(ctx,
		`SELECT tenant_id, secret_hash, allowed_origins, updated_at FROM tenant_embed_configs WHERE tenant_id = $1`,
		tenantID).Scan(&c.TenantID, &c.SecretHash, &c.AllowedOrigins, &c.UpdatedAt)
	if err != nil {
		if err == pgx.ErrNoRows {
			return nil, domain.ENotFound("embed config")
		}
		return nil, err
	}
	return &c, nil
}

func (s *Store) UpsertTenantEmbedConfig(ctx context.Context, cfg *domain.TenantEmbedConfig) error {
	_, err := s.pool.Exec(ctx,
		`INSERT INTO tenant_embed_configs (tenant_id, secret_hash, allowed_origins, updated_at)
		 VALUES ($1, $2, $3, now())
		 ON CONFLICT (tenant_id) DO UPDATE SET
		   secret_hash = EXCLUDED.secret_hash,
		   allowed_origins = EXCLUDED.allowed_origins,
		   updated_at = now()`,
		cfg.TenantID, cfg.SecretHash, cfg.AllowedOrigins)
	return err
}

func (s *Store) ListTenants(ctx context.Context, f domain.TenantFilter, page domain.PageRequest) ([]*domain.Tenant, domain.PageInfo, error) {
	q := `SELECT ` + tenantCols + ` FROM tenants WHERE 1=1`
	args := []any{}
	n := 0
	add := func(cond string, v any) {
		n++
		q += fmt.Sprintf(" AND %s = $%d", cond, n)
		args = append(args, v)
	}
	if f.Status != "" {
		add("status", f.Status)
	}
	if f.Cloud != "" {
		add("cloud", f.Cloud)
	}
	if f.CellID != "" {
		add("cell_id::text", f.CellID)
	}
	if page.AfterID != nil {
		n++
		q += fmt.Sprintf(" AND id > $%d", n)
		args = append(args, *page.AfterID)
	}
	n++
	q += fmt.Sprintf(" ORDER BY id LIMIT $%d", n)
	args = append(args, page.Limit+1)
	rows, err := s.pool.Query(ctx, q, args...)
	if err != nil {
		return nil, domain.PageInfo{}, err
	}
	defer rows.Close()
	var out []*domain.Tenant
	for rows.Next() {
		t, err := scanTenant(rows)
		if err != nil {
			return nil, domain.PageInfo{}, err
		}
		out = append(out, t)
	}
	items, info := domain.BuildPage(out, page.Limit, func(t *domain.Tenant) uuid.UUID { return t.ID })
	return items, info, nil
}

func (s *Store) UpdateTenant(ctx context.Context, t *domain.Tenant, evs ...domain.OutboxEvent) error {
	quotas, _ := json.Marshal(t.Quotas)
	return s.platformTx(ctx, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE tenants SET display_name=$2, owner_email=$3, cell_id=$4, quotas=$5,
				platform_version=$6, auto_upgrade=$7, modules=$8, updated_at=$9,
				deleted_at=$10, deletion_scheduled_at=$11
			WHERE id=$1`,
			t.ID, t.DisplayName, t.OwnerEmail, t.CellID, quotas, t.PlatformVersion,
			t.AutoUpgrade, t.Modules, t.UpdatedAt, t.DeletedAt, t.DeletionScheduledAt)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("tenant")
		}
		return insertOutbox(ctx, tx, evs)
	})
}

func (s *Store) TransitionTenant(ctx context.Context, id uuid.UUID, from, to domain.TenantStatus, evs ...domain.OutboxEvent) error {
	if !domain.CanTransition(from, to) {
		return domain.EConflict("invalid tenant status transition " + string(from) + " -> " + string(to))
	}
	return s.platformTx(ctx, func(tx pgx.Tx) error {
		// CAS at the persistence boundary (IDN-FR-003 guards).
		ct, err := tx.Exec(ctx, `UPDATE tenants SET status=$3, updated_at=now() WHERE id=$1 AND status=$2`,
			id, string(from), string(to))
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			var cur string
			if err := tx.QueryRow(ctx, `SELECT status FROM tenants WHERE id=$1`, id).Scan(&cur); err != nil {
				return domain.ENotFound("tenant")
			}
			return domain.EConflict("tenant status is " + cur + ", expected " + string(from))
		}
		return insertOutbox(ctx, tx, evs)
	})
}

// --- cells ---

func (s *Store) CreateCell(ctx context.Context, c *domain.Cell) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO cells (id, name, cloud, region, capacity, tenant_count) VALUES ($1,$2,$3,$4,$5,$6)`,
		c.ID, c.Name, c.Cloud, c.Region, c.Capacity, c.TenantCount)
	return err
}

func (s *Store) ListCells(ctx context.Context) ([]*domain.Cell, error) {
	rows, err := s.pool.Query(ctx, `SELECT id, name, cloud, region, capacity, tenant_count, created_at, updated_at FROM cells ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*domain.Cell
	for rows.Next() {
		var c domain.Cell
		if err := rows.Scan(&c.ID, &c.Name, &c.Cloud, &c.Region, &c.Capacity, &c.TenantCount, &c.CreatedAt, &c.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, &c)
	}
	return out, nil
}

func (s *Store) ReserveCell(ctx context.Context, cellID uuid.UUID) error {
	ct, err := s.pool.Exec(ctx, `
		UPDATE cells SET tenant_count = tenant_count + 1, updated_at = now()
		WHERE id = $1 AND tenant_count < capacity`, cellID)
	if err != nil {
		return err
	}
	if ct.RowsAffected() == 0 {
		return domain.EConflict("cell at capacity")
	}
	return nil
}

func (s *Store) ReleaseCell(ctx context.Context, cellID uuid.UUID) error {
	_, err := s.pool.Exec(ctx, `
		UPDATE cells SET tenant_count = GREATEST(tenant_count - 1, 0), updated_at = now() WHERE id = $1`, cellID)
	return err
}

// --- tenant modules ---

func (s *Store) SetTenantModules(ctx context.Context, tenantID uuid.UUID, modules []string, version string) error {
	return s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		for _, m := range modules {
			id, _ := uuid.NewV7()
			if _, err := tx.Exec(ctx, `
				INSERT INTO tenant_modules (id, tenant_id, module, version, enabled)
				VALUES ($1,$2,$3,$4,true)
				ON CONFLICT (tenant_id, module) DO UPDATE SET version=$4, enabled=true, updated_at=now()`,
				id, tenantID, m, version); err != nil {
				return err
			}
		}
		return nil
	})
}

func (s *Store) DeleteTenantModules(ctx context.Context, tenantID uuid.UUID) error {
	return s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `DELETE FROM tenant_modules WHERE tenant_id = $1`, tenantID)
		return err
	})
}

func (s *Store) GetTenantModules(ctx context.Context, tenantID uuid.UUID) ([]string, error) {
	var out []string
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT module FROM tenant_modules WHERE tenant_id=$1 AND enabled ORDER BY module`, tenantID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var m string
			if err := rows.Scan(&m); err != nil {
				return err
			}
			out = append(out, m)
		}
		return rows.Err()
	})
	return out, err
}

// --- provisioning steps ---

func (s *Store) SaveProvisioningStep(ctx context.Context, r *domain.ProvisioningStep) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO provisioning_runs (id, tenant_id, workflow_id, step_index, step_name, status, attempt, error, compensation, started_at, finished_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
		ON CONFLICT (workflow_id, step_index) DO UPDATE SET
			status=EXCLUDED.status, attempt=EXCLUDED.attempt, error=EXCLUDED.error,
			compensation=EXCLUDED.compensation, started_at=EXCLUDED.started_at, finished_at=EXCLUDED.finished_at`,
		r.ID, r.TenantID, r.WorkflowID, r.StepIndex, r.StepName, string(r.Status), r.Attempt, r.Error,
		r.CompensationName, r.StartedAt, r.FinishedAt)
	return err
}

func (s *Store) ListProvisioningSteps(ctx context.Context, tenantID uuid.UUID, workflowID string) ([]*domain.ProvisioningStep, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, tenant_id, workflow_id, step_index, step_name, status, attempt, error, compensation, started_at, finished_at
		FROM provisioning_runs WHERE tenant_id=$1 AND workflow_id=$2 ORDER BY step_index`, tenantID, workflowID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*domain.ProvisioningStep
	for rows.Next() {
		var r domain.ProvisioningStep
		var status string
		if err := rows.Scan(&r.ID, &r.TenantID, &r.WorkflowID, &r.StepIndex, &r.StepName, &status,
			&r.Attempt, &r.Error, &r.CompensationName, &r.StartedAt, &r.FinishedAt); err != nil {
			return nil, err
		}
		r.Status = domain.StepStatus(status)
		out = append(out, &r)
	}
	return out, rows.Err()
}

// --- users ---

const userCols = `id, tenant_id, email, full_name, status, idp_subject, last_login_at, created_at, updated_at, deleted_at`

func scanUser(row pgx.Row) (*domain.User, error) {
	var u domain.User
	var status string
	err := row.Scan(&u.ID, &u.TenantID, &u.Email, &u.FullName, &status, &u.IdpSubject,
		&u.LastLoginAt, &u.CreatedAt, &u.UpdatedAt, &u.DeletedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, domain.ENotFound("user")
	}
	if err != nil {
		return nil, err
	}
	u.Status = domain.UserStatus(status)
	return &u, nil
}

func (s *Store) CreateUser(ctx context.Context, u *domain.User, evs ...domain.OutboxEvent) error {
	err := s.tenantTx(ctx, u.TenantID, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO users (`+userCols+`) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
			u.ID, u.TenantID, u.Email, u.FullName, string(u.Status), u.IdpSubject,
			u.LastLoginAt, u.CreatedAt, u.UpdatedAt, u.DeletedAt); err != nil {
			return err
		}
		return insertOutbox(ctx, tx, evs)
	})
	if isUnique(err) {
		return domain.EConflict("user email already exists in tenant")
	}
	return err
}

func (s *Store) getUserWhere(ctx context.Context, tenantID uuid.UUID, cond string, arg any) (*domain.User, error) {
	var u *domain.User
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		var err error
		u, err = scanUser(tx.QueryRow(ctx, `SELECT `+userCols+` FROM users WHERE `+cond, arg))
		return err
	})
	return u, err
}

func (s *Store) GetUser(ctx context.Context, tenantID, id uuid.UUID) (*domain.User, error) {
	return s.getUserWhere(ctx, tenantID, "id = $1", id)
}

func (s *Store) GetUserByEmail(ctx context.Context, tenantID uuid.UUID, email string) (*domain.User, error) {
	return s.getUserWhere(ctx, tenantID, "lower(email) = lower($1)", email)
}

func (s *Store) GetUserBySub(ctx context.Context, tenantID uuid.UUID, sub string) (*domain.User, error) {
	return s.getUserWhere(ctx, tenantID, "idp_subject = $1", sub)
}

func (s *Store) ListUsers(ctx context.Context, tenantID uuid.UUID, f domain.UserFilter, page domain.PageRequest) ([]*domain.User, domain.PageInfo, error) {
	var out []*domain.User
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		q := `SELECT ` + userCols + ` FROM users WHERE tenant_id=$1`
		args := []any{tenantID}
		if len(f.IDs) > 0 { // filter[id] batch hydration (bff-graphql loaders)
			args = append(args, f.IDs)
			q += fmt.Sprintf(` AND id = ANY($%d)`, len(args))
		}
		if page.AfterID != nil {
			args = append(args, *page.AfterID)
			q += fmt.Sprintf(` AND id > $%d`, len(args))
		}
		args = append(args, page.Limit+1)
		q += fmt.Sprintf(` ORDER BY id LIMIT $%d`, len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			u, err := scanUser(rows)
			if err != nil {
				return err
			}
			out = append(out, u)
		}
		return rows.Err()
	})
	if err != nil {
		return nil, domain.PageInfo{}, err
	}
	items, info := domain.BuildPage(out, page.Limit, func(u *domain.User) uuid.UUID { return u.ID })
	return items, info, nil
}

func (s *Store) UpdateUser(ctx context.Context, u *domain.User, evs ...domain.OutboxEvent) error {
	return s.tenantTx(ctx, u.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE users SET email=$2, full_name=$3, status=$4, idp_subject=$5, last_login_at=$6,
				updated_at=$7, deleted_at=$8
			WHERE id=$1`,
			u.ID, u.Email, u.FullName, string(u.Status), u.IdpSubject, u.LastLoginAt, u.UpdatedAt, u.DeletedAt)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("user")
		}
		return insertOutbox(ctx, tx, evs)
	})
}

// --- invitations ---

func (s *Store) CreateInvitation(ctx context.Context, inv *domain.Invitation, evs ...domain.OutboxEvent) error {
	return s.tenantTx(ctx, inv.TenantID, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO invitations (id, tenant_id, user_id, token_hash, expires_at, accepted_at, invalidated_at, created_at, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
			inv.ID, inv.TenantID, inv.UserID, inv.TokenHash, inv.ExpiresAt, inv.AcceptedAt,
			inv.InvalidatedAt, inv.CreatedAt, inv.UpdatedAt); err != nil {
			return err
		}
		return insertOutbox(ctx, tx, evs)
	})
}

// GetInvitationByTokenHash is pre-auth (public activation link): platform role.
func (s *Store) GetInvitationByTokenHash(ctx context.Context, tokenHash string) (*domain.Invitation, error) {
	var inv domain.Invitation
	err := s.platformTx(ctx, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `
			SELECT id, tenant_id, user_id, token_hash, expires_at, accepted_at, invalidated_at, created_at, updated_at
			FROM invitations WHERE token_hash=$1`, tokenHash).
			Scan(&inv.ID, &inv.TenantID, &inv.UserID, &inv.TokenHash, &inv.ExpiresAt,
				&inv.AcceptedAt, &inv.InvalidatedAt, &inv.CreatedAt, &inv.UpdatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.ENotFound("invitation")
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	return &inv, nil
}

func (s *Store) UpdateInvitation(ctx context.Context, inv *domain.Invitation, evs ...domain.OutboxEvent) error {
	return s.tenantTx(ctx, inv.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE invitations SET accepted_at=$2, invalidated_at=$3, updated_at=$4 WHERE id=$1`,
			inv.ID, inv.AcceptedAt, inv.InvalidatedAt, inv.UpdatedAt)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("invitation")
		}
		return insertOutbox(ctx, tx, evs)
	})
}

func (s *Store) InvalidateInvitations(ctx context.Context, tenantID, userID uuid.UUID, now time.Time) error {
	return s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			UPDATE invitations SET invalidated_at=$3, updated_at=$3
			WHERE tenant_id=$1 AND user_id=$2 AND accepted_at IS NULL AND invalidated_at IS NULL`,
			tenantID, userID, now)
		return err
	})
}

// --- service accounts ---

const saCols = `id, tenant_id, name, secret_hash, old_secret_hash, old_secret_expires_at, scopes, expires_at, last_used_at, revoked_at, created_at, updated_at`

func scanSA(row pgx.Row) (*domain.ServiceAccount, error) {
	var sa domain.ServiceAccount
	err := row.Scan(&sa.ID, &sa.TenantID, &sa.Name, &sa.SecretHash, &sa.OldSecretHash,
		&sa.OldSecretExpiresAt, &sa.Scopes, &sa.ExpiresAt, &sa.LastUsedAt, &sa.RevokedAt,
		&sa.CreatedAt, &sa.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, domain.ENotFound("service account")
	}
	if err != nil {
		return nil, err
	}
	return &sa, nil
}

func (s *Store) CreateServiceAccount(ctx context.Context, sa *domain.ServiceAccount, evs ...domain.OutboxEvent) error {
	err := s.tenantTx(ctx, sa.TenantID, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO service_accounts (`+saCols+`) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
			sa.ID, sa.TenantID, sa.Name, sa.SecretHash, sa.OldSecretHash, sa.OldSecretExpiresAt,
			sa.Scopes, sa.ExpiresAt, sa.LastUsedAt, sa.RevokedAt, sa.CreatedAt, sa.UpdatedAt); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO api_key_index (sa_id, tenant_id) VALUES ($1,$2)`, sa.ID, sa.TenantID); err != nil {
			return err
		}
		return insertOutbox(ctx, tx, evs)
	})
	if isUnique(err) {
		return domain.EConflict("service account name already exists")
	}
	return err
}

func (s *Store) GetServiceAccount(ctx context.Context, tenantID, id uuid.UUID) (*domain.ServiceAccount, error) {
	var sa *domain.ServiceAccount
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		var err error
		sa, err = scanSA(tx.QueryRow(ctx, `SELECT `+saCols+` FROM service_accounts WHERE id=$1`, id))
		return err
	})
	return sa, err
}

func (s *Store) ResolveAPIKeyTenant(ctx context.Context, saID uuid.UUID) (uuid.UUID, error) {
	var tid uuid.UUID
	err := s.pool.QueryRow(ctx, `SELECT tenant_id FROM api_key_index WHERE sa_id=$1`, saID).Scan(&tid)
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, domain.ENotFound("api key")
	}
	return tid, err
}

func (s *Store) ListServiceAccounts(ctx context.Context, tenantID uuid.UUID, page domain.PageRequest) ([]*domain.ServiceAccount, domain.PageInfo, error) {
	var out []*domain.ServiceAccount
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		q := `SELECT ` + saCols + ` FROM service_accounts WHERE tenant_id=$1`
		args := []any{tenantID}
		if page.AfterID != nil {
			q += ` AND id > $2 ORDER BY id LIMIT $3`
			args = append(args, *page.AfterID, page.Limit+1)
		} else {
			q += ` ORDER BY id LIMIT $2`
			args = append(args, page.Limit+1)
		}
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			sa, err := scanSA(rows)
			if err != nil {
				return err
			}
			out = append(out, sa)
		}
		return rows.Err()
	})
	if err != nil {
		return nil, domain.PageInfo{}, err
	}
	items, info := domain.BuildPage(out, page.Limit, func(sa *domain.ServiceAccount) uuid.UUID { return sa.ID })
	return items, info, nil
}

func (s *Store) CountServiceAccounts(ctx context.Context, tenantID uuid.UUID) (int, error) {
	var n int
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT count(*) FROM service_accounts WHERE tenant_id=$1 AND revoked_at IS NULL`, tenantID).Scan(&n)
	})
	return n, err
}

func (s *Store) UpdateServiceAccount(ctx context.Context, sa *domain.ServiceAccount, evs ...domain.OutboxEvent) error {
	return s.tenantTx(ctx, sa.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE service_accounts SET secret_hash=$2, old_secret_hash=$3, old_secret_expires_at=$4,
				scopes=$5, expires_at=$6, last_used_at=$7, revoked_at=$8, updated_at=$9
			WHERE id=$1`,
			sa.ID, sa.SecretHash, sa.OldSecretHash, sa.OldSecretExpiresAt, sa.Scopes,
			sa.ExpiresAt, sa.LastUsedAt, sa.RevokedAt, sa.UpdatedAt)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return domain.ENotFound("service account")
		}
		return insertOutbox(ctx, tx, evs)
	})
}

// --- agent principals ---

func (s *Store) UpsertAgentPrincipal(ctx context.Context, a *domain.AgentPrincipal, evs ...domain.OutboxEvent) error {
	return s.tenantTx(ctx, a.TenantID, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO agent_principals (id, tenant_id, agent_id, agent_version, scopes, autonomous_allowed, eval_gate_ok, status, created_at, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
			ON CONFLICT (tenant_id, agent_id, agent_version) DO UPDATE SET
				scopes=EXCLUDED.scopes, autonomous_allowed=EXCLUDED.autonomous_allowed,
				eval_gate_ok=EXCLUDED.eval_gate_ok, status=EXCLUDED.status, updated_at=EXCLUDED.updated_at`,
			a.ID, a.TenantID, a.AgentID, a.AgentVersion, a.Scopes, a.AutonomousAllowed,
			a.EvalGateOK, string(a.Status), a.CreatedAt, a.UpdatedAt); err != nil {
			return err
		}
		return insertOutbox(ctx, tx, evs)
	})
}

func (s *Store) GetAgentPrincipal(ctx context.Context, tenantID uuid.UUID, agentID, version string) (*domain.AgentPrincipal, error) {
	var a domain.AgentPrincipal
	var status string
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `
			SELECT id, tenant_id, agent_id, agent_version, scopes, autonomous_allowed, eval_gate_ok, status, created_at, updated_at
			FROM agent_principals WHERE tenant_id=$1 AND agent_id=$2 AND agent_version=$3`,
			tenantID, agentID, version).
			Scan(&a.ID, &a.TenantID, &a.AgentID, &a.AgentVersion, &a.Scopes, &a.AutonomousAllowed,
				&a.EvalGateOK, &status, &a.CreatedAt, &a.UpdatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.ENotFound("agent principal")
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	a.Status = domain.AgentPrincipalStatus(status)
	return &a, nil
}

func (s *Store) ListAgentPrincipals(ctx context.Context, tenantID uuid.UUID) ([]*domain.AgentPrincipal, error) {
	var out []*domain.AgentPrincipal
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, agent_id, agent_version, scopes, autonomous_allowed, eval_gate_ok, status, created_at, updated_at
			FROM agent_principals WHERE tenant_id=$1 ORDER BY agent_id, agent_version`, tenantID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var a domain.AgentPrincipal
			var status string
			if err := rows.Scan(&a.ID, &a.TenantID, &a.AgentID, &a.AgentVersion, &a.Scopes,
				&a.AutonomousAllowed, &a.EvalGateOK, &status, &a.CreatedAt, &a.UpdatedAt); err != nil {
				return err
			}
			a.Status = domain.AgentPrincipalStatus(status)
			out = append(out, &a)
		}
		return rows.Err()
	})
	return out, err
}

// --- signing keys ---

func (s *Store) SaveSigningKey(ctx context.Context, k *domain.SigningKey, evs ...domain.OutboxEvent) error {
	return s.platformTx(ctx, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
			INSERT INTO signing_keys (kid, alg, vault_ref, public_key_pem, not_before, retired_at, created_at, updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			k.KID, k.Alg, k.VaultRef, k.PublicKeyPEM, k.NotBefore, k.RetiredAt, k.CreatedAt, k.UpdatedAt); err != nil {
			return err
		}
		return insertOutbox(ctx, tx, evs)
	})
}

func (s *Store) ListSigningKeys(ctx context.Context) ([]*domain.SigningKey, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT kid, alg, vault_ref, public_key_pem, not_before, retired_at, created_at, updated_at
		FROM signing_keys ORDER BY not_before`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*domain.SigningKey
	for rows.Next() {
		var k domain.SigningKey
		if err := rows.Scan(&k.KID, &k.Alg, &k.VaultRef, &k.PublicKeyPEM, &k.NotBefore,
			&k.RetiredAt, &k.CreatedAt, &k.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, &k)
	}
	return out, rows.Err()
}

func (s *Store) UpdateSigningKey(ctx context.Context, k *domain.SigningKey) error {
	ct, err := s.pool.Exec(ctx, `
		UPDATE signing_keys SET retired_at=$2, updated_at=$3 WHERE kid=$1`,
		k.KID, k.RetiredAt, k.UpdatedAt)
	if err != nil {
		return err
	}
	if ct.RowsAffected() == 0 {
		return domain.ENotFound("signing key")
	}
	return nil
}

// --- idempotency ---

func (s *Store) GetIdempotency(ctx context.Context, tenantID uuid.UUID, key string) (*domain.IdempotencyRecord, error) {
	var rec domain.IdempotencyRecord
	err := s.tenantTx(ctx, tenantID, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `
			SELECT tenant_id, key, request_hash, status, body, created_at FROM idempotency_keys
			WHERE tenant_id=$1 AND key=$2 AND created_at > $3`,
			tenantID, key, time.Now().UTC().Add(-domain.IdempotencyTTL)).
			Scan(&rec.TenantID, &rec.Key, &rec.RequestHash, &rec.Status, &rec.Body, &rec.CreatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.ENotFound("idempotency key")
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	return &rec, nil
}

func (s *Store) PutIdempotency(ctx context.Context, rec *domain.IdempotencyRecord) error {
	return s.tenantTx(ctx, rec.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO idempotency_keys (tenant_id, key, request_hash, status, body, created_at)
			VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (tenant_id, key) DO NOTHING`,
			rec.TenantID, rec.Key, rec.RequestHash, rec.Status, rec.Body, rec.CreatedAt)
		return err
	})
}

// --- outbox ---

func (s *Store) AppendOutbox(ctx context.Context, evs ...domain.OutboxEvent) error {
	return s.platformTx(ctx, func(tx pgx.Tx) error { return insertOutbox(ctx, tx, evs) })
}

func (s *Store) ListOutbox(ctx context.Context, limit int) ([]*domain.OutboxEvent, error) {
	var out []*domain.OutboxEvent
	err := s.platformTx(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT event_id, event_type, tenant_id, actor, via_agent, resource_urn, occurred_at, trace_id, payload, published_at
			FROM outbox WHERE published_at IS NULL ORDER BY occurred_at LIMIT $1`, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var ev domain.OutboxEvent
			var actor, payload, via []byte
			if err := rows.Scan(&ev.EventID, &ev.EventType, &ev.TenantID, &actor, &via,
				&ev.ResourceURN, &ev.OccurredAt, &ev.TraceID, &payload, &ev.PublishedAt); err != nil {
				return err
			}
			if err := json.Unmarshal(actor, &ev.Actor); err != nil {
				return err
			}
			if len(via) > 0 {
				var v domain.ViaAgent
				if err := json.Unmarshal(via, &v); err != nil {
					return err
				}
				ev.ViaAgent = &v
			}
			if err := json.Unmarshal(payload, &ev.Payload); err != nil {
				return err
			}
			out = append(out, &ev)
		}
		return rows.Err()
	})
	return out, err
}

func (s *Store) MarkOutboxPublished(ctx context.Context, eventIDs []uuid.UUID, at time.Time) error {
	return s.platformTx(ctx, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `UPDATE outbox SET published_at=$2 WHERE event_id = ANY($1)`, eventIDs, at)
		return err
	})
}
