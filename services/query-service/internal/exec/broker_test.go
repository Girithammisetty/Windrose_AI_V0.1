package exec

import (
	"context"
	"encoding/json"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/windrose-ai/query-service/internal/datasets"
	"github.com/windrose-ai/query-service/internal/domain"
	"github.com/windrose-ai/query-service/internal/engine"
	"github.com/windrose-ai/query-service/internal/events"
	"github.com/windrose-ai/query-service/internal/results"
	"github.com/windrose-ai/query-service/internal/store"
)

// fakeEngine is a controllable Engine for broker-tier tests.
type fakeEngine struct {
	name      string
	healthy   bool
	cols      []engine.Column
	rows      [][]any
	scanBytes int64
	rowDelay  time.Duration
	block     chan struct{} // non-nil: hold until closed or ctx done

	mu       sync.Mutex
	lastSQL  string
	lastArgs []any
	calls    int
}

func (f *fakeEngine) Name() string                 { return f.name }
func (f *fakeEngine) Healthy(context.Context) bool { return f.healthy }
func (f *fakeEngine) LastSQL() string              { f.mu.Lock(); defer f.mu.Unlock(); return f.lastSQL }
func (f *fakeEngine) LastArgs() []any              { f.mu.Lock(); defer f.mu.Unlock(); return f.lastArgs }
func (f *fakeEngine) Calls() int                   { f.mu.Lock(); defer f.mu.Unlock(); return f.calls }

func (f *fakeEngine) Execute(ctx context.Context, q engine.Query, sink engine.Sink) (engine.Stats, error) {
	f.mu.Lock()
	f.lastSQL, f.lastArgs = q.SQL, q.Args
	f.calls++
	f.mu.Unlock()
	if f.block != nil {
		select {
		case <-f.block:
		case <-ctx.Done():
			return engine.Stats{ScanBytes: f.scanBytes / 2}, ctx.Err() // partial accounting
		}
	}
	cols := f.cols
	if cols == nil {
		cols = []engine.Column{{Name: "n", Type: "integer"}}
	}
	if err := sink.Start(cols); err != nil {
		return engine.Stats{}, err
	}
	var stats engine.Stats
	for _, row := range f.rows {
		if f.rowDelay > 0 {
			select {
			case <-time.After(f.rowDelay):
			case <-ctx.Done():
				return stats, ctx.Err()
			}
		}
		if ctx.Err() != nil {
			return stats, ctx.Err()
		}
		if err := sink.Row(row); err != nil {
			return stats, err
		}
		stats.Rows++
	}
	stats.ScanBytes = f.scanBytes
	return stats, nil
}

type brokerFixture struct {
	broker   *Broker
	mem      *store.Mem
	resolver *datasets.Static
	duck     *fakeEngine
	trino    *fakeEngine
	tenant   uuid.UUID
	op       domain.Op
}

func newFixture(t *testing.T) *brokerFixture {
	t.Helper()
	tenant := uuid.New()
	mem := store.NewMem()
	resolver := datasets.NewStatic()
	resolver.Put(tenant, datasets.Meta{
		Name: "Orders", Version: 3,
		URN:           "wr:" + tenant.String() + ":dataset:dataset/orders",
		PhysicalIdent: `"bronze_t"."orders_v3"`,
		Namespace:     "bronze_t",
		SizeBytes:     400 << 20, // 400MB → duckdb small_interactive
		RowCount:      1000,
		Columns: []datasets.Column{
			{Name: "region", Type: "string"},
			{Name: "email", Type: "string", PIITag: "pii:email"},
			{Name: "order_total", Type: "decimal"},
		},
	}, true)
	duck := &fakeEngine{name: engine.NameDuckDB, healthy: true, rows: [][]any{{int64(1)}, {int64(2)}}}
	trino := &fakeEngine{name: engine.NameTrino, healthy: true, rows: [][]any{{int64(1)}}}
	wh := &fakeEngine{name: engine.NameWarehouse, healthy: false}
	b := &Broker{
		Store:    mem,
		Resolver: resolver,
		Engines:  engine.NewRegistry(duck, trino, wh),
		Results:  results.NewStore(t.TempDir()),
		Slots:    NewSlotManager(),
	}
	op := domain.Op{Tenant: tenant, Actor: domain.Actor{Type: "user", ID: "u1"}, UserID: "u1",
		Caller: domain.CallerUser, TraceID: "trace-1"}
	return &brokerFixture{broker: b, mem: mem, resolver: resolver, duck: duck, trino: trino, tenant: tenant, op: op}
}

