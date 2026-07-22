package store

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/datacern-ai/case-service/internal/domain"
	"github.com/datacern-ai/case-service/internal/events"
)

// DueTimers returns pending timers whose fire_at has passed, across tenants
// (platform role). The SLA sweep worker then fires each one under its tenant
// context (CASE-FR-012). Durable: timer state is in Postgres, so a restart
// resumes exactly where it left off (AC-4).
func (s *PG) DueTimers(ctx context.Context, now time.Time, limit int) ([]SLADueTimer, error) {
	var out []SLADueTimer
	err := s.withPlatform(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT tenant_id, case_id, kind, case_version FROM sla_timers
			WHERE status='pending' AND fire_at <= $1 ORDER BY fire_at LIMIT $2`, now, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var t SLADueTimer
			if err := rows.Scan(&t.TenantID, &t.CaseID, &t.Kind, &t.CaseVersion); err != nil {
				return err
			}
			out = append(out, t)
		}
		return rows.Err()
	})
	return out, err
}

// terminalForSLA reports whether the SLA timer is moot (case no longer active).
func terminalForSLA(st domain.Status) bool {
	return st == domain.StatusResolved || st == domain.StatusClosed || st == domain.StatusUnassigned
}

// FireWarnTimer emits case.sla.warning if the case is still active, else cancels
// the warn timer (CASE-FR-012). Idempotent: the timer flips pending→fired under
// a row lock.
func (s *PG) FireWarnTimer(ctx context.Context, tenant, caseID uuid.UUID) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var status string
		if err := tx.QueryRow(ctx, `SELECT status FROM sla_timers WHERE case_id=$1 AND kind='warn' FOR UPDATE`, caseID).Scan(&status); err != nil {
			if err == pgx.ErrNoRows {
				return nil
			}
			return err
		}
		if status != "pending" {
			return nil
		}
		c, err := scanCase(tx.QueryRow(ctx, `SELECT `+caseCols+` FROM cases WHERE id=$1 AND deleted_at IS NULL FOR UPDATE`, caseID))
		if err != nil {
			return err
		}
		if terminalForSLA(c.Status) {
			_, err := tx.Exec(ctx, `UPDATE sla_timers SET status='cancelled', updated_at=now() WHERE case_id=$1 AND kind='warn'`, caseID)
			return err
		}
		// Emitted-envelope actor: the master envelope (MASTER-FR-031) constrains
		// actor.type to {user,service,agent,platform}; a background SLA sweep is
		// the service acting autonomously, so it emits actor={service,case-service}
		// (aligned with identity/rbac). The timeline activity below still records
		// the finer-grained "sla" sub-actor for display.
		op := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "service", ID: "case-service"}}
		urn := events.CaseURN(tenant, caseID)
		env := events.NewEnvelope(events.EvSLAWarning, op, urn, map[string]any{
			"case_number": c.CaseNumber, "due_date": c.DueDate, "assignee": strOrNil(c.AssignedToID),
		})
		if err := insertOutboxTx(ctx, tx, []events.Envelope{env}); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `UPDATE sla_timers SET status='fired', updated_at=now() WHERE case_id=$1 AND kind='warn'`, caseID)
		return err
	})
}

// FireDueTimer applies the SLA breach policy at due_date (CASE-FR-012, AC-3):
// auto_unassign (default), escalate, or notify_only. Auto-unassign performs the
// same transition as a manual unassign with actor system/sla, bumps
// case_version, appends the timeline entry and emits case.sla.breached +
// case.unassigned{reason:sla_breach}. Never fires on resolved/closed (BR-4).
func (s *PG) FireDueTimer(ctx context.Context, tenant, caseID uuid.UUID, policy domain.SLAPolicy) error {
	return s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var status string
		if err := tx.QueryRow(ctx, `SELECT status FROM sla_timers WHERE case_id=$1 AND kind='due' FOR UPDATE`, caseID).Scan(&status); err != nil {
			if err == pgx.ErrNoRows {
				return nil
			}
			return err
		}
		if status != "pending" {
			return nil
		}
		c, err := scanCase(tx.QueryRow(ctx, `SELECT `+caseCols+` FROM cases WHERE id=$1 AND deleted_at IS NULL FOR UPDATE`, caseID))
		if err != nil {
			return err
		}
		cancelTimers := func() error {
			_, e := tx.Exec(ctx, `UPDATE sla_timers SET status='cancelled', updated_at=now() WHERE case_id=$1 AND status='pending'`, caseID)
			return e
		}
		if terminalForSLA(c.Status) {
			return cancelTimers()
		}
		// Emitted-envelope actor: the master envelope (MASTER-FR-031) constrains
		// actor.type to {user,service,agent,platform}; a background SLA sweep is
		// the service acting autonomously, so it emits actor={service,case-service}
		// (aligned with identity/rbac). The timeline activity below still records
		// the finer-grained "sla" sub-actor for display.
		op := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "service", ID: "case-service"}}
		urn := events.CaseURN(tenant, caseID)
		var envs []events.Envelope
		var acts []domain.Activity
		now := time.Now().UTC()

		breachEnv := events.NewEnvelope(events.EvSLABreached, op, urn, map[string]any{
			"case_number": c.CaseNumber, "on_breach": policy.OnBreach, "reassign_count": c.ReassignCount,
		})
		envs = append(envs, breachEnv)

		autoUnassign := policy.OnBreach == domain.BreachAutoUnassign && c.ReassignCount < policy.MaxReassignCount
		switch {
		case autoUnassign:
			if err := c.Unassign(); err != nil {
				return err
			}
			// Each SLA-driven recycle counts toward the ceiling (CASE-FR-012:
			// "max_reassign_count (default 3, then always escalate)"). The count
			// is advanced here — not only on a mid-work reassign — because the SLA
			// cycle is assign→breach→auto_unassign→manager reassigns from
			// UNASSIGNED, and that reassign path must also drive escalation. Once
			// reassign_count reaches max, the branch below escalates instead of
			// unassigning again, so the ladder is reachable (regression: it was
			// stuck at 0 and auto-unassigned indefinitely).
			c.ReassignCount++
			acts = append(acts, domain.Activity{ID: domain.NewID(), CaseID: caseID, EventType: events.EvUnassigned,
				ActorType: "system", ActorID: "sla", NewValue: map[string]any{"status": c.Status.String(), "reason": domain.ReasonSLABreach, "reassign_count": c.ReassignCount}, OccurredAt: now})
			envs = append(envs, events.NewEnvelope(events.EvUnassigned, op, urn, map[string]any{
				"case_number": c.CaseNumber, "reason": domain.ReasonSLABreach}))
		case policy.OnBreach == domain.BreachEscalate || (policy.OnBreach == domain.BreachAutoUnassign && c.ReassignCount >= policy.MaxReassignCount):
			old := c.Severity
			c.Severity = domain.BumpSeverity(c.Severity)
			acts = append(acts, domain.Activity{ID: domain.NewID(), CaseID: caseID, EventType: events.EvEscalated,
				ActorType: "system", ActorID: "sla", OldValue: map[string]any{"severity": old},
				NewValue: map[string]any{"severity": c.Severity, "escalate_to": strOrNilPtr(policy.EscalateTo)}, OccurredAt: now})
			envs = append(envs, events.NewEnvelope(events.EvEscalated, op, urn, map[string]any{
				"case_number": c.CaseNumber, "severity": c.Severity, "escalate_to": strOrNilPtr(policy.EscalateTo), "reason": "sla_breach"}))
		default: // notify_only
			acts = append(acts, domain.Activity{ID: domain.NewID(), CaseID: caseID, EventType: events.EvSLABreached,
				ActorType: "system", ActorID: "sla", NewValue: map[string]any{"on_breach": policy.OnBreach}, OccurredAt: now})
		}

		c.CaseVersion++
		c.UpdatedAt = now
		if _, err := tx.Exec(ctx, `
			UPDATE cases SET status=$2, severity=$3, assigned_to_id=$4, assigned_to_at=$5, reassign_count=$6, case_version=$7, updated_at=$8 WHERE id=$1`,
			c.ID, c.Status, c.Severity, c.AssignedToID, c.AssignedToAt, c.ReassignCount, c.CaseVersion, c.UpdatedAt); err != nil {
			return err
		}
		if err := insertActivitiesTx(ctx, tx, tenant, acts); err != nil {
			return err
		}
		if err := insertOutboxTx(ctx, tx, envs); err != nil {
			return err
		}
		return cancelTimers()
	})
}

// ---- Projection reads (search indexer + reindex, CASE-FR-041/043) -----------

// CaseCommentText returns the concatenated comment bodies for a case (the
// analyzed comment_text field in the OpenSearch mapping, CASE-FR-040).
func (s *PG) CaseCommentText(ctx context.Context, tenant, caseID uuid.UUID) (string, error) {
	var text string
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT body FROM case_comments WHERE case_id=$1 AND deleted_at IS NULL ORDER BY created_at`, caseID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var b string
			if err := rows.Scan(&b); err != nil {
				return err
			}
			text += b + "\n"
		}
		return rows.Err()
	})
	return text, err
}

