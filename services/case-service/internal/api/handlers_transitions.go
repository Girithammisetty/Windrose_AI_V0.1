package api

import (
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
	"github.com/windrose-ai/case-service/internal/store"
)

type assignReq struct {
	AssigneeID string `json:"assignee_id"`
}

func (s *Server) handleAssign(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	var req assignReq
	if !decodeBody(w, r, &req) {
		return
	}
	assignee, err := uuid.Parse(req.AssigneeID)
	if err != nil {
		writeErr(w, r, domain.EValidation("assignee_id must be a uuid", nil))
		return
	}
	now := time.Now().UTC()
	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		if err := c.Assign(assignee, now); err != nil {
			return store.Mutation{}, err
		}
		urn := events.CaseURN(op.Tenant, c.ID)
		policy, _ := s.Store.GetSLAPolicy(r.Context(), op.Tenant, c.WorkspaceID)
		timers := store.TimerPlan{Set: []store.Timer{
			{Kind: "warn", FireAt: c.DueDate.Add(-policy.WarnBefore)},
			{Kind: "due", FireAt: c.DueDate},
		}}
		return store.Mutation{
			Activities: []domain.Activity{mkActivity(op, events.EvAssigned, nil, map[string]any{"assignee": assignee.String(), "status": c.Status.String()})},
			Events:     []events.Envelope{events.NewEnvelope(events.EvAssigned, op, urn, map[string]any{"case_number": c.CaseNumber, "assignee": assignee.String(), "due_date": c.DueDate})},
			Timers:     timers,
		}, nil
	})
	s.respondCase(w, r, c, err)
}

func (s *Server) handleUnassign(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		if err := c.Unassign(); err != nil {
			return store.Mutation{}, err
		}
		urn := events.CaseURN(op.Tenant, c.ID)
		return store.Mutation{
			Activities: []domain.Activity{mkActivity(op, events.EvUnassigned, nil, map[string]any{"status": c.Status.String(), "reason": domain.ReasonManual})},
			Events:     []events.Envelope{events.NewEnvelope(events.EvUnassigned, op, urn, map[string]any{"case_number": c.CaseNumber, "reason": domain.ReasonManual})},
			Timers:     store.TimerPlan{Cancel: true},
		}, nil
	})
	s.respondCase(w, r, c, err)
}

func (s *Server) handleStart(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		if err := c.Start(); err != nil {
			return store.Mutation{}, err
		}
		urn := events.CaseURN(op.Tenant, c.ID)
		return store.Mutation{
			Activities: []domain.Activity{mkActivity(op, events.EvStarted, nil, map[string]any{"status": c.Status.String()})},
			Events:     []events.Envelope{events.NewEnvelope(events.EvStarted, op, urn, map[string]any{"case_number": c.CaseNumber})},
		}, nil
	})
	s.respondCase(w, r, c, err)
}

type resolveReq struct {
	DispositionID  string `json:"disposition_id"`
	ResolutionNote string `json:"resolution_note"`
}

func (s *Server) handleResolve(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	var req resolveReq
	if !decodeBody(w, r, &req) {
		return
	}
	dispID, err := uuid.Parse(req.DispositionID)
	if err != nil {
		writeErr(w, r, domain.EDispositionRequired())
		return
	}
	disp, err := s.Store.GetDisposition(r.Context(), op.Tenant, dispID)
	if err != nil {
		writeErr(w, r, domain.EDispositionRequired())
		return
	}
	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		return s.resolveMutation(op, c, disp, req.ResolutionNote, "")
	})
	s.respondCase(w, r, c, err)
}

// resolveMutation applies a resolution and emits the learning-loop signal
// (case.disposition_applied). proposalURN is set when the resolution came from
// an approved copilot proposal (adds case.correction_recorded).
func (s *Server) resolveMutation(op domain.Op, c *domain.Case, disp *domain.Disposition, note, proposalURN string) (store.Mutation, error) {
	now := time.Now().UTC()
	if err := c.Resolve(disp, note, now); err != nil {
		return store.Mutation{}, err
	}
	urn := events.CaseURN(op.Tenant, c.ID)
	acts := []domain.Activity{mkActivity(op, events.EvResolved, nil, map[string]any{
		"status": c.Status.String(), "disposition_id": disp.ID.String(), "category": disp.Category})}
	// Learning-loop hook: the human triage correction on THIS dataset row is now
	// a labeled training signal the learning loop consumes (BRD §1, CASE-FR-051).
	correction := map[string]any{
		"case_number": c.CaseNumber, "dataset_urn": c.DatasetURN, "dataset_version": c.DatasetVersion, "row_pk": c.RowPK,
		"disposition": map[string]any{"id": disp.ID.String(), "code": disp.Code, "category": disp.Category},
		"resolution_note": note, "severity": c.Severity,
	}
	// memory-service grounds copilot RAG on this payload (resolved_cases corpus):
	// resolution_note is the embedded narrative, authored_by drives right-to-erasure
	// user linkage (MEM-FR-040).
	evs := []events.Envelope{
		events.NewEnvelope(events.EvResolved, op, urn, map[string]any{
			"case_number": c.CaseNumber, "disposition_code": disp.Code, "disposition_category": disp.Category,
			"resolution_note": note, "authored_by": op.Actor.ID,
		}),
		events.NewEnvelope(events.EvDispositionApplied, op, urn, correction),
	}
	if proposalURN != "" {
		crAct := mkActivity(op, events.EvCorrectionRecorded, nil, correction)
		crAct.ProposalURN = proposalURN
		acts = append(acts, crAct)
		crEnv := events.NewEnvelope(events.EvCorrectionRecorded, op, urn, correction)
		crEnv.Payload["proposal_urn"] = proposalURN
		evs = append(evs, crEnv)
	}
	return store.Mutation{Activities: acts, Events: evs, Timers: store.TimerPlan{Cancel: true}}, nil
}

