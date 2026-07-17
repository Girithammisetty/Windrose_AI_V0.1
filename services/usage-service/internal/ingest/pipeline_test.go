package ingest

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/usage-service/internal/domain"
	"github.com/windrose-ai/usage-service/internal/metrics"
)

// fakeRaw is a unit-test double (allowed only in *_test.go).
type fakeRaw struct{ recs []domain.MeterRecord }

func (f *fakeRaw) InsertRaw(_ context.Context, recs []domain.MeterRecord) (int, error) {
	f.recs = append(f.recs, recs...)
	return len(recs), nil
}

type fakeBudgets struct{ calls int }

func (f *fakeBudgets) EvaluateAfterIngest(context.Context, uuid.UUID, map[string]bool) error {
	f.calls++
	return nil
}

func TestCatalogValid(t *testing.T) {
	if err := ValidateCatalog(Catalog()); err != nil {
		t.Fatal(err)
	}
}

func TestTokenUsageMapping(t *testing.T) {
	raw := &fakeRaw{}
	bud := &fakeBudgets{}
	p := NewPipeline(Catalog(), raw, bud, nil)

	tenant := uuid.New()
	env := gcevent.Envelope{
		EventID: uuid.New(), EventType: "ai.token_usage.v1", TenantID: tenant,
		OccurredAt: time.Now().UTC(),
		Payload: map[string]any{
			"workspace_id": "ws-7", "principal": "u-1", "agent_id": "a-9", "model_alias": "qwen2.5",
			"input_tokens": float64(1200), "output_tokens": float64(800),
		},
	}
	if err := p.Handle(context.Background(), env); err != nil {
		t.Fatal(err)
	}
	if len(raw.recs) != 2 {
		t.Fatalf("want 2 records, got %d", len(raw.recs))
	}
	byMeter := map[string]domain.MeterRecord{}
	for _, r := range raw.recs {
		byMeter[r.MeterKey] = r
	}
	in := byMeter[domain.MeterLLMInputTokens]
	if in.Quantity != 1200 || in.WorkspaceID == nil || *in.WorkspaceID != "ws-7" || in.Model == nil || *in.Model != "qwen2.5" {
		t.Fatalf("bad input-token record: %+v", in)
	}
	if byMeter[domain.MeterLLMOutputTokens].Quantity != 800 {
		t.Fatalf("bad output-token qty")
	}
	if bud.calls != 1 {
		t.Fatalf("budget evaluation should run once, got %d", bud.calls)
	}
}

func TestAgentRunFilterAndUnmapped(t *testing.T) {
	raw := &fakeRaw{}
	p := NewPipeline(Catalog(), raw, nil, nil)

	// Failed agent run → filtered out (status != succeeded).
	failed := gcevent.Envelope{EventID: uuid.New(), EventType: "agent_run.completed", TenantID: uuid.New(),
		OccurredAt: time.Now().UTC(), Payload: map[string]any{"status": "failed"}}
	_ = p.Handle(context.Background(), failed)
	if len(raw.recs) != 0 {
		t.Fatalf("failed run must not meter, got %d", len(raw.recs))
	}

	// Succeeded → one agent_tasks_completed record.
	ok := gcevent.Envelope{EventID: uuid.New(), EventType: "agent_run.completed", TenantID: uuid.New(),
		OccurredAt: time.Now().UTC(), Payload: map[string]any{"status": "succeeded"}}
	_ = p.Handle(context.Background(), ok)
	if len(raw.recs) != 1 || raw.recs[0].MeterKey != domain.MeterAgentTasksCompleted {
		t.Fatalf("expected one agent_tasks_completed, got %+v", raw.recs)
	}

	// Unknown event_type → unmapped, no records, no error.
	unk := gcevent.Envelope{EventID: uuid.New(), EventType: "something.else", TenantID: uuid.New(),
		OccurredAt: time.Now().UTC(), Payload: map[string]any{}}
	if err := p.Handle(context.Background(), unk); err != nil {
		t.Fatal(err)
	}
	if len(raw.recs) != 1 {
		t.Fatalf("unmapped event should add no records")
	}
}

// TestAC15_UnmappedMetricIncrements: an event with an unknown event_type on a
// consumed topic increments usage_unmapped_events_total and does not error or
// meter (AC-15).
func TestAC15_UnmappedMetricIncrements(t *testing.T) {
	m := metrics.New(prometheus.NewRegistry())
	raw := &fakeRaw{}
	p := NewPipeline(Catalog(), raw, nil, m)

	unk := gcevent.Envelope{EventID: uuid.New(), EventType: "brand.new_event", TenantID: uuid.New(),
		OccurredAt: time.Now().UTC(), Payload: map[string]any{"foo": "bar"}}
	if err := p.Handle(context.Background(), unk); err != nil {
		t.Fatal(err)
	}
	if got := testutil.ToFloat64(m.Unmapped.WithLabelValues("brand.new_event")); got != 1 {
		t.Fatalf("usage_unmapped_events_total = %v, want 1", got)
	}
	if len(raw.recs) != 0 {
		t.Fatalf("unmapped event must not meter")
	}
}

// TestAC02b_DeterministicTimeReplay: an event MISSING occurred_at gets a
// deterministic record time from its uuidv7 id, so a replay collides on the raw
// unique key even without Redis dedup (LOW fix).
func TestAC02b_DeterministicTimeReplay(t *testing.T) {
	a := eventTime(time.Time{}, mustV7())
	// same id → same derived time.
	id := mustV7()
	if !eventTime(time.Time{}, id).Equal(eventTime(time.Time{}, id)) {
		t.Fatal("derived time not deterministic for a fixed event id")
	}
	if a.IsZero() {
		t.Fatal("derived time should not be zero for a uuidv7 id")
	}
}

func mustV7() uuid.UUID {
	id, err := uuid.NewV7()
	if err != nil {
		panic(err)
	}
	return id
}