func (f *brokerFixture) runReq(sql string) RunRequest {
	return RunRequest{
		PlanRequest: PlanRequest{Op: f.op, SQLText: sql, Async: true},
		WorkspaceID: uuid.New(),
		UseCache:    false,
	}
}

func (f *brokerFixture) waitTerminal(t *testing.T, id uuid.UUID) *domain.Execution {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		e, err := f.mem.GetExecution(context.Background(), f.tenant, id)
		require.NoError(t, err)
		if domain.IsTerminalStatus(e.Status) {
			return e
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("execution never reached a terminal state")
	return nil
}

// AC-1 at the broker tier: the engine receives placeholders, not literals,
// and every variable binds.
func TestBrokerParameterizedExecution(t *testing.T) {
	f := newFixture(t)
	req := f.runReq("SELECT region FROM {{dataset('Orders')}} WHERE region = :region AND order_total > :min")
	req.Decls = []domain.VariableDecl{
		{Name: "region", Type: domain.VarString},
		{Name: "min", Type: domain.VarDecimal},
	}
	req.Values = map[string]json.RawMessage{"region": json.RawMessage(`"EMEA"`), "min": json.RawMessage(`10.5`)}
	e, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	assert.Equal(t, domain.StatusSucceeded, final.Status)

	// Engine query log: placeholders, not literals (AC-1).
	sql := f.duck.LastSQL()
	assert.Contains(t, sql, "$1")
	assert.Contains(t, sql, "$2")
	assert.NotContains(t, sql, "EMEA")
	assert.NotContains(t, sql, "10.5")
	assert.Equal(t, []any{"EMEA", "10.5"}, f.duck.LastArgs())
	assert.Contains(t, sql, `"bronze_t"."orders_v3"`, "dataset ref resolved to engine-quoted ident")

	// Routing decision recorded (QRY-FR-040).
	require.NotNil(t, final.RoutingReason)
	assert.Equal(t, "small_interactive", final.RoutingReason.Rule)
	assert.Equal(t, engine.NameDuckDB, final.Engine)
	// History has redacted-capable params, fingerprint, result stats.
	assert.Equal(t, "EMEA", final.BoundParams["region"])
	assert.NotEmpty(t, final.SQLFingerprint)
	assert.Equal(t, int64(2), final.ResultRows)
	assert.NotEmpty(t, final.ResultURI)
}

// AC-5: 60GB estimate → plan-time 422 with the estimate; the rejection is
// recorded in history.
func TestBrokerPlanTimeCeiling(t *testing.T) {
	f := newFixture(t)
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "Huge", Version: 1, URN: "urn:huge", PhysicalIdent: `"bronze_t"."huge"`,
		Namespace: "bronze_t", SizeBytes: 60 << 30,
	}, true)
	req := f.runReq("SELECT count(*) FROM {{dataset('Huge')}}")
	_, err := f.broker.Run(context.Background(), req)
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeCostCeilingExceeded, de.Code)
	assert.Equal(t, 422, de.HTTP)
	details := de.Details.(map[string]any)
	assert.EqualValues(t, 60<<30, details["estimated_scan_bytes"], "estimate included in the 422")

	page, err := f.mem.ListExecutions(context.Background(), f.tenant, store.ExecutionFilter{Status: domain.StatusRejected})
	require.NoError(t, err)
	require.Len(t, page.Data, 1, "rejection recorded in history (QRY-FR-080)")
	assert.Equal(t, domain.CodeCostCeilingExceeded, page.Data[0].Error.Code)
}