// AllCaseIDs returns every live case id for a tenant (full reindex, CASE-FR-043).
func (s *PG) AllCaseIDs(ctx context.Context, tenant uuid.UUID) ([]uuid.UUID, error) {
	var out []uuid.UUID
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT id FROM cases WHERE deleted_at IS NULL ORDER BY created_at`)
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

// CasesPage returns up to limit live cases ordered by (created_at, id),
// starting strictly after the given keyset cursor -- the batched-read half of
// the B5 reindex fix (scalability audit). Backed by cases_tenant_created_idx
// (tenant_id, created_at, id) so a multi-million-case tenant is paged with a
// single indexed query per page instead of one GetCase round trip per case.
// The zero-value cursor (time.Time{}, uuid.Nil) starts from the beginning.
func (s *PG) CasesPage(ctx context.Context, tenant uuid.UUID, afterCreatedAt time.Time, afterID uuid.UUID, limit int) ([]*domain.Case, error) {
	var out []*domain.Case
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT `+caseCols+` FROM cases
			WHERE deleted_at IS NULL AND (created_at, id) > ($1, $2)
			ORDER BY created_at, id
			LIMIT $3`, afterCreatedAt, afterID, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			c, err := scanCase(rows)
			if err != nil {
				return err
			}
			out = append(out, c)
		}
		return rows.Err()
	})
	return out, err
}

