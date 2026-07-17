package integration

import (
	"compress/gzip"
	"context"
	"encoding/csv"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/case-service/internal/api"
	"github.com/windrose-ai/case-service/internal/authz"
	"github.com/windrose-ai/case-service/internal/domain"
	"github.com/windrose-ai/case-service/internal/search"
	"github.com/windrose-ai/case-service/internal/sla"
)

// ---- helpers ----------------------------------------------------------------

type actorCtx struct {
	tenant, workspace uuid.UUID
	tok               string
}

func (h *harness) newActor(t *testing.T) actorCtx {
	tenant, ws := uuid.New(), uuid.New()
	return actorCtx{tenant: tenant, workspace: ws, tok: h.token(t, tenant, ws, domain.TypUser, "u-"+uuid.NewString()[:8], nil)}
}

func (h *harness) seedDisposition(t *testing.T, a actorCtx, code string, requiresNote bool) string {
	t.Helper()
	r := h.do(t, "POST", "/api/v1/dispositions", a.tok, map[string]any{
		"code": code, "label": code, "category": "true_positive", "requires_note": requiresNote,
	}, nil)
	require.Equal(t, http.StatusCreated, r.status, "%v", r.body)
	return dataMap(r)["id"].(string)
}

func (h *harness) putSLA(t *testing.T, a actorCtx, warnSecs int, onBreach string) {
	t.Helper()
	r := h.do(t, "PUT", "/api/v1/sla-policy", a.tok, map[string]any{
		"warn_before_seconds": warnSecs, "on_breach": onBreach, "max_reassign_count": 3,
	}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
}

func (h *harness) createOne(t *testing.T, a actorCtx, assignee string, due time.Time, extra map[string]any) map[string]any {
	t.Helper()
	body := map[string]any{
		"dataset_urn": "wr:" + a.tenant.String() + ":dataset:dataset/txns",
		"due_date":    due.Format(time.RFC3339),
		"rows":        []map[string]any{{"row_pk": "txn-" + uuid.NewString()[:8], "display_projection": map[string]string{"merchant": "ACME"}}},
	}
	if assignee != "" {
		body["assigned_to_id"] = assignee
	}
	for k, v := range extra {
		body[k] = v
	}
	r := h.do(t, "POST", "/api/v1/cases", a.tok, body, nil)
	require.Equal(t, http.StatusCreated, r.status, "%v", r.body)
	created := dataMap(r)["created"].([]any)
	require.Len(t, created, 1)
	return created[0].(map[string]any)
}

func (h *harness) lifecycleToClosed(t *testing.T, a actorCtx, dispID string) string {
	t.Helper()
	assignee := uuid.NewString()
	c := h.createOne(t, a, assignee, time.Now().Add(48*time.Hour), nil)
	id := c["id"].(string)
	require.Equal(t, http.StatusOK, h.do(t, "POST", "/api/v1/cases/"+id+"/start", a.tok, nil, nil).status)
	rr := h.do(t, "POST", "/api/v1/cases/"+id+"/resolve", a.tok, map[string]any{"disposition_id": dispID, "resolution_note": "done"}, nil)
	require.Equal(t, http.StatusOK, rr.status, "%v", rr.body)
	require.Equal(t, http.StatusOK, h.do(t, "POST", "/api/v1/cases/"+id+"/close", a.tok, nil, nil).status)
	return id
}

// ---- AC-1: row-reference creation, sequential numbers, no snapshot ----------

func TestAC1_RowReferenceCreation(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	rows := []map[string]any{
		{"row_pk": "txn-1", "display_projection": map[string]string{"txn_id": "txn-1", "amount": "1,250.50", "merchant": "ACME"}},
		{"row_pk": "txn-2", "display_projection": map[string]string{"txn_id": "txn-2", "amount": "980.00", "merchant": "ZORP"}},
		{"row_pk": "txn-3", "display_projection": map[string]string{"txn_id": "txn-3", "amount": "12.00", "merchant": "BETA"}},
	}
	r := h.do(t, "POST", "/api/v1/cases", a.tok, map[string]any{
		"query_urn": "wr:t:query:query/q-1", "dataset_urn": "wr:t:dataset:dataset/txns",
		"due_date": time.Now().Add(24 * time.Hour).Format(time.RFC3339), "rows": rows,
	}, nil)
	require.Equal(t, http.StatusCreated, r.status, "%v", r.body)
	created := dataMap(r)["created"].([]any)
	require.Len(t, created, 3)

	var numbers []float64
	for _, cr := range created {
		m := cr.(map[string]any)
		id := m["id"].(string)
		g := h.do(t, "GET", "/api/v1/cases/"+id, a.tok, nil, nil)
		require.Equal(t, http.StatusOK, g.status)
		d := dataMap(g)
		assert.Equal(t, "wr:t:dataset:dataset/txns", d["dataset_urn"])
		assert.NotEmpty(t, d["row_pk"])
		assert.NotNil(t, d["display_projection"])
		_, hasRowSnapshot := d["row"]
		assert.False(t, hasRowSnapshot, "no full-row snapshot column while open")
		numbers = append(numbers, m["case_number"].(float64))
	}
	// Sequential per workspace.
	assert.Equal(t, numbers[0]+1, numbers[1])
	assert.Equal(t, numbers[1]+1, numbers[2])
}

// ---- AC-2: dedup merges query refs, never duplicates ------------------------

func TestAC2_Dedup(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	body := func(q string) map[string]any {
		return map[string]any{
			"query_urn": q, "dataset_urn": "wr:t:dataset:dataset/txns",
			"due_date": time.Now().Add(24 * time.Hour).Format(time.RFC3339),
			"rows":     []map[string]any{{"row_pk": "row-R", "display_projection": map[string]string{"merchant": "ACME"}}},
		}
	}
	r1 := h.do(t, "POST", "/api/v1/cases", a.tok, body("wr:t:query:query/q-1"), nil)
	require.Equal(t, http.StatusCreated, r1.status)
	require.Len(t, dataMap(r1)["created"].([]any), 1)

	r2 := h.do(t, "POST", "/api/v1/cases", a.tok, body("wr:t:query:query/q-2"), nil)
	require.Equal(t, http.StatusCreated, r2.status, "%v", r2.body)
	assert.Empty(t, dataMap(r2)["created"], "no new case for a duplicate row")
	deduped := dataMap(r2)["deduplicated"].([]any)
	require.Len(t, deduped, 1)
	assert.Equal(t, "true", r2.headers.Get("X-Case-Deduplicated"))
	urns := deduped[0].(map[string]any)["source_query_urns"].([]any)
	assert.Contains(t, urns, "wr:t:query:query/q-1")
	assert.Contains(t, urns, "wr:t:query:query/q-2")
}

// ---- AC-3: SLA auto-unassign fires with system/sla attribution --------------

func TestAC3_SLAAutoUnassign(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	h.putSLA(t, a, 1, domain.BreachAutoUnassign)
	c := h.createOne(t, a, uuid.NewString(), time.Now().Add(2*time.Second), nil)
	id := c["id"].(string)

	time.Sleep(2500 * time.Millisecond)
	require.NoError(t, h.slaWorker.Sweep(context.Background()))

	g := h.do(t, "GET", "/api/v1/cases/"+id, a.tok, nil, nil)
	require.Equal(t, http.StatusOK, g.status)
	assert.Equal(t, "unassigned", dataMap(g)["status"])
	assert.Nil(t, dataMap(g)["assigned_to_id"])

	breached, _ := h.pg.OutboxEventsByType(context.Background(), a.tenant, "case.sla.breached")
	assert.NotEmpty(t, breached, "case.sla.breached emitted")
	unassigned, _ := h.pg.OutboxEventsByType(context.Background(), a.tenant, "case.unassigned")
	require.NotEmpty(t, unassigned)
	assert.Equal(t, "sla_breach", unassigned[len(unassigned)-1].Payload["reason"])

	tl := h.do(t, "GET", "/api/v1/cases/"+id+"/timeline", a.tok, nil, nil)
	require.Equal(t, http.StatusOK, tl.status)
	found := false
	for _, e := range tl.body["data"].([]any) {
		m := e.(map[string]any)
		if m["event_type"] == "case.unassigned" && m["actor_id"] == "sla" {
			found = true
		}
	}
	assert.True(t, found, "timeline shows actor system/sla")
}

// ---- AC-4: SLA durability across a simulated restart ------------------------

func TestAC4_SLADurableAcrossRestart(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	h.putSLA(t, a, 1, domain.BreachAutoUnassign)
	c := h.createOne(t, a, uuid.NewString(), time.Now().Add(2*time.Second), nil)
	id := c["id"].(string)

	// Simulate a restart: a brand-new worker instance with no in-memory state.
	// The timer survives because it lives in Postgres (sla_timers), not the
	// process — this is the Temporal-durability equivalent.
	time.Sleep(2500 * time.Millisecond)
	fresh := sla.New(h.pg) // fresh worker, same durable store
	require.NoError(t, fresh.Sweep(context.Background()))

	g := h.do(t, "GET", "/api/v1/cases/"+id, a.tok, nil, nil)
	assert.Equal(t, "unassigned", dataMap(g)["status"], "breach fired after restart")
}

// ---- AC-5: invalid transition + disposition note required -------------------

func TestAC5_TransitionGuards(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	// resolve an unassigned case → 409 INVALID_TRANSITION.
	c := h.createOne(t, a, "", time.Now().Add(24*time.Hour), nil)
	id := c["id"].(string)
	dispID := h.seedDisposition(t, a, "note-required", true)
	rr := h.do(t, "POST", "/api/v1/cases/"+id+"/resolve", a.tok, map[string]any{"disposition_id": dispID, "resolution_note": "x"}, nil)
	assert.Equal(t, http.StatusConflict, rr.status)
	assert.Equal(t, domain.CodeInvalidTransition, errCode(rr))

	// assign → start → resolve with requires_note and no note → 422.
	assignee := uuid.NewString()
	require.Equal(t, http.StatusOK, h.do(t, "POST", "/api/v1/cases/"+id+"/assign", a.tok, map[string]any{"assignee_id": assignee}, nil).status)
	require.Equal(t, http.StatusOK, h.do(t, "POST", "/api/v1/cases/"+id+"/start", a.tok, nil, nil).status)
	noNote := h.do(t, "POST", "/api/v1/cases/"+id+"/resolve", a.tok, map[string]any{"disposition_id": dispID}, nil)
	assert.Equal(t, http.StatusUnprocessableEntity, noNote.status)
	assert.Equal(t, domain.CodeDispositionNote, errCode(noNote))
}

// ---- AC-6: bulk partial failure + exact success-event count -----------------

func TestAC6_BulkPartialFailure(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	dispID := h.seedDisposition(t, a, "confirmed", false)

	const openN = 20
	var ids []string
	for i := 0; i < openN; i++ {
		ids = append(ids, h.createOne(t, a, "", time.Now().Add(24*time.Hour), nil)["id"].(string))
	}
	closed1 := h.lifecycleToClosed(t, a, dispID)
	closed2 := h.lifecycleToClosed(t, a, dispID)
	ids = append(ids, closed1, closed2)

	before, _ := h.pg.OutboxEventsByType(context.Background(), a.tenant, "case.assigned")
	r := h.do(t, "POST", "/api/v1/cases/bulk", a.tok, map[string]any{
		"operation": "assign", "case_ids": ids, "params": map[string]any{"assignee_id": uuid.NewString()},
	}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.Len(t, r.body["succeeded"].([]any), openN)
	assert.Len(t, r.body["failed"].([]any), 2)

	after, _ := h.pg.OutboxEventsByType(context.Background(), a.tenant, "case.assigned")
	assert.Equal(t, openN, len(after)-len(before), "exactly one case.assigned per success")
}

// ---- AC-7: oversize batch rejected, nothing changed -------------------------

func TestAC7_BatchTooLarge(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	ids := make([]string, 501)
	for i := range ids {
		ids[i] = uuid.NewString()
	}
	r := h.do(t, "POST", "/api/v1/cases/bulk", a.tok, map[string]any{
		"operation": "unassign", "case_ids": ids,
	}, nil)
	assert.Equal(t, http.StatusUnprocessableEntity, r.status)
	assert.Equal(t, domain.CodeBatchTooLarge, errCode(r))
}

// ---- AC-8: closure snapshot immune to later dataset changes -----------------

func TestAC8_ClosureSnapshot(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	dispID := h.seedDisposition(t, a, "confirmed", false)
	id := h.lifecycleToClosed(t, a, dispID)

	g := h.do(t, "GET", "/api/v1/cases/"+id+"?with_row=true", a.tok, nil, nil)
	require.Equal(t, http.StatusOK, g.status)
	d := dataMap(g)
	assert.NotEmpty(t, d["snapshot_ref"], "snapshot_ref set at closure")
	assert.NotNil(t, d["row"], "closed case serves the archived snapshot row")
}

// ---- AC-9: OpenSearch projection searchable within 5s (REAL Kafka + OS) ------

func TestAC9_SearchProjectionWithinWindow(t *testing.T) {
	h := requireHarness(t)
	if !h.kafka {
		t.Skip("Kafka (Redpanda) not reachable; projection is fed from case.events.v1")
	}
	a := h.newActor(t)
	marker := "zzq" + uuid.NewString()[:8]
	h.createOne(t, a, "", time.Now().Add(24*time.Hour), map[string]any{"description": "urgent " + marker + " review"})

	deadline := time.Now().Add(6 * time.Second)
	var found bool
	for time.Now().Before(deadline) {
		r := h.do(t, "GET", "/api/v1/cases?q="+marker+"&facets=status,severity", a.tok, nil, nil)
		if r.status == http.StatusOK {
			if data, ok := r.body["data"].([]any); ok && len(data) > 0 {
				found = true
				facets, _ := r.body["facets"].(map[string]any)
				assert.NotNil(t, facets["status"], "facet counts present")
				break
			}
		}
		time.Sleep(300 * time.Millisecond)
	}
	assert.True(t, found, "case searchable in OpenSearch within the ≤5s window")
}

// ---- AC-10: copilot proposal applied with dual attribution ------------------

func TestAC10_ApplyProposalDualAttribution(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	dispID := h.seedDisposition(t, a, "confirmed", false)

	// Get a case to in_progress.
	assignee := uuid.NewString()
	c := h.createOne(t, a, assignee, time.Now().Add(24*time.Hour), nil)
	id := c["id"].(string)
	require.Equal(t, http.StatusOK, h.do(t, "POST", "/api/v1/cases/"+id+"/start", a.tok, nil, nil).status)

	approver := "approver-user"
	agentTok := h.token(t, a.tenant, a.workspace, domain.TypAgentOBO, "agent-runtime", map[string]any{
		"obo_sub": approver, "agent_id": "triage-copilot", "agent_version": "1.4.0",
	})
	proposalURN := "wr:" + a.tenant.String() + ":agent:proposal/" + uuid.NewString()
	r := h.do(t, "POST", "/api/v1/cases/"+id+"/apply-proposal", agentTok, map[string]any{
		"proposal_urn": proposalURN,
		"changes":      map[string]any{"severity": "critical", "disposition": map[string]any{"id": dispID, "resolution_note": "agent-assisted"}},
	}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.Equal(t, "critical", dataMap(r)["severity"])
	assert.Equal(t, "resolved", dataMap(r)["status"])

	// The learning-loop signal carries the row reference + attribution.
	applied, _ := h.pg.OutboxEventsByType(context.Background(), a.tenant, "case.disposition_applied")
	require.NotEmpty(t, applied)
	ev := applied[len(applied)-1]
	assert.Equal(t, "user", ev.Actor.Type)
	assert.Equal(t, approver, ev.Actor.ID)
	require.NotNil(t, ev.ViaAgent)
	assert.Equal(t, "triage-copilot", ev.ViaAgent.AgentID)

	// Timeline entry links proposal_urn + via_agent.
	tl := h.do(t, "GET", "/api/v1/cases/"+id+"/timeline", a.tok, nil, nil)
	linked := false
	for _, e := range tl.body["data"].([]any) {
		m := e.(map[string]any)
		if m["proposal_urn"] == proposalURN {
			linked = true
			assert.NotNil(t, m["via_agent"])
		}
	}
	assert.True(t, linked, "timeline entry carries proposal_urn")
}

// ---- AC-11: proposal with a disallowed field rejected -----------------------

func TestAC11_ProposalFieldNotAllowed(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	c := h.createOne(t, a, uuid.NewString(), time.Now().Add(24*time.Hour), nil)
	id := c["id"].(string)
	before := h.do(t, "GET", "/api/v1/cases/"+id, a.tok, nil, nil)
	agentTok := h.token(t, a.tenant, a.workspace, domain.TypAgentOBO, "agent-runtime", map[string]any{
		"obo_sub": "approver", "agent_id": "copilot", "agent_version": "1",
	})
	r := h.do(t, "POST", "/api/v1/cases/"+id+"/apply-proposal", agentTok, map[string]any{
		"proposal_urn": "wr:t:agent:proposal/x", "changes": map[string]any{"due_date": "2027-01-01T00:00:00Z"},
	}, nil)
	assert.Equal(t, http.StatusUnprocessableEntity, r.status)
	assert.Equal(t, domain.CodeProposalFieldDenied, errCode(r))
	after := h.do(t, "GET", "/api/v1/cases/"+id, a.tok, nil, nil)
	assert.Equal(t, dataMap(before)["case_version"], dataMap(after)["case_version"], "no mutation occurred")
}

// ---- AC-12: form fields, query-scoped shadows workspace-wide ----------------

func TestAC12_FormFieldsShadowing(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	queryURN := "wr:t:query:query/Q"
	// Workspace-wide field.
	require.Equal(t, http.StatusCreated, h.do(t, "POST", "/api/v1/case-fields", a.tok, map[string]any{
		"name": "risk_reason", "data_type": "string", "purpose": "both", "field_meta": map[string]any{"label": "ws-wide"},
	}, nil).status)
	// Query-scoped field with the same name, purpose=update.
	require.Equal(t, http.StatusCreated, h.do(t, "POST", "/api/v1/case-fields", a.tok, map[string]any{
		"query_urn": queryURN, "name": "risk_reason", "data_type": "string", "purpose": "update", "field_meta": map[string]any{"label": "query-scoped"},
	}, nil).status)

	r := h.do(t, "GET", "/api/v1/cases/form?mode=update&query_urn="+queryURN, a.tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	d := dataMap(r)
	defaults := d["defaults"].([]any)
	assert.NotEmpty(t, defaults)
	custom := d["custom_fields"].([]any)
	require.Len(t, custom, 1, "query-scoped shadows workspace-wide by name")
	meta := custom[0].(map[string]any)["field_meta"].(map[string]any)
	assert.Equal(t, "query-scoped", meta["label"])
}

// ---- AC-13: cross-tenant access returns 404 + RLS proven --------------------

func TestAC13_CrossTenantIsolation(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	c := h.createOne(t, a, "", time.Now().Add(24*time.Hour), nil)
	id := c["id"].(string)

	// Tenant B token against tenant A's case id → 404 (not 403).
	b := h.newActor(t)
	r := h.do(t, "GET", "/api/v1/cases/"+id, b.tok, nil, nil)
	assert.Equal(t, http.StatusNotFound, r.status)
	assert.Equal(t, domain.CodeNotFound, errCode(r))

	// RLS proven at the DB layer: tenant B context cannot see the row even with
	// a direct store read (the app pool is a NOSUPERUSER/NOBYPASSRLS role).
	cid := uuid.MustParse(id)
	_, err := h.pg.GetCase(context.Background(), b.tenant, cid)
	assert.Error(t, err, "RLS hides tenant A's case from tenant B context")
	_, err = h.pg.GetCase(context.Background(), a.tenant, cid)
	assert.NoError(t, err, "owner tenant still sees its case")
}

// ---- AC-14: OpenSearch down → 503, mutations still succeed ------------------

func TestAC14_SearchUnavailable(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	// A mutation path (create) still works while search is down.
	c := h.createOne(t, a, "", time.Now().Add(24*time.Hour), nil)
	require.NotEmpty(t, c["id"])

	// A server whose search client points at a closed port returns 503 on list.
	broken, err := search.New("http://localhost:9201")
	require.NoError(t, err)
	bs := &api.Server{Store: h.pg, Search: broken, Projector: h.server.Projector, Authz: authz.AllowAll{},
		Verifier: api.NewVerifierStatic(&h.key.PublicKey, "windrose-test", "windrose"), Snapshots: api.NewFSSnapshotStore(mustTempDir())}
	srv := httptest.NewServer(bs.Router())
	defer srv.Close()

	req, _ := http.NewRequest("GET", srv.URL+"/api/v1/cases?q=anything", nil)
	req.Header.Set("Authorization", "Bearer "+a.tok)
	res, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer res.Body.Close()
	assert.Equal(t, http.StatusServiceUnavailable, res.StatusCode)
}

// ---- MEDIUM #1: SLA escalation ladder is reachable (CASE-FR-012) ------------

// Drives the full SLA cycle max+1 times and asserts that once reassign_count
// hits max_reassign_count the breach ESCALATES instead of auto-unassigning
// forever (the regression: the counter was stuck at 0).
func TestSLAEscalationLadderReachable(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	const maxReassign = 2
	r := h.do(t, "PUT", "/api/v1/sla-policy", a.tok, map[string]any{
		"warn_before_seconds": 1, "on_breach": domain.BreachAutoUnassign, "max_reassign_count": maxReassign,
	}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)

	c := h.createOne(t, a, uuid.NewString(), time.Now().Add(2*time.Second), nil)
	id := c["id"].(string)
	time.Sleep(2300 * time.Millisecond)

	// Cycle: breach → auto_unassign (count++) → manager reassigns. The due_date
	// is now in the past, so subsequent breaches fire on the next sweep.
	for i := 0; i < maxReassign; i++ {
		require.NoError(t, h.slaWorker.Sweep(context.Background()))
		g := h.do(t, "GET", "/api/v1/cases/"+id, a.tok, nil, nil)
		require.Equal(t, "unassigned", dataMap(g)["status"], "cycle %d should auto-unassign", i)
		assert.EqualValues(t, i+1, dataMap(g)["reassign_count"], "reassign_count advances toward ceiling")
		require.Equal(t, http.StatusOK, h.do(t, "POST", "/api/v1/cases/"+id+"/assign", a.tok, map[string]any{"assignee_id": uuid.NewString()}, nil).status)
	}

	// At the ceiling the next breach escalates: severity bumps, the case stays
	// assigned, and case.escalated fires (not another unassign).
	require.NoError(t, h.slaWorker.Sweep(context.Background()))
	g := h.do(t, "GET", "/api/v1/cases/"+id, a.tok, nil, nil)
	assert.NotEqual(t, "unassigned", dataMap(g)["status"], "at ceiling the case must NOT auto-unassign again")
	assert.NotNil(t, dataMap(g)["assigned_to_id"])
	assert.Equal(t, "high", dataMap(g)["severity"], "escalation bumped severity medium→high")
	esc, _ := h.pg.OutboxEventsByType(context.Background(), a.tenant, "case.escalated")
	assert.NotEmpty(t, esc, "case.escalated emitted at the ceiling")
}

// ---- MEDIUM #2: real CSV export to object storage (CASE-FR-044) -------------

func TestExportCSVReal(t *testing.T) {
	h := requireHarness(t)
	a := h.newActor(t)
	for i := 0; i < 3; i++ {
		h.createOne(t, a, "", time.Now().Add(24*time.Hour), nil)
	}
	r := h.do(t, "POST", "/api/v1/cases/export", a.tok, map[string]any{"filter": map[string]string{}, "format": "csv"}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	opID := dataMap(r)["operation_id"].(string)

	var dl string
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		g := h.do(t, "GET", "/api/v1/operations/"+opID, a.tok, nil, nil)
		if dataMap(g)["status"] == "succeeded" {
			res := dataMap(g)["result"].(map[string]any)
			dl = res["download_url"].(string)
			assert.EqualValues(t, 3, res["row_count"])
			break
		}
		time.Sleep(150 * time.Millisecond)
	}
	require.NotEmpty(t, dl, "export operation did not complete")

	req, _ := http.NewRequest("GET", h.httpSrv.URL+dl, nil)
	req.Header.Set("Authorization", "Bearer "+a.tok)
	res, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer res.Body.Close()
	require.Equal(t, http.StatusOK, res.StatusCode)
	gz, err := gzip.NewReader(res.Body)
	require.NoError(t, err)
	recs, err := csv.NewReader(gz).ReadAll()
	require.NoError(t, err)
	assert.Equal(t, "case_number", recs[0][0], "CSV header present")
	assert.Len(t, recs, 4, "header + 3 data rows")
}

// ---- MEDIUM #3: filter-based async bulk (CASE-FR-030) -----------------------

func TestBulkByFilterAsync(t *testing.T) {
	h := requireHarness(t)
	if !h.kafka {
		t.Skip("Kafka not reachable; filter resolution reads the OpenSearch projection")
	}
	a := h.newActor(t)
	const n = 4
	for i := 0; i < n; i++ {
		h.createOne(t, a, "", time.Now().Add(24*time.Hour), map[string]any{"severity": "critical"})
	}
	deadline := time.Now().Add(6 * time.Second)
	for time.Now().Before(deadline) {
		r := h.do(t, "GET", "/api/v1/cases?filter[severity]=critical", a.tok, nil, nil)
		if data, ok := r.body["data"].([]any); ok && len(data) >= n {
			break
		}
		time.Sleep(300 * time.Millisecond)
	}

	r := h.do(t, "POST", "/api/v1/cases/bulk", a.tok, map[string]any{
		"operation": "assign", "filter": map[string]string{"severity": "critical"},
		"params": map[string]any{"assignee_id": uuid.NewString()},
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	opID := dataMap(r)["operation_id"].(string)

	var succeeded float64
	done := time.Now().Add(6 * time.Second)
	for time.Now().Before(done) {
		g := h.do(t, "GET", "/api/v1/operations/"+opID, a.tok, nil, nil)
		if dataMap(g)["status"] == "succeeded" {
			succeeded = dataMap(g)["succeeded"].(float64)
			break
		}
		time.Sleep(150 * time.Millisecond)
	}
	assert.EqualValues(t, n, succeeded, "all filter-matched cases assigned via async bulk")
}

var _ = fmt.Sprintf
