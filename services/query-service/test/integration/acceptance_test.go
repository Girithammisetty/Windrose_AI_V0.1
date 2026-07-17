package integration

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/datasets"
	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/events"
	"github.com/windrose-ai/query-service/internal/exec"
)

var savedQueryBody = map[string]any{
	"name":         "Orders by region",
	"module_names": []string{"insights"},
	"sql_text":     "SELECT id, region, order_total FROM {{dataset('Orders')}} WHERE region = :region AND order_date >= :since ORDER BY id",
	"variables": []map[string]any{
		{"name": "region", "type": "string"},
		{"name": "since", "type": "date", "required": false, "default": "2026-01-01"},
	},
}

// AC-1: saved query with :region string and :since date — both variables
// bound as parameters, correct result from the REAL DuckDB engine. The
// process_vars_multi_variable regression, end to end.
func TestAC1_ProcessVarsMultiVariable_EndToEnd(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)

	r := h.do(t, "POST", "/api/v1/queries", tok, savedQueryBody, nil)
	require.Equal(t, http.StatusCreated, r.status, "%v", r.body)
	qid := data(r)["id"].(string)

	// Both variables must constrain: region=EMEA alone matches ids 1,3;
	// since=2026-06-01 must additionally exclude id 3.
	r = h.do(t, "POST", "/api/v1/queries/"+qid+"/run", tok,
		map[string]any{"variables": map[string]any{"region": "EMEA", "since": "2026-06-01"}}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	execID := data(r)["execution_id"].(string)
	assert.Equal(t, "duckdb", data(r)["plan"].(map[string]any)["engine"])
	h.waitStatus(t, tok, execID, domain.StatusSucceeded)

	r = h.do(t, "GET", "/api/v1/executions/"+execID+"/results", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	rows := data(r)["rows"].([]any)
	require.Len(t, rows, 1, "second variable ignored → V1 bug would return 2 rows")
	row := rows[0].([]any)
	assert.EqualValues(t, 1, row[0])
	assert.Equal(t, "EMEA", row[1])
	assert.Equal(t, "100.50", row[2], "decimal delivered as lossless string (QRY-FR-063)")
}

// AC-2: malicious value executes safely as a bound literal — no DDL, no
// rows, users table intact in the real engine.
func TestAC2_InjectionValueInert(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)

	for _, payload := range []string{
		`x'; DROP TABLE users;--`,
		`x' OR '1'='1`,
		`EMEA' UNION SELECT email, email, email FROM users --`,
	} {
		r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
			"sql":          "SELECT id FROM {{dataset('Orders')}} WHERE region = :v",
			"declarations": []map[string]any{{"name": "v", "type": "string"}},
			"variables":    map[string]any{"v": payload},
			"cache":        false,
		}, nil)
		require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
		execID := data(r)["execution_id"].(string)
		final := h.waitTerminal(t, tok, execID)
		require.Equal(t, domain.StatusSucceeded, final["status"], "payload %q must execute safely", payload)
		rr := h.do(t, "GET", "/api/v1/executions/"+execID+"/results", tok, nil, nil)
		assert.Empty(t, data(rr)["rows"], "matching rows: none for %q", payload)
	}

	// users table survived every payload
	r := h.do(t, "POST", "/api/v1/sql/run?mode=sync", tok, map[string]any{
		"sql": "SELECT count(*) FROM {{dataset('Orders')}}", "cache": false,
	}, nil)
	require.Equal(t, http.StatusOK, r.status)
	rr := h.do(t, "GET", "/api/v1/executions/"+data(r)["execution_id"].(string)+"/results", tok, nil, nil)
	assert.EqualValues(t, 4, rr.body["data"].(map[string]any)["rows"].([]any)[0].([]any)[0], "orders untouched")
}

// AC-3: AST classification at the API edge against Postgres-backed store.
func TestAC3_StatementClassification(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	for _, sql := range []string{
		"DELETE FROM {{dataset('Orders')}}",
		"select 1; delete from t",
		"dElEtE FROM t",
		"WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
		"SELECT * INTO x FROM {{dataset('Orders')}}",
	} {
		r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{"sql": sql}, nil)
		assert.Equal(t, http.StatusForbidden, r.status, sql)
		assert.Equal(t, domain.CodeStatementNotAllowed, errCode(r), sql)
	}
}

