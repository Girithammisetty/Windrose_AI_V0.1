package store

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
)

// ---- Dispositions (CASE-FR-020) ---------------------------------------------

func (s *PG) CreateDisposition(ctx context.Context, d *domain.Disposition) error {
	err := s.withTenant(ctx, d.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO dispositions (id, tenant_id, workspace_id, code, label, category, requires_note, active)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			d.ID, d.TenantID, d.WorkspaceID, d.Code, d.Label, d.Category, d.RequiresNote, d.Active)
		return err
	})
	if isUniqueViolation(err) {
		return ErrCodeConflict
	}
	return err
}

func (s *PG) GetDisposition(ctx context.Context, tenant, id uuid.UUID) (*domain.Disposition, error) {
	var d domain.Disposition
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT id, tenant_id, workspace_id, code, label, category, requires_note, active, created_at, updated_at
			FROM dispositions WHERE id=$1`, id).
			Scan(&d.ID, &d.TenantID, &d.WorkspaceID, &d.Code, &d.Label, &d.Category, &d.RequiresNote, &d.Active, &d.CreatedAt, &d.UpdatedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return &d, err
}

func (s *PG) ListDispositions(ctx context.Context, tenant, ws uuid.UUID) ([]*domain.Disposition, error) {
	var out []*domain.Disposition
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, workspace_id, code, label, category, requires_note, active, created_at, updated_at
			FROM dispositions WHERE workspace_id=$1 ORDER BY code`, ws)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var d domain.Disposition
			if err := rows.Scan(&d.ID, &d.TenantID, &d.WorkspaceID, &d.Code, &d.Label, &d.Category, &d.RequiresNote, &d.Active, &d.CreatedAt, &d.UpdatedAt); err != nil {
				return err
			}
			out = append(out, &d)
		}
		return rows.Err()
	})
	return out, err
}

func (s *PG) UpdateDisposition(ctx context.Context, d *domain.Disposition) error {
	return s.withTenant(ctx, d.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE dispositions SET label=$2, category=$3, requires_note=$4, active=$5, updated_at=now() WHERE id=$1`,
			d.ID, d.Label, d.Category, d.RequiresNote, d.Active)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// ---- Custom fields (CASE-FR-022) --------------------------------------------

func (s *PG) CreateField(ctx context.Context, f *domain.CaseField) error {
	err := s.withTenant(ctx, f.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO case_fields (id, tenant_id, workspace_id, query_urn, name, data_type, purpose, field_meta)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			f.ID, f.TenantID, f.WorkspaceID, f.QueryURN, f.Name, f.DataType, f.Purpose, mustJSON(f.FieldMeta))
		return err
	})
	if isUniqueViolation(err) {
		return ErrCodeConflict
	}
	return err
}

// ListFields returns fields for a workspace, optionally scoped to a query_urn
// and purpose mode. Query-scoped fields shadow workspace-wide ones by name
// (CASE-FR-022, AC-12).
func (s *PG) ListFields(ctx context.Context, tenant, ws uuid.UUID, queryURN string, purposes []int16) ([]*domain.CaseField, error) {
	var out []*domain.CaseField
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, workspace_id, query_urn, name, data_type, purpose, field_meta, created_at, updated_at
			FROM case_fields
			WHERE workspace_id=$1 AND deleted_at IS NULL AND (query_urn='' OR query_urn=$2)
			ORDER BY name, (query_urn <> '') DESC`, ws, queryURN)
		if err != nil {
			return err
		}
		defer rows.Close()
		byName := map[string]*domain.CaseField{}
		var order []string
		for rows.Next() {
			var f domain.CaseField
			var meta []byte
			if err := rows.Scan(&f.ID, &f.TenantID, &f.WorkspaceID, &f.QueryURN, &f.Name, &f.DataType, &f.Purpose, &meta, &f.CreatedAt, &f.UpdatedAt); err != nil {
				return err
			}
			_ = json.Unmarshal(meta, &f.FieldMeta)
			if !purposeMatch(f.Purpose, purposes) {
				continue
			}
			// Query-scoped (query_urn != '') shadows workspace-wide.
			if ex, ok := byName[f.Name]; ok {
				if ex.QueryURN != "" || f.QueryURN == "" {
					continue
				}
			} else {
				order = append(order, f.Name)
			}
			ff := f
			byName[f.Name] = &ff
		}
		if err := rows.Err(); err != nil {
			return err
		}
		for _, n := range order {
			out = append(out, byName[n])
		}
		return nil
	})
	return out, err
}