// AC-5: 20GB routes to trino with the reason recorded.
func TestBrokerRoutesLargeToTrino(t *testing.T) {
	f := newFixture(t)
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "Big", Version: 1, URN: "urn:big", PhysicalIdent: `"bronze_t"."big"`,
		Namespace: "bronze_t", SizeBytes: 20 << 30,
	}, true)
	req := f.runReq("SELECT count(*) FROM {{dataset('Big')}}")
	e, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	assert.Equal(t, engine.NameTrino, final.Engine)
	assert.Equal(t, "default_large", final.RoutingReason.Rule)
}

// AC-6: agent-class run without LIMIT gets `LIMIT 10000` injected, dry-run
// planning always precedes execution, and the 5GB agent scan ceiling
// applies.
func TestBrokerAgentHardening(t *testing.T) {
	f := newFixture(t)
	agentOp := f.op
	agentOp.Caller = domain.CallerAgent
	agentOp.Actor = domain.Actor{Type: "agent", ID: "agent-1"}
	agentOp.ViaAgent = &domain.ViaAgent{AgentID: "agent-1", Version: "3"}

	req := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	req.Op = agentOp
	e, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	require.Equal(t, domain.StatusSucceeded, final.Status)
	assert.Contains(t, f.duck.LastSQL(), "LIMIT 10000", "LIMIT injection (QRY-FR-022)")
	assert.Equal(t, int64(domain.AgentMaxScanBytes), final.Ceilings.MaxScanBytes, "agent 5GB scan ceiling")
	assert.Equal(t, int64(domain.AgentMaxResultRows), final.Ceilings.MaxResultRows)

	// Agent ceiling rejects what a user-tier run would allow (10GB dataset).
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "Mid", Version: 1, URN: "urn:mid", PhysicalIdent: `"bronze_t"."mid"`,
		Namespace: "bronze_t", SizeBytes: 10 << 30,
	}, true)
	req2 := f.runReq("SELECT count(*) FROM {{dataset('Mid')}}")
	req2.Op = agentOp
	_, err = f.broker.Run(context.Background(), req2)
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeCostCeilingExceeded, de.Code)

	// A user-supplied LIMIT below the cap is preserved.
	req3 := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	req3.Op = agentOp
	req3.Limit = 500
	e3, err := f.broker.Run(context.Background(), req3)
	require.NoError(t, err)
	f.waitTerminal(t, e3.ID)
	assert.Contains(t, f.duck.LastSQL(), "LIMIT 500")

	// An existing outer LIMIT is not double-wrapped.
	req4 := f.runReq("SELECT region FROM {{dataset('Orders')}} LIMIT 7")
	req4.Op = agentOp
	e4, err := f.broker.Run(context.Background(), req4)
	require.NoError(t, err)
	f.waitTerminal(t, e4.ID)
	assert.NotContains(t, f.duck.LastSQL(), "_wr_agent_guard")
}

// AC-8: runtime ceiling kill → status ceiling_exceeded + event emitted.
func TestBrokerRuntimeCeilingKill(t *testing.T) {
	f := newFixture(t)
	f.duck.block = make(chan struct{}) // never closes: query "runs" forever
	one := int64(1)
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), f.op, &domain.TenantLimits{MaxRuntimeS: &one}))
	f.broker.WatchdogGrace = 500 * time.Millisecond

	req := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	start := time.Now()
	e, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	assert.Equal(t, domain.StatusCeilingExceeded, final.Status)
	assert.Less(t, time.Since(start), 6*time.Second, "kill ≤5s after breach")

	envs, err := f.mem.OutboxEventsByType(context.Background(), f.tenant, events.EvExecutionCeilingExceeded)
	require.NoError(t, err)
	require.Len(t, envs, 1, "execution.ceiling_exceeded emitted (AC-8)")
	assert.Equal(t, reasonRuntimeCeiling, envs[0].Payload["ceiling"])
}