// CaseCommentTextBatch returns each case's concatenated comment body (the
// same text CaseCommentText produces for one case) for a whole batch of ids
// in a single round trip -- the other half of the B5 reindex fix: the old
// path issued one comment query per case on top of one case read per case.
// A case with no comments is simply absent from the returned map.
func (s *PG) CaseCommentTextBatch(ctx context.Context, tenant uuid.UUID, caseIDs []uuid.UUID) (map[uuid.UUID]string, error) {
	out := map[uuid.UUID]string{}
	if len(caseIDs) == 0 {
		return out, nil
	}
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT case_id, string_agg(body || E'\n', '' ORDER BY created_at)
			FROM case_comments
			WHERE case_id = ANY($1) AND deleted_at IS NULL
			GROUP BY case_id`, caseIDs)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id uuid.UUID
			var text string
			if err := rows.Scan(&id, &text); err != nil {
				return err
			}
			out[id] = text
		}
		return rows.Err()
	})
	return out, err
}

// PolicyForCase resolves the SLA policy governing a case (its workspace's
// policy, or the default). Used by the sweep worker at fire time.
func (s *PG) PolicyForCase(ctx context.Context, tenant, caseID uuid.UUID) (domain.SLAPolicy, error) {
	var ws uuid.UUID
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT workspace_id FROM cases WHERE id=$1`, caseID).Scan(&ws)
	})
	if err != nil {
		return domain.SLAPolicy{}, err
	}
	return s.GetSLAPolicy(ctx, tenant, ws)
}

func strOrNil(u *uuid.UUID) any {
	if u == nil {
		return nil
	}
	return u.String()
}

func strOrNilPtr(u *uuid.UUID) any {
	if u == nil {
		return nil
	}
	return u.String()
}
