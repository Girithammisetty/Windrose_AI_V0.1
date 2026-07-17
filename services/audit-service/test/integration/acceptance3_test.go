package integration

import (
	"context"
	"fmt"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/chain"
	"github.com/windrose-ai/audit-service/internal/chstore"
	"github.com/windrose-ai/audit-service/internal/domain"
	"github.com/windrose-ai/audit-service/internal/ingest"
)

// flakyInserter wraps the real ClickHouse store and fails the FIRST insert of
// each event_id (a transient outage, BR-6), succeeding on retry. UNIT-TEST
// DOUBLE ONLY — never wired into cmd/server.
type flakyInserter struct {
	real   *chstore.Store
	mu     sync.Mutex
	seen   map[uuid.UUID]bool
}

func (f *flakyInserter) Insert(ctx context.Context, r domain.Record) error {
	f.mu.Lock()
	first := !f.seen[r.EventID]
	f.seen[r.EventID] = true
	f.mu.Unlock()
	if first {
		return fmt.Errorf("transient clickhouse outage")
	}
	return f.real.Insert(ctx, r)
}

// TestAC11_TransientClickHouseNoGap: a transient ClickHouse insert failure that
// forces a retry must NOT advance the chain to a new seq (HIGH-1). The retried
// event reuses its assigned seq; the chain stays contiguous and verifies valid
// (AC-11, BR-6).
func TestAC11_TransientClickHouseNoGap(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	flaky := &flakyInserter{real: h.ch, seen: map[uuid.UUID]bool{}}
	proc := &ingest.Processor{CH: flaky, Chain: h.chain}

	for i := 0; i < 6; i++ {
		e := domain.Envelope{
			EventID: uuid.New(), EventType: "case.assigned", TenantID: tenant,
			Actor: domain.Actor{Type: "user", ID: fmt.Sprintf("u-%d", i)},
			ResourceURN: "wr:" + tenant.String() + ":case:case/c-" + fmt.Sprint(i),
			OccurredAt: time.Now().UTC().Add(time.Duration(i) * time.Millisecond),
			Payload: map[string]any{"i": i},
		}
		// Retry loop mirroring the consumer's transient handling.
		for attempt := 0; attempt < 5; attempt++ {
			err := proc.Handle(context.Background(), ingest.Source{Topic: "case.events.v1"}, e)
			if err == nil {
				break
			}
			if attempt == 4 {
				t.Fatalf("event %d never ingested: %v", i, err)
			}
		}
	}

	rows, err := h.ch.ChainScan(context.Background(), tenant, h.today)
	if err != nil {
		t.Fatalf("chain scan: %v", err)
	}
	if len(rows) != 6 {
		t.Fatalf("expected 6 stored rows (no phantom gap), got %d", len(rows))
	}
	for i, r := range rows {
		if r.ChainSeq != uint64(i+1) {
			t.Fatalf("chain gap after transient failure: row %d has seq %d (want %d)", i, r.ChainSeq, i+1)
		}
	}
	if res := chain.Verify(rows, tenant, h.today, ""); !res.Valid {
		t.Fatalf("chain invalid after transient-failure retries: %+v", res)
	}
}

// TestAC11b_ConcurrentAppendSingleWriter: concurrent ingest of the same tenant's
// events across goroutines (simulating different partitions/replicas of the
// multi-topic ingest group) produces a correctly-ordered, gap-free, valid chain
// — the distributed single-writer lock holds (HIGH-2, BR-10).
func TestAC11b_ConcurrentAppendSingleWriter(t *testing.T) {
	h := newHarness(t)
	tenant := uuid.New()
	proc := &ingest.Processor{CH: h.ch, Chain: h.chain}

	const writers, per = 4, 15
	var wg sync.WaitGroup
	errCh := make(chan error, writers*per)
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for j := 0; j < per; j++ {
				e := domain.Envelope{
					EventID: uuid.New(), EventType: "case.assigned", TenantID: tenant,
					Actor: domain.Actor{Type: "user", ID: fmt.Sprintf("u-%d-%d", w, j)},
					ResourceURN: "wr:" + tenant.String() + ":case:case/c",
					OccurredAt: time.Now().UTC(), Payload: map[string]any{"w": w, "j": j},
				}
				if err := proc.Handle(context.Background(), ingest.Source{Topic: "case.events.v1"}, e); err != nil {
					errCh <- err
				}
			}
		}(w)
	}
	wg.Wait()
	close(errCh)
	for err := range errCh {
		t.Fatalf("concurrent ingest error: %v", err)
	}

	rows, err := h.ch.ChainScan(context.Background(), tenant, h.today)
	if err != nil {
		t.Fatalf("chain scan: %v", err)
	}
	if len(rows) != writers*per {
		t.Fatalf("expected %d rows, got %d", writers*per, len(rows))
	}
	for i, r := range rows {
		if r.ChainSeq != uint64(i+1) {
			t.Fatalf("concurrent chain not contiguous at %d: seq %d", i, r.ChainSeq)
		}
	}
	if res := chain.Verify(rows, tenant, h.today, ""); !res.Valid {
		t.Fatalf("concurrent chain invalid: %+v", res)
	}
}