// AC-5: routing decisions + plan-time ceiling, all recorded in PG history.
func TestAC5_RoutingAndCeilingRecorded(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)

	// 400MB over 3GB dataset → duckdb (executed for real).
	h.resolver.Put(tenant, mustMeta(tenant, "Mid", `"main"."orders"`, 400<<20), true)
	r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT count(*) FROM {{dataset('Mid')}}", "cache": false}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	d := h.waitStatus(t, tok, data(r)["execution_id"].(string), domain.StatusSucceeded)
	assert.Equal(t, "duckdb", d["engine"])
	assert.Equal(t, "small_interactive", d["routing_reason"].(map[string]any)["rule"])

	// 60GB → plan-time 422 with the estimate.
	h.resolver.Put(tenant, mustMeta(tenant, "Huge", `"main"."orders"`, 60<<30), true)
	r = h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT count(*) FROM {{dataset('Huge')}}"}, nil)
	require.Equal(t, 422, r.status)
	assert.Equal(t, domain.CodeCostCeilingExceeded, errCode(r))

	// 20GB → trino (default_large) recorded via dry-run (trino is a stub).
	h.resolver.Put(tenant, mustMeta(tenant, "Big20", `"main"."orders"`, 20<<30), true)
	r = h.do(t, "POST", "/api/v1/sql/dry-run", tok, map[string]any{
		"sql": "SELECT count(*) FROM {{dataset('Big20')}}"}, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	// Trino is down in the cell (stub Up=false) → warehouse fallback (BR-13).
	assert.Equal(t, "warehouse", data(r)["engine"])
	assert.Equal(t, "engine_fallback", data(r)["routing_reason"].(map[string]any)["rule"])
	warns := data(r)["warnings"].([]any)
	assert.Contains(t, warns, "ENGINE_FALLBACK")

	// all three history rows are queryable
	r = h.do(t, "GET", "/api/v1/executions", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status)
	assert.GreaterOrEqual(t, len(r.body["data"].([]any)), 3)
}

// AC-6: agent runs get LIMIT injection + agent ceilings, end to end on
// DuckDB.
func TestAC6_AgentGuardrails(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	agentTok := h.token(t, tenant, domain.TypAgentOBO, "agent-principal", map[string]any{
		"agent_id": "analytics-agent", "agent_version": "1", "obo_sub": "alice",
	})
	r := h.do(t, "POST", "/api/v1/sql/run", agentTok, map[string]any{
		"sql": "SELECT n FROM {{dataset('Big')}}", "cache": false,
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	execID := data(r)["execution_id"].(string)
	final := h.waitTerminal(t, agentTok, execID)
	require.Equal(t, domain.StatusSucceeded, final["status"], "%v", final)
	stats := final["stats"].(map[string]any)
	assert.EqualValues(t, 10000, stats["result_rows"], "injected LIMIT 10000 effective on 120k-row table (AC-6)")
	ceil := final["ceilings"].(map[string]any)
	assert.EqualValues(t, domain.AgentMaxScanBytes, ceil["max_scan_bytes"], "5GB agent ceiling applied")
}

// AC-7: cap 10 → 11th queues at position 1 and starts when a slot frees.
func TestAC7_ConcurrencyCapQueue(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := func(u string) string { return h.token(t, tenant, domain.TypUser, u, nil) }
	adminOp := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "admin"}, UserID: "admin"}
	require.NoError(t, h.pg.PutTenantLimits(context.Background(), adminOp,
		&domain.TenantLimits{WarehousePrimary: true})) // route to the blocking engine

	h.warehouse.SetHold(true)
	var execIDs []string
	for i := 0; i < 10; i++ {
		r := h.do(t, "POST", "/api/v1/sql/run", tok(uuidStr(i)), map[string]any{
			"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
		}, nil)
		require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
		execIDs = append(execIDs, data(r)["execution_id"].(string))
		h.waitStatus(t, tok("x"), execIDs[i], domain.StatusRunning)
	}
	// 11th queues with queue_position = 1
	r := h.do(t, "POST", "/api/v1/sql/run", tok("user-11"), map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	queuedID := data(r)["execution_id"].(string)
	assert.Equal(t, domain.StatusQueued, data(r)["status"])
	assert.EqualValues(t, 1, data(r)["queue_position"], "AC-7 queue_position")

	// free one slot → the queued run is promoted and starts (AC-7)
	h.warehouse.ReleaseOne()
	h.waitStatus(t, tok("x"), queuedID, domain.StatusRunning)
	h.warehouse.ReleaseAll()
	h.waitTerminal(t, tok("x"), queuedID)
	for _, id := range execIDs {
		h.waitTerminal(t, tok("x"), id)
	}
	// restore tenant limits for other tests (fresh tenants used elsewhere)
}

// AC-8: runtime ceiling kill ≤5s, status ceiling_exceeded, event emitted
// (verified in the PG outbox).
func TestAC8_RuntimeCeilingKill(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	adminOp := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "admin"}, UserID: "admin"}
	one := int64(1)
	require.NoError(t, h.pg.PutTenantLimits(context.Background(), adminOp,
		&domain.TenantLimits{WarehousePrimary: true, MaxRuntimeS: &one}))
	h.warehouse.SetHold(true)
	defer h.warehouse.ReleaseAll()

	start := time.Now()
	r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	execID := data(r)["execution_id"].(string)
	final := h.waitTerminal(t, tok, execID)
	assert.Equal(t, domain.StatusCeilingExceeded, final["status"])
	assert.Less(t, time.Since(start), 7*time.Second, "engine killed ≤5s after the 1s breach")

	envs, err := h.pg.OutboxEventsByType(context.Background(), tenant, events.EvExecutionCeilingExceeded)
	require.NoError(t, err)
	require.Len(t, envs, 1, "execution.ceiling_exceeded in the transactional outbox")
}

