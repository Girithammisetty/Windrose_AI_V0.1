package api

import (
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/events"
	"github.com/windrose-ai/case-service/internal/store"
)

type applyProposalReq struct {
	ProposalURN string         `json:"proposal_urn"`
	Changes     map[string]any `json:"changes"`
}

// allowedProposalFields are exactly the fields the copilot may propose
// (CASE-FR-052, AC-11).
var allowedProposalFields = map[string]bool{"severity": true, "assigned_to_id": true, "disposition": true}

// handleApplyProposal applies an approved copilot proposal (CASE-FR-051, AC-10).
// Dual attribution comes from the agent_obo token: actor=user(approver) +
// via_agent(agent). Idempotent by proposal_urn (BR-9).
func (s *Server) handleApplyProposal(w http.ResponseWriter, r *http.Request) {
	op, id, ok := s.opCase(w, r)
	if !ok {
		return
	}
	var req applyProposalReq
	if !decodeBody(w, r, &req) {
		return
	}
	if req.ProposalURN == "" {
		writeErr(w, r, domain.EValidation("proposal_urn is required", nil))
		return
	}
	// Only the whitelisted fields may appear (AC-11).
	for k := range req.Changes {
		if !allowedProposalFields[k] {
			writeErr(w, r, domain.EProposalFieldDenied(k))
			return
		}
	}
	// Idempotent replay (BR-9).
	if prev, ok, err := s.Store.GetAppliedProposal(r.Context(), op.Tenant, req.ProposalURN); err == nil && ok {
		w.Header().Set("Idempotency-Replayed", "true")
		writeData(w, http.StatusOK, prev)
		return
	}

	// Pre-resolve the disposition (outside the mutation) if present.
	var disp *domain.Disposition
	var note string
	if dc, present := req.Changes["disposition"]; present {
		m, _ := dc.(map[string]any)
		dispIDRaw, _ := m["id"].(string)
		note, _ = m["resolution_note"].(string)
		dispID, err := uuid.Parse(dispIDRaw)
		if err != nil {
			writeErr(w, r, domain.EDispositionRequired())
			return
		}
		disp, err = s.Store.GetDisposition(r.Context(), op.Tenant, dispID)
		if err != nil {
			writeErr(w, r, domain.EDispositionRequired())
			return
		}
	}
	var newAssignee *uuid.UUID
	if av, present := req.Changes["assigned_to_id"]; present {
		raw, _ := av.(string)
		a, err := uuid.Parse(raw)
		if err != nil {
			writeErr(w, r, domain.EValidation("assigned_to_id must be a uuid", nil))
			return
		}
		newAssignee = &a
	}
	var newSeverity string
	if sv, present := req.Changes["severity"]; present {
		newSeverity, _ = sv.(string)
		if !domain.ValidSeverity(newSeverity) {
			writeErr(w, r, domain.EValidation("invalid severity", nil))
			return
		}
	}

	c, err := s.Store.MutateCase(r.Context(), op, id, ifMatchVersion(r), func(c *domain.Case) (store.Mutation, error) {
		now := time.Now().UTC()
		urn := events.CaseURN(op.Tenant, c.ID)
		var acts []domain.Activity
		var evs []events.Envelope
		timers := store.TimerPlan{}

		if newSeverity != "" && newSeverity != c.Severity {
			old := c.Severity
			c.Severity = newSeverity
			a := mkActivity(op, events.EvSeverityChanged, map[string]any{"severity": old}, map[string]any{"severity": newSeverity})
			a.ProposalURN = req.ProposalURN
			acts = append(acts, a)
			evs = append(evs, events.NewEnvelope(events.EvSeverityChanged, op, urn, map[string]any{"case_number": c.CaseNumber, "severity": newSeverity}))
		}
		if newAssignee != nil {
			if err := c.Assign(*newAssignee, now); err != nil {
				return store.Mutation{}, err
			}
			a := mkActivity(op, events.EvAssigned, nil, map[string]any{"assignee": newAssignee.String()})
			a.ProposalURN = req.ProposalURN
			acts = append(acts, a)
			evs = append(evs, events.NewEnvelope(events.EvAssigned, op, urn, map[string]any{"case_number": c.CaseNumber, "assignee": newAssignee.String()}))
			policy, _ := s.Store.GetSLAPolicy(r.Context(), op.Tenant, c.WorkspaceID)
			timers = store.TimerPlan{Set: []store.Timer{{Kind: "warn", FireAt: c.DueDate.Add(-policy.WarnBefore)}, {Kind: "due", FireAt: c.DueDate}}}
		}
		if disp != nil {
			m, err := s.resolveMutation(op, c, disp, note, req.ProposalURN)
			if err != nil {
				return store.Mutation{}, err
			}
			// Link the resolved activity to the proposal too.
			for i := range m.Activities {
				if m.Activities[i].ProposalURN == "" {
					m.Activities[i].ProposalURN = req.ProposalURN
				}
			}
			acts = append(acts, m.Activities...)
			evs = append(evs, m.Events...)
			timers = m.Timers
		}
		return store.Mutation{Activities: acts, Events: evs, Timers: timers}, nil
	})
	if err != nil {
		s.writeLookupErr(w, r, err)
		return
	}
	view := caseView(c)
	if err := s.Store.PutAppliedProposal(r.Context(), op.Tenant, req.ProposalURN, c.ID, view); err != nil {
		// Best-effort idempotency record; the mutation already committed.
	}
	writeData(w, http.StatusOK, view)
}