func purposeMatch(p int16, want []int16) bool {
	if len(want) == 0 {
		return true
	}
	for _, w := range want {
		if p == w || p == domain.PurposeBoth {
			return true
		}
	}
	return false
}

// DeleteField soft-deletes a field. Blocked when open cases carry a value
// unless orphan=true, which strips the key from open cases (BR-8).
func (s *PG) DeleteField(ctx context.Context, tenant, id uuid.UUID, orphan bool) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var name string
		var ws uuid.UUID
		if err := tx.QueryRow(ctx, `SELECT name, workspace_id FROM case_fields WHERE id=$1 AND deleted_at IS NULL`, id).Scan(&name, &ws); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return ErrNotFound
			}
			return err
		}
		var inUse int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM cases WHERE workspace_id=$1 AND status<>4 AND deleted_at IS NULL AND custom_fields ? $2`, ws, name).Scan(&inUse); err != nil {
			return err
		}
		if inUse > 0 && !orphan {
			return ErrFieldInUse
		}
		if inUse > 0 && orphan {
			if _, err := tx.Exec(ctx, `UPDATE cases SET custom_fields = custom_fields - $2, updated_at=now() WHERE workspace_id=$1 AND status<>4 AND custom_fields ? $2`, ws, name); err != nil {
				return err
			}
		}
		_, err := tx.Exec(ctx, `UPDATE case_fields SET deleted_at=now() WHERE id=$1`, id)
		return err
	})
}

// GetField loads a single (non-deleted) custom field, tenant-scoped
// (CASE-FR-022).
func (s *PG) GetField(ctx context.Context, tenant, id uuid.UUID) (*domain.CaseField, error) {
	var f domain.CaseField
	var meta []byte
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT id, tenant_id, workspace_id, query_urn, name, data_type, purpose, field_meta, created_at, updated_at
			FROM case_fields WHERE id=$1 AND deleted_at IS NULL`, id).
			Scan(&f.ID, &f.TenantID, &f.WorkspaceID, &f.QueryURN, &f.Name, &f.DataType, &f.Purpose, &meta, &f.CreatedAt, &f.UpdatedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal(meta, &f.FieldMeta)
	return &f, nil
}

// UpdateField mutates the editable subset of a custom field (purpose +
// field_meta). The field key (name), data_type and query_urn scope are
// immutable and are enforced by the handler (CASE-FR-022).
func (s *PG) UpdateField(ctx context.Context, f *domain.CaseField) error {
	return s.withTenant(ctx, f.TenantID, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `
			UPDATE case_fields SET purpose=$2, field_meta=$3, updated_at=now() WHERE id=$1 AND deleted_at IS NULL`,
			f.ID, f.Purpose, mustJSON(f.FieldMeta))
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// ---- SLA policy (CASE-FR-012) -----------------------------------------------

func (s *PG) GetSLAPolicy(ctx context.Context, tenant, ws uuid.UUID) (domain.SLAPolicy, error) {
	p := domain.DefaultSLAPolicy(tenant, ws)
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var warn time.Duration
		var escalate *uuid.UUID
		err := tx.QueryRow(ctx, `
			SELECT warn_before, on_breach, escalate_to, max_reassign_count FROM sla_policies WHERE workspace_id=$1`, ws).
			Scan(&warn, &p.OnBreach, &escalate, &p.MaxReassignCount)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		p.WarnBefore = warn
		p.EscalateTo = escalate
		return nil
	})
	return p, err
}