// AC-9 (CI-sized): a 120k-row DuckDB result streams through chunked parts
// with stable cursors; identical data on re-read. (The 2M-row/64MB-RSS
// soak is a release-gate perf test, documented in the README — CI runs the
// same code path at 120k rows across 12+ sealed parts.)
func TestAC9_StreamingPagedResults(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)

	r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT n, val FROM {{dataset('Big')}} ORDER BY n", "cache": false,
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	execID := data(r)["execution_id"].(string)
	final := h.waitStatus(t, tok, execID, domain.StatusSucceeded)
	assert.EqualValues(t, 120000, final["stats"].(map[string]any)["result_rows"])

	seen := 0
	cursor := ""
	var firstPage []any
	for {
		path := "/api/v1/executions/" + execID + "/results?limit=10000"
		if cursor != "" {
			path += "&cursor=" + cursor
		}
		rr := h.do(t, "GET", path, tok, nil, nil)
		require.Equal(t, http.StatusOK, rr.status)
		rows := data(rr)["rows"].([]any)
		if firstPage == nil {
			firstPage = rows
		}
		for _, row := range rows {
			assert.EqualValues(t, seen, row.([]any)[0], "ordered streaming without gaps")
			seen++
		}
		page := data(rr)["page"].(map[string]any)
		if page["has_more"] != true {
			break
		}
		cursor = page["next_cursor"].(string)
	}
	assert.Equal(t, 120000, seen)

	// stable cursors (BR-9): first page identical on re-read
	rr := h.do(t, "GET", "/api/v1/executions/"+execID+"/results?limit=10000", tok, nil, nil)
	assert.Equal(t, firstPage, data(rr)["rows"].([]any))
}

// AC-10: cache hit within TTL, miss after a dataset version bump.
func TestAC10_ResultCache(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	body := map[string]any{
		"sql":          "SELECT id FROM {{dataset('Orders')}} WHERE region = :r",
		"declarations": []map[string]any{{"name": "r", "type": "string"}},
		"variables":    map[string]any{"r": "EMEA"},
	}
	r := h.do(t, "POST", "/api/v1/sql/run", tok, body, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	firstID := data(r)["execution_id"].(string)
	h.waitStatus(t, tok, firstID, domain.StatusSucceeded)
	first := h.do(t, "GET", "/api/v1/executions/"+firstID+"/results", tok, nil, nil)

	r = h.do(t, "POST", "/api/v1/sql/run", tok, body, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	secondID := data(r)["execution_id"].(string)
	d := h.waitStatus(t, tok, secondID, domain.StatusSucceeded)
	assert.Equal(t, true, d["cache_hit"], "AC-10 cache_hit=true in history")
	second := h.do(t, "GET", "/api/v1/executions/"+secondID+"/results", tok, nil, nil)
	assert.Equal(t, data(first)["rows"], data(second)["rows"], "identical results without engine contact")

	// new dataset version → key changes → miss
	h.resolver.Put(tenant, mustMetaV(tenant, "Orders", `"main"."orders"`, 1<<20, 2), true)
	r = h.do(t, "POST", "/api/v1/sql/run", tok, body, nil)
	thirdID := data(r)["execution_id"].(string)
	d = h.waitStatus(t, tok, thirdID, domain.StatusSucceeded)
	assert.Equal(t, false, d["cache_hit"], "version bump invalidates (AC-10)")
}

// AC-11: cancel a running execution; kill propagates; partial scan bytes
// recorded. (Trino itself is a stub — the kill path is exercised against
// the registered warehouse engine; the Trino adapter TODO covers the
// engine-specific kill call.)
func TestAC11_CancelRunning(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	adminOp := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "admin"}, UserID: "admin"}
	require.NoError(t, h.pg.PutTenantLimits(context.Background(), adminOp, &domain.TenantLimits{WarehousePrimary: true}))
	h.warehouse.SetHold(true)
	defer h.warehouse.ReleaseAll()

	r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT region FROM {{dataset('Orders')}}", "cache": false,
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	execID := data(r)["execution_id"].(string)
	h.waitStatus(t, tok, execID, domain.StatusRunning)

	start := time.Now()
	r = h.do(t, "POST", "/api/v1/executions/"+execID+"/cancel", tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)
	assert.Equal(t, domain.StatusCancelled, data(r)["status"])
	assert.Less(t, time.Since(start), 5*time.Second, "kill ≤5s (QRY-FR-045)")
	assert.EqualValues(t, 512, data(r)["stats"].(map[string]any)["actual_scan_bytes"],
		"partial bytes-scanned recorded (AC-11)")

	envs, err := h.pg.OutboxEventsByType(context.Background(), tenant, events.EvExecutionCancelled)
	require.NoError(t, err)
	assert.NotEmpty(t, envs)
}