// Result-row ceiling breached mid-stream → kill + ceiling_exceeded.
func TestBrokerResultRowsCeiling(t *testing.T) {
	f := newFixture(t)
	rows := make([][]any, 200)
	for i := range rows {
		rows[i] = []any{int64(i)}
	}
	f.duck.rows = rows
	limit := int64(100)
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), f.op, &domain.TenantLimits{MaxResultRows: &limit}))

	e, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	assert.Equal(t, domain.StatusCeilingExceeded, final.Status)
	assert.Contains(t, final.Error.Message, reasonResultRows)
}

// BR-8: actual scan exceeding the ceiling is a kill verdict even when the
// engine finished ("never finish since we started").
func TestBrokerActualScanDriftKill(t *testing.T) {
	f := newFixture(t)
	f.duck.scanBytes = domain.DefaultMaxScanBytes + 1
	e, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	assert.Equal(t, domain.StatusCeilingExceeded, final.Status)
}

// AC-11 (broker tier): cancel a running execution → cancelled with partial
// scan accounting + event.
func TestBrokerCancelRunning(t *testing.T) {
	f := newFixture(t)
	f.duck.block = make(chan struct{})
	f.duck.scanBytes = 1000
	e, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)

	// wait until running
	require.Eventually(t, func() bool {
		cur, _ := f.mem.GetExecution(context.Background(), f.tenant, e.ID)
		return cur.Status == domain.StatusRunning
	}, 2*time.Second, 10*time.Millisecond)

	got, err := f.broker.Cancel(context.Background(), f.op, e.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.StatusCancelled, got.Status)
	assert.Equal(t, int64(500), got.ActualScanBytes, "bytes-scanned-so-far recorded (QRY-FR-045)")

	envs, _ := f.mem.OutboxEventsByType(context.Background(), f.tenant, events.EvExecutionCancelled)
	assert.Len(t, envs, 1)

	// terminal cancel → 409 (BRD §4.4)
	_, err = f.broker.Cancel(context.Background(), f.op, e.ID)
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeConflict, de.Code)
}

// Queued cancel: the run never starts.
func TestBrokerCancelQueued(t *testing.T) {
	f := newFixture(t)
	f.duck.block = make(chan struct{})
	one := 1
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), f.op, &domain.TenantLimits{ConcurrentSlots: &one}))

	e1, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	op2 := f.op
	op2.UserID = "u2"
	req2 := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	req2.Op = op2
	e2, err := f.broker.Run(context.Background(), req2)
	require.NoError(t, err)
	require.Equal(t, domain.StatusQueued, e2.Status)
	require.NotNil(t, e2.QueuePosition)
	assert.Equal(t, 1, *e2.QueuePosition)

	got, err := f.broker.Cancel(context.Background(), op2, e2.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.StatusCancelled, got.Status)
	assert.Equal(t, 0, f.duck.Calls(), "engine untouched while blocked run holds the slot")
	close(f.duck.block)
	f.waitTerminal(t, e1.ID)
}