func (s *Server) handleReopen(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	now := time.Now().UTC()
	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		prior := c.PriorDisposition()
		if err := c.Reopen(now); err != nil {
			return store.Mutation{}, err
		}
		urn := events.CaseURN(op.Tenant, c.ID)
		newV := map[string]any{"status": c.Status.String()}
		if prior != nil {
			newV["reopened_from"] = prior.String()
		}
		return store.Mutation{
			Activities: []domain.Activity{mkActivity(op, events.EvReopened, nil, newV)},
			Events:     []events.Envelope{events.NewEnvelope(events.EvReopened, op, urn, map[string]any{"case_number": c.CaseNumber})},
		}, nil
	})
	s.respondCase(w, r, c, err)
}

func (s *Server) handleClose(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	// Fetch the full row once and archive it in object storage (CASE-FR-006,
	// AC-8) — the only time full row data is persisted. Fall back to the display
	// projection so snapshot_ref is always set.
	cur, err := s.Store.GetCase(r.Context(), op.Tenant, id)
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	row, ferr := s.fetchRow(r, cur)
	if ferr != nil || row == nil {
		row = map[string]any{}
		for k, v := range cur.DisplayProjection {
			row[k] = v
		}
		row["_projection_only"] = true
	}
	ref, err := s.Snapshots.Put(r.Context(), op.Tenant, id, row)
	if err != nil {
		writeErr(w, r, domain.EInternal("snapshot write failed"))
		return
	}
	now := time.Now().UTC()
	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		if err := c.Close(ref, now); err != nil {
			return store.Mutation{}, err
		}
		urn := events.CaseURN(op.Tenant, c.ID)
		return store.Mutation{
			Activities: []domain.Activity{mkActivity(op, events.EvClosed, nil, map[string]any{"status": c.Status.String(), "snapshot_ref": ref})},
			Events:     []events.Envelope{events.NewEnvelope(events.EvClosed, op, urn, map[string]any{"case_number": c.CaseNumber, "snapshot_ref": ref})},
			Timers:     store.TimerPlan{Cancel: true},
		}, nil
	})
	s.respondCase(w, r, c, err)
}

type escalateReq struct {
	To     string `json:"to"`
	Reason string `json:"reason"`
}

func (s *Server) handleEscalate(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	var req escalateReq
	if !decodeBody(w, r, &req) {
		return
	}
	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		old := c.Severity
		c.Severity = domain.BumpSeverity(c.Severity)
		urn := events.CaseURN(op.Tenant, c.ID)
		payload := map[string]any{"case_number": c.CaseNumber, "severity": c.Severity, "to": req.To, "reason": req.Reason}
		return store.Mutation{
			Activities: []domain.Activity{mkActivity(op, events.EvEscalated, map[string]any{"severity": old}, payload)},
			Events:     []events.Envelope{events.NewEnvelope(events.EvEscalated, op, urn, payload)},
		}, nil
	})
	s.respondCase(w, r, c, err)
}

// ---- helpers ----------------------------------------------------------------

func (s *Server) opCase(w http.ResponseWriter, r *http.Request) (domain.Op, uuid.UUID, bool) {
	op, ok := opFrom(r)
	if !ok {
		writeErr(w, r, domain.EUnauthenticated("bad claims"))
		return domain.Op{}, uuid.Nil, false
	}
	_, id, ok := s.pathCase(w, r)
	if !ok {
		return domain.Op{}, uuid.Nil, false
	}
	return op, id, true
}

func (s *Server) respondCase(w http.ResponseWriter, r *http.Request, c *domain.Case, err error) {
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	writeData(w, http.StatusOK, caseView(c))
}