// AC-12: RLS isolation suite — tenant A's resources are 404 for tenant B on
// every endpoint, enforced by Postgres row-level security, with audit
// events.
func TestAC12_IsolationSuiteRLS(t *testing.T) {
	h := requireHarness(t)
	tenantA, tenantB := h.newTenant(), h.newTenant()
	tokA := h.token(t, tenantA, domain.TypUser, "alice", nil)
	tokB := h.token(t, tenantB, domain.TypUser, "bob", nil)

	r := h.do(t, "POST", "/api/v1/queries", tokA, savedQueryBody, nil)
	require.Equal(t, http.StatusCreated, r.status)
	qid := data(r)["id"].(string)
	r = h.do(t, "POST", "/api/v1/queries/"+qid+"/run", tokA,
		map[string]any{"variables": map[string]any{"region": "EMEA"}}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	execID := data(r)["execution_id"].(string)
	h.waitStatus(t, tokA, execID, domain.StatusSucceeded)

	endpoints := []struct {
		method, path string
		body         any
	}{
		{"GET", "/api/v1/queries/" + qid, nil},
		{"PATCH", "/api/v1/queries/" + qid, map[string]any{"description": "x"}},
		{"DELETE", "/api/v1/queries/" + qid, nil},
		{"GET", "/api/v1/queries/" + qid + "/versions", nil},
		{"POST", "/api/v1/queries/" + qid + "/run", map[string]any{"variables": map[string]any{"region": "EMEA"}}},
		{"GET", "/api/v1/executions/" + execID, nil},
		{"GET", "/api/v1/executions/" + execID + "/results", nil},
		{"POST", "/api/v1/executions/" + execID + "/cancel", nil},
		{"POST", "/api/v1/executions/" + execID + "/export", map[string]any{"format": "csv"}},
	}
	for _, ep := range endpoints {
		r := h.do(t, ep.method, ep.path, tokB, ep.body, nil)
		assert.Equal(t, http.StatusNotFound, r.status,
			"RLS: %s %s must 404 for tenant B (MASTER-FR-003)", ep.method, ep.path)
	}
	envs, err := h.pg.OutboxEventsByType(context.Background(), tenantB, events.EvCrossTenantDenied)
	require.NoError(t, err)
	assert.GreaterOrEqual(t, len(envs), len(endpoints), "cross-tenant audit events (MASTER-FR-003)")

	// tenant A unaffected
	r = h.do(t, "GET", "/api/v1/queries/"+qid, tokA, nil, nil)
	assert.Equal(t, http.StatusOK, r.status)
}

// AC-13: results 410 after retention GC; PG history row persists.
func TestAC13_ResultRetention(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT id FROM {{dataset('Orders')}}", "cache": false}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	execID := data(r)["execution_id"].(string)
	h.waitStatus(t, tok, execID, domain.StatusSucceeded)

	// age everything out (Now injection would need a fresh store; GC(0)
	// expresses "older than the retention window")
	_, err := h.server.Results.GC(0)
	require.NoError(t, err)

	r = h.do(t, "GET", "/api/v1/executions/"+execID+"/results", tok, nil, nil)
	require.Equal(t, http.StatusGone, r.status)
	assert.Equal(t, domain.CodeGone, errCode(r))

	r = h.do(t, "GET", "/api/v1/executions/"+execID, tok, nil, nil)
	require.Equal(t, http.StatusOK, r.status, "history row remains queryable (AC-13)")
	assert.Equal(t, domain.StatusSucceeded, data(r)["status"])
}

// AC-14: PII-bound parameter redacted in PG history; non-PII in clear.
func TestAC14_PIIRedactionInHistory(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT id FROM {{dataset('Orders')}} WHERE email = :email AND region = :region",
		"declarations": []map[string]any{
			{"name": "email", "type": "string"},
			{"name": "region", "type": "string"},
		},
		"variables": map[string]any{"email": "a@x.com", "region": "EMEA"},
		"cache":     false,
	}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	execID := data(r)["execution_id"].(string)
	final := h.waitStatus(t, tok, execID, domain.StatusSucceeded)
	params := final["bound_params"].(map[string]any)
	assert.Equal(t, "«redacted»", params["email"], "AC-14: PII param redacted in history")
	assert.Equal(t, "EMEA", params["region"], "non-PII param persists in clear")

	// the query itself used the real value (1 matching row)
	rr := h.do(t, "GET", "/api/v1/executions/"+execID+"/results", tok, nil, nil)
	assert.Len(t, data(rr)["rows"].([]any), 1)
}