// AC-10 (broker tier): identical run within TTL is a cache hit — no engine
// contact; a new dataset version misses.
func TestBrokerResultCache(t *testing.T) {
	f := newFixture(t)
	req := f.runReq("SELECT region FROM {{dataset('Orders')}} WHERE region = :r")
	req.Decls = []domain.VariableDecl{{Name: "r", Type: domain.VarString}}
	req.Values = map[string]json.RawMessage{"r": json.RawMessage(`"EMEA"`)}
	req.UseCache = true

	e1, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	first := f.waitTerminal(t, e1.ID)
	require.Equal(t, domain.StatusSucceeded, first.Status)
	require.Equal(t, 1, f.duck.Calls())

	e2, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	second := f.waitTerminal(t, e2.ID)
	assert.Equal(t, domain.StatusSucceeded, second.Status)
	assert.True(t, second.CacheHit, "cache_hit=true in history (AC-10)")
	assert.Equal(t, 1, f.duck.Calls(), "no engine contact on the hit")
	assert.Equal(t, first.ResultURI, second.ResultURI, "identical results reused")

	// Different parameters miss.
	req3 := req
	req3.Values = map[string]json.RawMessage{"r": json.RawMessage(`"AMER"`)}
	e3, err := f.broker.Run(context.Background(), req3)
	require.NoError(t, err)
	third := f.waitTerminal(t, e3.ID)
	assert.False(t, third.CacheHit)
	assert.Equal(t, 2, f.duck.Calls())

	// New dataset version → miss (key pins versions).
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "Orders", Version: 4, URN: "urn:orders", PhysicalIdent: `"bronze_t"."orders_v4"`,
		Namespace: "bronze_t", SizeBytes: 400 << 20,
		Columns: []datasets.Column{{Name: "region", Type: "string"}},
	}, true)
	e4, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	fourth := f.waitTerminal(t, e4.ID)
	assert.False(t, fourth.CacheHit, "version bump invalidates by key")
	assert.Equal(t, 3, f.duck.Calls())

	// ?cache=false bypasses.
	req5 := req
	req5.UseCache = false
	e5, err := f.broker.Run(context.Background(), req5)
	require.NoError(t, err)
	fifth := f.waitTerminal(t, e5.ID)
	assert.False(t, fifth.CacheHit)
}

// AC-14 (unit tier): a parameter compared against a pii-tagged column is
// stored redacted; non-PII parameters persist in clear.
func TestBrokerPIIRedaction(t *testing.T) {
	f := newFixture(t)
	req := f.runReq("SELECT region FROM {{dataset('Orders')}} WHERE email = :email AND region = :region")
	req.Decls = []domain.VariableDecl{
		{Name: "email", Type: domain.VarString},
		{Name: "region", Type: domain.VarString},
	}
	req.Values = map[string]json.RawMessage{
		"email":  json.RawMessage(`"person@example.com"`),
		"region": json.RawMessage(`"EMEA"`),
	}
	e, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	assert.Equal(t, "«redacted»", final.BoundParams["email"], "PII param redacted (BR-12)")
	assert.Equal(t, "EMEA", final.BoundParams["region"], "non-PII param in clear")
	// The engine still received the REAL value — redaction is history-only.
	assert.Contains(t, f.duck.LastArgs(), "person@example.com")
}

// BR-4: deprecated dataset still runs with a warning.
func TestBrokerDeprecatedDatasetWarning(t *testing.T) {
	f := newFixture(t)
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "Old", Version: 1, URN: "urn:old", PhysicalIdent: `"bronze_t"."old"`,
		Namespace: "bronze_t", SizeBytes: 1 << 20, Deprecated: true,
	}, true)
	e, err := f.broker.Run(context.Background(), f.runReq("SELECT count(*) FROM {{dataset('Old')}}"))
	require.NoError(t, err)
	final := f.waitTerminal(t, e.ID)
	assert.Equal(t, domain.StatusSucceeded, final.Status)
	assert.Contains(t, final.Warnings, WarnDatasetDeprecated)
}

// Deleted dataset → DATASET_NOT_FOUND (BR-4: V1 silently broke).
func TestBrokerDeletedDataset(t *testing.T) {
	f := newFixture(t)
	_, err := f.broker.Run(context.Background(), f.runReq("SELECT 1 FROM {{dataset('Ghost')}}"))
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeDatasetNotFound, de.Code)
}