func (s *PG) PutSLAPolicy(ctx context.Context, p domain.SLAPolicy) error {
	return s.withTenant(ctx, p.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO sla_policies (tenant_id, workspace_id, warn_before, on_breach, escalate_to, max_reassign_count)
			VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (tenant_id, workspace_id) DO UPDATE
			SET warn_before=$3, on_breach=$4, escalate_to=$5, max_reassign_count=$6, updated_at=now()`,
			p.TenantID, p.WorkspaceID, p.WarnBefore, p.OnBreach, p.EscalateTo, p.MaxReassignCount)
		return err
	})
}

// ---- Comments (CASE-FR-024) -------------------------------------------------

// AddComment inserts a comment, appends a timeline entry and emits
// case.comment.added — all atomically (CASE-FR-024/025).
func (s *PG) AddComment(ctx context.Context, op domain.Op, caseID uuid.UUID, body string) (*domain.Comment, error) {
	c := &domain.Comment{ID: domain.NewID(), TenantID: op.Tenant, CaseID: caseID, AuthorID: op.UserID, Body: body, CreatedAt: time.Now().UTC()}
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT true FROM cases WHERE id=$1 AND deleted_at IS NULL`, caseID).Scan(&exists); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return ErrNotFound
			}
			return err
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO case_comments (id, tenant_id, case_id, author_id, body, created_at)
			VALUES ($1,$2,$3,$4,$5,$6)`, c.ID, c.TenantID, caseID, c.AuthorID, body, c.CreatedAt); err != nil {
			return err
		}
		urn := events.CaseURN(op.Tenant, caseID)
		act := domain.Activity{ID: domain.NewID(), CaseID: caseID, EventType: events.EvCommentAdded,
			ActorType: op.Actor.Type, ActorID: op.Actor.ID, ViaAgent: op.ViaAgent,
			NewValue: map[string]any{"comment_id": c.ID.String()}, OccurredAt: time.Now().UTC()}
		if err := insertActivitiesTx(ctx, tx, op.Tenant, []domain.Activity{act}); err != nil {
			return err
		}
		env := events.NewEnvelope(events.EvCommentAdded, op, urn, map[string]any{"comment_id": c.ID.String()})
		return insertOutboxTx(ctx, tx, []events.Envelope{env})
	})
	if err != nil {
		return nil, err
	}
	return c, nil
}

func (s *PG) GetComment(ctx context.Context, tenant, id uuid.UUID) (*domain.Comment, error) {
	var c domain.Comment
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT id, tenant_id, case_id, author_id, body, edited_at, created_at, deleted_at
			FROM case_comments WHERE id=$1`, id).
			Scan(&c.ID, &c.TenantID, &c.CaseID, &c.AuthorID, &c.Body, &c.EditedAt, &c.CreatedAt, &c.DeletedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return &c, err
}

// EditComment updates a comment body (author within 15 min; enforced by caller).
func (s *PG) EditComment(ctx context.Context, tenant, id uuid.UUID, body string) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE case_comments SET body=$2, edited_at=now() WHERE id=$1 AND deleted_at IS NULL`, id, body)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

func (s *PG) DeleteComment(ctx context.Context, tenant, id uuid.UUID) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		ct, err := tx.Exec(ctx, `UPDATE case_comments SET deleted_at=now() WHERE id=$1 AND deleted_at IS NULL`, id)
		if err != nil {
			return err
		}
		if ct.RowsAffected() == 0 {
			return ErrNotFound
		}
		return nil
	})
}

// ---- Timeline (CASE-FR-025) -------------------------------------------------

// ListTimeline returns the merged event+comment timeline (comment.added events
// are already appended to case_events on write). Paginated by occurred_at.
func (s *PG) ListTimeline(ctx context.Context, tenant, caseID uuid.UUID, limit int, before *time.Time) ([]domain.Activity, error) {
	limit = ClampLimit(limit)
	var out []domain.Activity
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		// Confirm the case is visible (RLS/404).
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT true FROM cases WHERE id=$1 AND deleted_at IS NULL`, caseID).Scan(&exists); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return ErrNotFound
			}
			return err
		}
		q := `SELECT id, case_id, event_type, actor_type, actor_id, via_agent, proposal_urn, old_value, new_value, occurred_at
			FROM case_events WHERE case_id=$1`
		args := []any{caseID}
		if before != nil {
			args = append(args, *before)
			q += ` AND occurred_at < $2`
		}
		args = append(args, limit)
		q += ` ORDER BY occurred_at DESC LIMIT $` + strconv.Itoa(len(args))
		rows, err := tx.Query(ctx, q, args...)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var a domain.Activity
			var viaAgent, oldV, newV []byte
			var proposalURN *string
			if err := rows.Scan(&a.ID, &a.CaseID, &a.EventType, &a.ActorType, &a.ActorID, &viaAgent, &proposalURN, &oldV, &newV, &a.OccurredAt); err != nil {
				return err
			}
			if len(viaAgent) > 0 {
				var va domain.ViaAgent
				if json.Unmarshal(viaAgent, &va) == nil {
					a.ViaAgent = &va
				}
			}
			if proposalURN != nil {
				a.ProposalURN = *proposalURN
			}
			if len(oldV) > 0 {
				_ = json.Unmarshal(oldV, &a.OldValue)
			}
			if len(newV) > 0 {
				_ = json.Unmarshal(newV, &a.NewValue)
			}
			out = append(out, a)
		}
		return rows.Err()
	})
	return out, err
}