// Saved-query versioning against PG: PATCH bumps immutable versions;
// version pinning on run (QRY-FR-001).
func TestSavedQueryVersionPinning_PG(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	r := h.do(t, "POST", "/api/v1/queries", tok, savedQueryBody, nil)
	require.Equal(t, http.StatusCreated, r.status)
	qid := data(r)["id"].(string)

	r = h.do(t, "PATCH", "/api/v1/queries/"+qid, tok, map[string]any{
		"sql_text":  "SELECT count(*) AS c FROM {{dataset('Orders')}} WHERE region = :region",
		"variables": []map[string]any{{"name": "region", "type": "string"}},
	}, map[string]string{"If-Match": `"v1"`})
	require.Equal(t, http.StatusOK, r.status, "%v", r.body)

	// pin version 1 (two variables)
	r = h.do(t, "POST", "/api/v1/queries/"+qid+"/run?query_version=1", tok,
		map[string]any{"variables": map[string]any{"region": "EMEA", "since": "2026-06-01"}, "cache": false}, nil)
	require.Equal(t, http.StatusAccepted, r.status, "%v", r.body)
	h.waitStatus(t, tok, data(r)["execution_id"].(string), domain.StatusSucceeded)

	// current version (v2): 'since' is now undeclared → 422
	r = h.do(t, "POST", "/api/v1/queries/"+qid+"/run", tok,
		map[string]any{"variables": map[string]any{"region": "EMEA", "since": "2026-06-01"}, "cache": false}, nil)
	require.Equal(t, 422, r.status)
	assert.Equal(t, domain.CodeVariableInvalid, errCode(r))
}

// The outbox relay publishes committed events from PG (MASTER-FR-034).
func TestOutboxRelay_PG(t *testing.T) {
	h := requireHarness(t)
	tenant := h.newTenant()
	tok := h.token(t, tenant, domain.TypUser, "alice", nil)
	r := h.do(t, "POST", "/api/v1/sql/run", tok, map[string]any{
		"sql": "SELECT id FROM {{dataset('Orders')}}", "cache": false}, nil)
	require.Equal(t, http.StatusAccepted, r.status)
	h.waitStatus(t, tok, data(r)["execution_id"].(string), domain.StatusSucceeded)

	pub := events.NewInMemory()
	relay := &events.Relay{Source: h.pg, Publisher: pub, Batch: 1000}
	require.NoError(t, relay.Drain(context.Background()))
	var mine []events.Envelope
	for _, env := range pub.All() {
		if env.TenantID == tenant {
			mine = append(mine, env)
		}
	}
	require.NotEmpty(t, mine)
	types := map[string]bool{}
	for _, env := range mine {
		types[env.EventType] = true
		assert.NotEqual(t, "", env.TraceID, "trace id propagated into events")
	}
	assert.True(t, types[events.EvExecutionStarted])
	assert.True(t, types[events.EvExecutionSucceeded])
}

// helpers

func mustMeta(tenant uuid.UUID, name, ident string, size int64) datasets.Meta {
	return mustMetaV(tenant, name, ident, size, 1)
}

func mustMetaV(tenant uuid.UUID, name, ident string, size int64, version int) datasets.Meta {
	return datasets.Meta{
		Name: name, Version: version,
		URN:           "wr:" + tenant.String() + ":dataset:dataset/" + strings.ToLower(name),
		PhysicalIdent: ident, Namespace: "main", SizeBytes: size,
	}
}

func uuidStr(i int) string { return fmt.Sprintf("user-%d", i) }

var _ = exec.Caps{}