// Sync mode: refuses large plans and refuses to queue (BR-5).
func TestBrokerSyncMode(t *testing.T) {
	f := newFixture(t)

	// Small plan, free slot → runs inline.
	req := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	f.resolver.Put(f.tenant, datasets.Meta{
		Name: "Tiny", Version: 1, URN: "urn:tiny", PhysicalIdent: `"bronze_t"."tiny"`,
		Namespace: "bronze_t", SizeBytes: 1 << 20,
	}, true)
	req = f.runReq("SELECT count(*) FROM {{dataset('Tiny')}}")
	req.Mode = "sync"
	req.Async = false
	e, err := f.broker.Run(context.Background(), req)
	require.NoError(t, err)
	assert.Equal(t, domain.StatusSucceeded, e.Status, "sync returns terminal state")

	// Plan too large for sync (>10MB estimate) → USE_ASYNC.
	reqBig := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	reqBig.Mode = "sync"
	reqBig.Async = false
	_, err = f.broker.Run(context.Background(), reqBig)
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeUseAsync, de.Code)
	assert.Equal(t, 409, de.HTTP)

	// No instant slot → USE_ASYNC (BR-5: sync never queues).
	f.duck.block = make(chan struct{})
	one := 1
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), f.op, &domain.TenantLimits{ConcurrentSlots: &one}))
	bg, err := f.broker.Run(context.Background(), f.runReq("SELECT count(*) FROM {{dataset('Tiny')}}"))
	require.NoError(t, err)
	reqSync := f.runReq("SELECT count(*) FROM {{dataset('Tiny')}}")
	reqSync.Mode = "sync"
	reqSync.Async = false
	op2 := f.op
	op2.UserID = "u2"
	reqSync.Op = op2
	_, err = f.broker.Run(context.Background(), reqSync)
	require.Error(t, err)
	de, _ = domain.AsError(err)
	assert.Equal(t, domain.CodeUseAsync, de.Code)
	close(f.duck.block)
	f.waitTerminal(t, bg.ID)
}

// Queue overflow → 429 RATE_LIMITED (QRY-FR-044).
func TestBrokerQueueOverflow429(t *testing.T) {
	f := newFixture(t)
	f.duck.block = make(chan struct{})
	t.Cleanup(func() {
		close(f.duck.block)
		f.broker.Wait() // let promoted runs drain before TempDir cleanup
	})
	one := 1
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), f.op, &domain.TenantLimits{ConcurrentSlots: &one}))

	// Occupy the slot, then fill the 50-deep queue.
	_, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	for i := 0; i < domain.MaxQueueDepth; i++ {
		op := f.op
		op.UserID = uuid.NewString()
		req := f.runReq("SELECT region FROM {{dataset('Orders')}}")
		req.Op = op
		_, err := f.broker.Run(context.Background(), req)
		require.NoError(t, err, "queued run %d", i)
	}
	op := f.op
	op.UserID = "overflow-user"
	req := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	req.Op = op
	_, err = f.broker.Run(context.Background(), req)
	require.Error(t, err)
	de, _ := domain.AsError(err)
	assert.Equal(t, domain.CodeRateLimited, de.Code)
	assert.Equal(t, 429, de.HTTP)
}

// tenant.suspended: cancel queued+running, block new (§6).
func TestBrokerTenantSuspension(t *testing.T) {
	f := newFixture(t)
	f.duck.block = make(chan struct{})
	one := 1
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), f.op, &domain.TenantLimits{ConcurrentSlots: &one}))

	running, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	op2 := f.op
	op2.UserID = "u2"
	req2 := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	req2.Op = op2
	queued, err := f.broker.Run(context.Background(), req2)
	require.NoError(t, err)
	require.Equal(t, domain.StatusQueued, queued.Status)

	f.broker.SuspendTenant(context.Background(), f.tenant)

	assert.Equal(t, domain.StatusCancelled, f.waitTerminal(t, running.ID).Status)
	assert.Equal(t, domain.StatusCancelled, f.waitTerminal(t, queued.ID).Status)

	_, err = f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.Error(t, err, "new executions blocked while suspended")

	f.broker.ResumeTenant(f.tenant)
	f.duck.block = nil
	e, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	f.waitTerminal(t, e.ID)
}