// ---- Operations (CASE-FR-030/044) -------------------------------------------

// Operation is an async bulk/export operation record.
type Operation struct {
	ID          uuid.UUID
	TenantID    uuid.UUID
	WorkspaceID uuid.UUID
	Kind        string
	Status      string
	Succeeded   int
	Failed      int
	Total       int
	Result      map[string]any
	CreatedBy   string
	CreatedAt   time.Time
}

func (s *PG) CreateOperation(ctx context.Context, op *Operation) error {
	return s.withTenant(ctx, op.TenantID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO operations (id, tenant_id, workspace_id, kind, status, total, created_by)
			VALUES ($1,$2,$3,$4,$5,$6,$7)`, op.ID, op.TenantID, op.WorkspaceID, op.Kind, op.Status, op.Total, op.CreatedBy)
		return err
	})
}

func (s *PG) UpdateOperation(ctx context.Context, tenant, id uuid.UUID, status string, succeeded, failed int, result map[string]any) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			UPDATE operations SET status=$2, succeeded=$3, failed=$4, result=$5, updated_at=now() WHERE id=$1`,
			id, status, succeeded, failed, mustJSON(result))
		return err
	})
}

func (s *PG) GetOperation(ctx context.Context, tenant, id uuid.UUID) (*Operation, error) {
	var o Operation
	var result []byte
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `
			SELECT id, tenant_id, workspace_id, kind, status, succeeded, failed, total, result, created_by, created_at
			FROM operations WHERE id=$1`, id).
			Scan(&o.ID, &o.TenantID, &o.WorkspaceID, &o.Kind, &o.Status, &o.Succeeded, &o.Failed, &o.Total, &result, &o.CreatedBy, &o.CreatedAt)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal(result, &o.Result)
	return &o, nil
}

// ---- Applied proposals (CASE-FR-051, BR-9) ----------------------------------

// PutAppliedProposal records a proposal application for idempotent replay
// (CASE-FR-051, BR-9).
func (s *PG) PutAppliedProposal(ctx context.Context, tenant uuid.UUID, proposalURN string, caseID uuid.UUID, response map[string]any) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO applied_proposals (tenant_id, proposal_urn, case_id, response)
			VALUES ($1,$2,$3,$4) ON CONFLICT (tenant_id, proposal_urn) DO NOTHING`,
			tenant, proposalURN, caseID, mustJSON(response))
		return err
	})
}

func (s *PG) GetAppliedProposal(ctx context.Context, tenant uuid.UUID, proposalURN string) (map[string]any, bool, error) {
	var raw []byte
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT response FROM applied_proposals WHERE tenant_id=$1 AND proposal_urn=$2`, tenant, proposalURN).Scan(&raw)
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	var out map[string]any
	_ = json.Unmarshal(raw, &out)
	return out, true, nil
}

