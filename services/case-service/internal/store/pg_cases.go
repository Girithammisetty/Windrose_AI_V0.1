package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/datacern-ai/case-service/internal/domain"
	"github.com/datacern-ai/case-service/internal/events"
)

const caseCols = `id, tenant_id, workspace_id, case_number, status, severity, assigned_to_id, assigned_to_at,
	created_by_id, dataset_urn, dataset_version, row_pk, dedup_key, display_projection, projection_truncated,
	source_query_urns, dashboard_urn, due_date, description, custom_fields, disposition_id, resolution_note,
	resolved_at, closed_at, snapshot_ref, recurrence_of, reassign_count, row_unavailable, case_version,
	created_at, updated_at, deleted_at`

func scanCase(row pgx.Row) (*domain.Case, error) {
	var c domain.Case
	var proj, custom []byte
	err := row.Scan(&c.ID, &c.TenantID, &c.WorkspaceID, &c.CaseNumber, &c.Status, &c.Severity, &c.AssignedToID, &c.AssignedToAt,
		&c.CreatedByID, &c.DatasetURN, &c.DatasetVersion, &c.RowPK, &c.DedupKey, &proj, &c.ProjectionTruncated,
		&c.SourceQueryURNs, &c.DashboardURN, &c.DueDate, &c.Description, &custom, &c.DispositionID, &c.ResolutionNote,
		&c.ResolvedAt, &c.ClosedAt, &c.SnapshotRef, &c.RecurrenceOf, &c.ReassignCount, &c.RowUnavailable, &c.CaseVersion,
		&c.CreatedAt, &c.UpdatedAt, &c.DeletedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal(proj, &c.DisplayProjection)
	_ = json.Unmarshal(custom, &c.CustomFields)
	if c.DisplayProjection == nil {
		c.DisplayProjection = map[string]string{}
	}
	if c.CustomFields == nil {
		c.CustomFields = map[string]any{}
	}
	if c.SourceQueryURNs == nil {
		c.SourceQueryURNs = []string{}
	}
	return &c, nil
}

func insertCaseTx(ctx context.Context, tx pgx.Tx, c *domain.Case) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO cases (`+caseCols+`)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32)`,
		c.ID, c.TenantID, c.WorkspaceID, c.CaseNumber, c.Status, c.Severity, c.AssignedToID, c.AssignedToAt,
		c.CreatedByID, c.DatasetURN, c.DatasetVersion, c.RowPK, c.DedupKey, mustJSON(c.DisplayProjection), c.ProjectionTruncated,
		c.SourceQueryURNs, c.DashboardURN, c.DueDate, c.Description, mustJSON(c.CustomFields), c.DispositionID, c.ResolutionNote,
		c.ResolvedAt, c.ClosedAt, c.SnapshotRef, c.RecurrenceOf, c.ReassignCount, c.RowUnavailable, c.CaseVersion,
		c.CreatedAt, c.UpdatedAt, c.DeletedAt)
	return err
}

// nextCaseNumber allocates the per-workspace monotonic number under a row lock
// (CASE-FR-004, BR-3).
func nextCaseNumber(ctx context.Context, tx pgx.Tx, tenant, ws uuid.UUID) (int64, error) {
	var n int64
	err := tx.QueryRow(ctx, `
		INSERT INTO case_sequences (tenant_id, workspace_id, last_number) VALUES ($1,$2,1)
		ON CONFLICT (tenant_id, workspace_id) DO UPDATE SET last_number = case_sequences.last_number + 1
		RETURNING last_number`, tenant, ws).Scan(&n)
	return n, err
}

// CreateCases inserts cases from query rows with dedup + recurrence semantics
// (CASE-FR-002/005, BR-2). Rows whose open dedup_key already exists merge the
// query_urn into the existing case (deduped). Rows whose only match is a closed
// case link recurrence_of. Assigned cases get SLA timers set (CASE-FR-012).
func (s *PG) CreateCases(ctx context.Context, op domain.Op, cases []*domain.Case, queryURN string, warnBefore time.Duration) ([]*domain.Case, []DedupResult, error) {
	var created []*domain.Case
	var deduped []DedupResult
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		if len(cases) == 0 {
			return nil
		}
		ws := cases[0].WorkspaceID
		// Hard open-case limit (BR-13).
		var open int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM cases WHERE workspace_id=$1 AND status<>4 AND deleted_at IS NULL`, ws).Scan(&open); err != nil {
			return err
		}
		if open+len(cases) > 10000 {
			return ErrCaseLimit
		}
		for _, c := range cases {
			if c.DedupKey != nil {
				// Serialize per dedup key to make the check-then-insert atomic (BR-2).
				if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1, 99))`, *c.DedupKey); err != nil {
					return err
				}
				existing, err := scanCase(tx.QueryRow(ctx,
					`SELECT `+caseCols+` FROM cases WHERE dedup_key=$1 AND status<>4 AND deleted_at IS NULL LIMIT 1`, *c.DedupKey))
				if err != nil && !errors.Is(err, ErrNotFound) {
					return err
				}
				if existing != nil {
					if queryURN != "" && !contains(existing.SourceQueryURNs, queryURN) {
						existing.SourceQueryURNs = append(existing.SourceQueryURNs, queryURN)
						if _, err := tx.Exec(ctx, `UPDATE cases SET source_query_urns=$2, updated_at=now() WHERE id=$1`,
							existing.ID, existing.SourceQueryURNs); err != nil {
							return err
						}
					}
					deduped = append(deduped, DedupResult{Case: existing, RowPK: c.RowPK})
					continue
				}
				// Closed case with same key → recurrence (BR-2).
				var priorID uuid.UUID
				err = tx.QueryRow(ctx,
					`SELECT id FROM cases WHERE dedup_key=$1 AND status=4 AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1`, *c.DedupKey).Scan(&priorID)
				if err == nil {
					c.RecurrenceOf = &priorID
				} else if !errors.Is(err, pgx.ErrNoRows) {
					return err
				}
			}
			n, err := nextCaseNumber(ctx, tx, op.Tenant, ws)
			if err != nil {
				return err
			}
			c.CaseNumber = n
			if err := insertCaseTx(ctx, tx, c); err != nil {
				if isUniqueViolation(err) && constraintName(err) == "cases_dedup_uniq" {
					return ErrDedupConflict
				}
				return err
			}
			// Timeline + emitted created event.
			urn := events.CaseURN(op.Tenant, c.ID)
			act := domain.Activity{ID: domain.NewID(), CaseID: c.ID, EventType: events.EvCreated,
				ActorType: op.Actor.Type, ActorID: op.Actor.ID, ViaAgent: op.ViaAgent,
				NewValue: map[string]any{"status": c.Status.String(), "case_number": c.CaseNumber}, OccurredAt: time.Now().UTC()}
			if err := insertActivitiesTx(ctx, tx, op.Tenant, []domain.Activity{act}); err != nil {
				return err
			}
			env := events.NewEnvelope(events.EvCreated, op, urn, map[string]any{
				"case_number": c.CaseNumber, "status": c.Status.String(), "severity": c.Severity,
				"dataset_urn": c.DatasetURN, "row_pk": c.RowPK, "dedup_key": derefStr(c.DedupKey),
				// workspace_id activates rbac's implicit creator grant on
				// *.created (RBC-FR-032) — without it the consumer no-ops.
				"workspace_id": c.WorkspaceID.String(),
			})
			if err := insertOutboxTx(ctx, tx, []events.Envelope{env}); err != nil {
				return err
			}
			// SLA timers for an assigned new case (CASE-FR-012).
			if c.AssignedToID != nil {
				plan := TimerPlan{Set: []Timer{
					{Kind: "warn", FireAt: c.DueDate.Add(-warnBefore)},
					{Kind: "due", FireAt: c.DueDate},
				}}
				if err := applyTimerPlanTx(ctx, tx, op.Tenant, c.ID, c.CaseVersion, plan); err != nil {
					return err
				}
				aenv := events.NewEnvelope(events.EvAssigned, op, urn, map[string]any{
					"case_number": c.CaseNumber, "assignee": c.AssignedToID.String(), "due_date": c.DueDate,
				})
				if err := insertOutboxTx(ctx, tx, []events.Envelope{aenv}); err != nil {
					return err
				}
			}
			created = append(created, c)
		}
		return nil
	})
	return created, deduped, err
}

// GetCase reads a single case from Postgres — the source of truth
// (CASE-FR-041). Cross-tenant reads are invisible under RLS (AC-13 → 404).
func (s *PG) GetCase(ctx context.Context, tenant, id uuid.UUID) (*domain.Case, error) {
	var c *domain.Case
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var err error
		c, err = scanCase(tx.QueryRow(ctx, `SELECT `+caseCols+` FROM cases WHERE id=$1 AND deleted_at IS NULL`, id))
		return err
	})
	return c, err
}

// MutateCase loads the case FOR UPDATE, verifies the optimistic version (when
// expectVersion is non-nil), applies the transition, bumps case_version, and
// commits the timeline + outbox + SLA timer plan atomically (§4). Returns the
// updated case.
func (s *PG) MutateCase(ctx context.Context, op domain.Op, id uuid.UUID, expectVersion *int, apply func(c *domain.Case) (Mutation, error)) (*domain.Case, error) {
	var out *domain.Case
	err := s.withTenant(ctx, op.Tenant, func(tx pgx.Tx) error {
		c, err := scanCase(tx.QueryRow(ctx, `SELECT `+caseCols+` FROM cases WHERE id=$1 AND deleted_at IS NULL FOR UPDATE`, id))
		if err != nil {
			return err
		}
		if expectVersion != nil && c.CaseVersion != *expectVersion {
			return ErrStaleVersion
		}
		mut, err := apply(c)
		if err != nil {
			return err
		}
		c.CaseVersion++
		c.UpdatedAt = time.Now().UTC()
		if _, err := tx.Exec(ctx, `
			UPDATE cases SET status=$2, severity=$3, assigned_to_id=$4, assigned_to_at=$5, due_date=$6,
				description=$7, custom_fields=$8, disposition_id=$9, resolution_note=$10, resolved_at=$11,
				closed_at=$12, snapshot_ref=$13, recurrence_of=$14, reassign_count=$15, row_unavailable=$16,
				source_query_urns=$17, case_version=$18, updated_at=$19
			WHERE id=$1`,
			c.ID, c.Status, c.Severity, c.AssignedToID, c.AssignedToAt, c.DueDate, c.Description, mustJSON(c.CustomFields),
			c.DispositionID, c.ResolutionNote, c.ResolvedAt, c.ClosedAt, c.SnapshotRef, c.RecurrenceOf, c.ReassignCount,
			c.RowUnavailable, c.SourceQueryURNs, c.CaseVersion, c.UpdatedAt); err != nil {
			if constraintName(err) == "cases_assignee_status_invariant" {
				return domain.EInternal("assignee/status invariant violated")
			}
			return err
		}
		// Stamp the post-mutation version onto any timers to set.
		if err := applyTimerPlanTx(ctx, tx, op.Tenant, c.ID, c.CaseVersion, mut.Timers); err != nil {
			return err
		}
		for i := range mut.Activities {
			mut.Activities[i].CaseID = c.ID
		}
		if err := insertActivitiesTx(ctx, tx, op.Tenant, mut.Activities); err != nil {
			return err
		}
		if err := insertOutboxTx(ctx, tx, mut.Events); err != nil {
			return err
		}
		out = c
		return nil
	})
	return out, err
}

// ExportCases returns cases for a workspace (optionally filtered by status),
// read from Postgres so exported values are authoritative, not projection-lagged
// (CASE-FR-044). Capped to protect the exporter.
func (s *PG) ExportCases(ctx context.Context, tenant, ws uuid.UUID, statuses []domain.Status, capN int) ([]*domain.Case, error) {
	if capN <= 0 || capN > 50000 {
		capN = 50000
	}
	var out []*domain.Case
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		q := `SELECT ` + caseCols + ` FROM cases WHERE workspace_id=$1 AND deleted_at IS NULL`
		args := []any{ws}
		if len(statuses) > 0 {
			ints := make([]int16, len(statuses))
			for i, st := range statuses {
				ints[i] = int16(st)
			}
			args = append(args, ints)
			q += ` AND status = ANY($2)`
		}
		args = append(args, capN)
		q += ` ORDER BY case_number LIMIT $` + strconv.Itoa(len(args))
		rows, err := tx.Query(ctx, q, args...)
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

// UnassignUserCases unassigns every open case assigned to a user (consumed on
// user.deactivated / workspace.member.removed, §6). Each unassign is its own
// transaction and emits case.unassigned{reason:user_deactivated}.
func (s *PG) UnassignUserCases(ctx context.Context, tenant, userID uuid.UUID) error {
	var ids []uuid.UUID
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT id FROM cases WHERE assigned_to_id=$1 AND status IN (0,1) AND deleted_at IS NULL`, userID)
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
	if err != nil {
		return err
	}
	// Identity-driven unassign is the service acting autonomously; the master
	// envelope actor.type must be one of {user,service,agent,platform}, so emit
	// actor={service,case-service} (the timeline keeps the "identity" sub-actor).
	op := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "service", ID: "case-service"}}
	for _, id := range ids {
		_, err := s.MutateCase(ctx, op, id, nil, func(c *domain.Case) (Mutation, error) {
			if err := c.Unassign(); err != nil {
				return Mutation{}, err
			}
			urn := events.CaseURN(tenant, c.ID)
			return Mutation{
				Activities: []domain.Activity{{ID: domain.NewID(), CaseID: c.ID, EventType: events.EvUnassigned, ActorType: "system", ActorID: "identity", NewValue: map[string]any{"reason": domain.ReasonDeactivate}, OccurredAt: time.Now().UTC()}},
				Events:     []events.Envelope{events.NewEnvelope(events.EvUnassigned, op, urn, map[string]any{"case_number": c.CaseNumber, "reason": domain.ReasonDeactivate})},
				Timers:     TimerPlan{Cancel: true},
			}, nil
		})
		if err != nil {
			return err
		}
	}
	return nil
}

func contains(ss []string, v string) bool {
	for _, s := range ss {
		if s == v {
			return true
		}
	}
	return false
}

func derefStr(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

var _ = fmt.Sprintf