// dataset.deleted: queued executions referencing the URN fail with
// DATASET_NOT_FOUND (§6).
func TestBrokerDatasetDeletedFailsQueued(t *testing.T) {
	f := newFixture(t)
	f.duck.block = make(chan struct{})
	t.Cleanup(func() {
		close(f.duck.block)
		f.broker.Wait()
	})
	one := 1
	require.NoError(t, f.mem.PutTenantLimits(context.Background(), f.op, &domain.TenantLimits{ConcurrentSlots: &one}))

	_, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	op2 := f.op
	op2.UserID = "u2"
	req2 := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	req2.Op = op2
	queued, err := f.broker.Run(context.Background(), req2)
	require.NoError(t, err)
	require.Equal(t, domain.StatusQueued, queued.Status)

	consumer := &events.Consumer{Broker: f.broker, Resolver: f.resolver}
	env := events.Envelope{
		EventID: domain.NewID(), EventType: "dataset.deleted", TenantID: f.tenant,
		ResourceURN: "wr:" + f.tenant.String() + ":dataset:dataset/orders",
		Payload:     map[string]any{"name": "Orders"},
	}
	consumer.Handle(context.Background(), env)
	consumer.Handle(context.Background(), env) // replay-safe (MASTER-FR-032)

	final := f.waitTerminal(t, queued.ID)
	assert.Equal(t, domain.StatusFailed, final.Status)
	assert.Equal(t, domain.CodeDatasetNotFound, final.Error.Code)
}

// DryRun returns the plan and records history (QRY-FR-041/080).
func TestBrokerDryRun(t *testing.T) {
	f := newFixture(t)
	req := f.runReq("SELECT region FROM {{dataset('Orders')}}")
	plan, err := f.broker.DryRun(context.Background(), req)
	require.NoError(t, err)
	assert.Equal(t, engine.NameDuckDB, plan.Route.Engine)
	assert.EqualValues(t, 400<<20, plan.Estimate.ScanBytes)
	assert.Equal(t, "ok", plan.CeilingVerdict)
	assert.Equal(t, 0, f.duck.Calls(), "dry-run never touches an engine")

	page, err := f.mem.ListExecutions(context.Background(), f.tenant, store.ExecutionFilter{})
	require.NoError(t, err)
	require.Len(t, page.Data, 1)
	assert.Contains(t, page.Data[0].Warnings, WarnDryRun, "dry-run recorded in history")
}

// Statement safety verdicts surface as 403 rejections and are recorded.
func TestBrokerRejectsUnsafeStatements(t *testing.T) {
	f := newFixture(t)
	for _, sql := range []string{
		"DELETE FROM {{dataset('Orders')}}",
		"SELECT 1; DELETE FROM t",
		"WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
		"SELECT * FROM other_tenant_schema.secrets",
	} {
		_, err := f.broker.Run(context.Background(), f.runReq(sql))
		require.Error(t, err, sql)
		de, ok := domain.AsError(err)
		require.True(t, ok, sql)
		assert.Equal(t, domain.CodeStatementNotAllowed, de.Code, sql)
	}
	page, err := f.mem.ListExecutions(context.Background(), f.tenant, store.ExecutionFilter{Status: domain.StatusRejected})
	require.NoError(t, err)
	assert.Len(t, page.Data, 4, "every rejection recorded")
}

// Outbox relay drains events to the publisher (MASTER-FR-034).
func TestBrokerOutboxRelay(t *testing.T) {
	f := newFixture(t)
	e, err := f.broker.Run(context.Background(), f.runReq("SELECT region FROM {{dataset('Orders')}}"))
	require.NoError(t, err)
	f.waitTerminal(t, e.ID)

	pub := events.NewInMemory()
	relay := &events.Relay{Source: f.mem, Publisher: pub}
	require.NoError(t, relay.Drain(context.Background()))
	assert.NotEmpty(t, pub.ByType(events.EvExecutionStarted))
	assert.NotEmpty(t, pub.ByType(events.EvExecutionSucceeded))
	// Drained rows are marked published: second drain publishes nothing new.
	before := len(pub.All())
	require.NoError(t, relay.Drain(context.Background()))
	assert.Equal(t, before, len(pub.All()))
}