// TestAC02_ReplayIdempotent: the same event replayed through the real consumer
// yields exactly one stored row and one chain position (AUD-FR-004, AC-2).
func TestAC02_ReplayIdempotent(t *testing.T) {
	h := newHarness(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go h.consumer.Run(ctx)

	tenant := uuid.New()
	e := domain.Envelope{
		EventID: uuid.New(), EventType: "dataset.created", TenantID: tenant,
		Actor: domain.Actor{Type: "user", ID: "u-1"},
		ResourceURN: "wr:" + tenant.String() + ":dataset:dataset/ds-1",
		OccurredAt: time.Now().UTC(), TraceID: uuid.NewString(), Payload: map[string]any{"n": 1},
	}
	// Publish the SAME event twice.
	h.produce(t, "dataset.events.v1", e)
	h.produce(t, "dataset.events.v1", e)

	// Wait until it lands, then confirm exactly one row / one chain position.
	deadline := time.Now().Add(40 * time.Second)
	for time.Now().Before(deadline) {
		if r, err := h.ch.GetEvent(context.Background(), tenant, e.EventID); err == nil && r != nil {
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	time.Sleep(2 * time.Second) // allow any duplicate to (not) be processed
	rows, err := h.ch.ChainScan(context.Background(), tenant, h.today)
	if err != nil {
		t.Fatalf("chain scan: %v", err)
	}
	count := 0
	for _, r := range rows {
		if r.EventID == e.EventID {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("replay not idempotent: event appears %d times in the chain", count)
	}
}

// TestAC10_AuditorsAreAudited: a search emits an audit.searched meta event naming
// the actor (AUD-FR-032, AC-10).
func TestAC10_AuditorsAreAudited(t *testing.T) {
	h := newHarness(t)
	h.seedCatalog(t)
	tenant := uuid.New()
	admin := "auditor-" + uuid.NewString()[:8]
	h.seedAdmin(t, tenant.String(), admin)
	tok := h.token(admin, tenant.String(), "user", nil)

	from := time.Now().UTC().Add(-time.Hour).Format(time.RFC3339)
	to := time.Now().UTC().Add(time.Hour).Format(time.RFC3339)
	status, body := h.do(t, http.MethodGet, fmt.Sprintf("/api/v1/audit/search?from=%s&to=%s", from, to), tok, nil)
	if status != 200 {
		t.Fatalf("search status %d: %s", status, body)
	}
	if !h.waitForMeta(t, "audit.searched", admin, 30*time.Second) {
		t.Fatal("no audit.searched meta event emitted for the search (auditors must be audited)")
	}
}

// TestBR9_VerifyUnsealedConflict: verifying an unsealed day returns CONFLICT
// (BR-9, §5), not a 200 that could false-alarm on an open chain.
func TestBR9_VerifyUnsealedConflict(t *testing.T) {
	h := newHarness(t)
	h.seedCatalog(t)
	tenant := uuid.New()
	admin := "sec-admin"
	h.seedAdmin(t, tenant.String(), admin)
	tok := h.token(admin, tenant.String(), "user", nil)

	h.ingestDirect(t, domain.Envelope{
		EventID: uuid.New(), EventType: "case.assigned", TenantID: tenant,
		Actor: domain.Actor{Type: "user", ID: "u-1"}, OccurredAt: time.Now().UTC(),
		ResourceURN: "wr:" + tenant.String() + ":case:case/c", Payload: map[string]any{"x": 1},
	}, "case.events.v1")

	status, body := h.do(t, http.MethodPost, "/api/v1/audit/verify", tok,
		map[string]string{"tenant_id": tenant.String(), "date": h.today})
	if status != http.StatusConflict {
		t.Fatalf("unsealed-day verify must be 409 CONFLICT, got %d: %s", status, body)
	}
}
