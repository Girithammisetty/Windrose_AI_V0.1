package fanout

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/windrose-ai/realtime-hub/internal/metrics"
)

// memBus is an in-memory Bus double (unit tests only, never wired into
// cmd/server): Publish delivers synchronously to local subscribers via the
// hub's OnBusMessage callback, standing in for real Redis pub/sub.
type memBus struct {
	onMsg func(tenant, topic string, ev Event)
}

func (b *memBus) Publish(_ context.Context, tenant, topic string, ev Event) error {
	b.onMsg(tenant, topic, ev)
	return nil
}
func (b *memBus) Subscribe(string, string)   {}
func (b *memBus) Unsubscribe(string, string) {}

type capturedEvent struct {
	id, event, data string
}

// fakeSink records wire writes. When gate is non-nil, WriteEvent blocks until
// the gate is closed, letting a test fill the send buffer to force overflow.
type fakeSink struct {
	mu      sync.Mutex
	events  []capturedEvent
	hbs     int
	closed  bool
	closeCd int
	gate    chan struct{}
	entered chan struct{} // signals the writer parked on the gate (first write)
}

func (s *fakeSink) WriteEvent(id, event string, data []byte) error {
	if s.gate != nil {
		if s.entered != nil {
			select {
			case s.entered <- struct{}{}:
			default:
			}
		}
		<-s.gate
	}
	s.mu.Lock()
	s.events = append(s.events, capturedEvent{id, event, string(data)})
	s.mu.Unlock()
	return nil
}
func (s *fakeSink) WriteHeartbeat(bool) error {
	s.mu.Lock()
	s.hbs++
	s.mu.Unlock()
	return nil
}
func (s *fakeSink) Close(code int, _ string) error {
	s.mu.Lock()
	s.closed = true
	s.closeCd = code
	s.mu.Unlock()
	return nil
}
func (s *fakeSink) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.events)
}
func (s *fakeSink) countID(id string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, e := range s.events {
		if e.id == id {
			n++
		}
	}
	return n
}
func (s *fakeSink) controls(ctrlType string) []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []map[string]any
	for _, e := range s.events {
		if e.event != "control" {
			continue
		}
		var m map[string]any
		if json.Unmarshal([]byte(e.data), &m) == nil && m["type"] == ctrlType {
			out = append(out, m)
		}
	}
	return out
}
func (s *fakeSink) closeCode() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.closeCd
}

func newTestHub() (*Hub, *memBus) {
	m := metrics.New(prometheus.NewRegistry())
	hub := NewHub(HubConfig{PodID: "p1", Metrics: m})
	bus := &memBus{}
	hub.SetBus(bus)
	bus.onMsg = hub.OnBusMessage
	return hub, bus
}

func waitFor(cond func() bool, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return true
		}
		time.Sleep(2 * time.Millisecond)
	}
	return cond()
}

// TestAC07_BackpressureGapAndIsolation: a slow reader on run-status overflows,
// oldest events drop with a `gap` control carrying the dropped id range, and a
// second (fast) connection is unaffected.
func TestAC07_BackpressureGapAndIsolation(t *testing.T) {
	hub, _ := newTestHub()
	ctx := context.Background()
	topic := "run-status:wr:t1:svc:res/1"
	urn := "wr:t1:svc:res/1"

	slow := &fakeSink{gate: make(chan struct{}), entered: make(chan struct{}, 1)}
	cs := hub.AddConn("slow", "u1", "t1", "user", nil, "sse", "", slow, time.Time{})
	hub.Subscribe(ctx, cs, "t1", topic, urn, "")
	// Park the slow writer on its first write before flooding, so the send
	// buffer genuinely overflows rather than draining concurrently.
	<-slow.entered

	fast := &fakeSink{}
	cf := hub.AddConn("fast", "u2", "t1", "user", nil, "sse", "", fast, time.Time{})
	hub.Subscribe(ctx, cf, "t1", topic, urn, "")

	total := MaxQueueLen + 50
	for i := 0; i < total; i++ {
		id := fmt.Sprintf("evt-%05d", i)
		_ = hub.IngestInternal(ctx, "t1", topic, Event{ID: id, Topic: topic, Data: []byte(`{"n":1}`)}, 0)
	}

	// Fast connection receives (nearly) everything with no gap.
	if !waitFor(func() bool { return fast.count() >= total-1 }, 2*time.Second) {
		t.Fatalf("fast conn only got %d/%d events (slow client leaked backpressure)", fast.count(), total)
	}
	if gs := fast.controls(CtrlGap); len(gs) != 0 {
		t.Fatalf("fast conn should not gap, got %v", gs)
	}

	// Release the slow reader; it must surface a gap for the dropped range.
	close(slow.gate)
	if !waitFor(func() bool { return len(slow.controls(CtrlGap)) > 0 }, 2*time.Second) {
		t.Fatal("slow conn never received a gap control event")
	}
	gap := slow.controls(CtrlGap)[0]
	if gap["topic"] != topic || gap["from_id"] == "" {
		t.Fatalf("gap control malformed: %v", gap)
	}
}

// TestAC07_ChatDisconnectsOnOverflow: chat QoS closes 4409 instead of gapping.
func TestAC07_ChatDisconnectsOnOverflow(t *testing.T) {
	hub, _ := newTestHub()
	ctx := context.Background()
	topic := "chat:sess-1"

	sink := &fakeSink{gate: make(chan struct{}), entered: make(chan struct{}, 1)}
	c := hub.AddConn("c1", "u1", "t1", "user", nil, "sse", "", sink, time.Time{})
	hub.Subscribe(ctx, c, "t1", topic, "", "")
	<-sink.entered // park the writer before flooding

	for i := 0; i < MaxQueueLen+5; i++ {
		id := fmt.Sprintf("tok-%05d", i)
		_ = hub.IngestInternal(ctx, "t1", topic, Event{ID: id, Topic: topic, Data: []byte(`"x"`), Chat: true}, 0)
	}
	close(sink.gate)
	if !waitFor(func() bool { return sink.closeCode() == CloseTooSlow }, 2*time.Second) {
		t.Fatalf("chat overflow should close 4409, got close code %d", sink.closeCode())
	}
}

// TestAC16_ExactlyOnceDedup: the same event_id arriving twice (producer retry
// via a second source) is delivered to the client exactly once.
func TestAC16_ExactlyOnceDedup(t *testing.T) {
	hub, _ := newTestHub()
	ctx := context.Background()
	topic := "run-status:wr:t1:svc:res/9"
	urn := "wr:t1:svc:res/9"
	sink := &fakeSink{}
	c := hub.AddConn("c1", "u1", "t1", "user", nil, "sse", "", sink, time.Time{})
	hub.Subscribe(ctx, c, "t1", topic, urn, "")

	ev := Event{ID: "dup-1", Topic: topic, Data: []byte(`{"a":1}`)}
	_ = hub.IngestInternal(ctx, "t1", topic, ev, 0)
	_ = hub.IngestInternal(ctx, "t1", topic, ev, 0)

	if !waitFor(func() bool { return sink.countID("dup-1") >= 1 }, time.Second) {
		t.Fatal("event never delivered")
	}
	time.Sleep(50 * time.Millisecond)
	if n := sink.countID("dup-1"); n != 1 {
		t.Fatalf("dedup failed: event delivered %d times", n)
	}
}

// TestRevocation terminates only the affected topic (AC-6 logic, sans Kafka).
func TestRevocation_TerminatesAffectedTopicOnly(t *testing.T) {
	hub, _ := newTestHub()
	ctx := context.Background()
	sink := &fakeSink{}
	c := hub.AddConn("c1", "u1", "t1", "user", nil, "sse", "", sink, time.Time{})
	revoked := "run-status:wr:t1:svc:res/A"
	kept := "run-status:wr:t1:svc:res/B"
	hub.Subscribe(ctx, c, "t1", revoked, "wr:t1:svc:res/A", "")
	hub.Subscribe(ctx, c, "t1", kept, "wr:t1:svc:res/B", "")

	hub.Revoke("t1", "wr:t1:svc:res/A")
	if !waitFor(func() bool { return len(sink.controls(CtrlRevoked)) == 1 }, time.Second) {
		t.Fatal("no revoked control event")
	}
	if c.hasTopic(revoked) {
		t.Fatal("revoked topic still subscribed")
	}
	if !c.hasTopic(kept) {
		t.Fatal("unaffected topic was wrongly dropped")
	}
	// Delivery on the kept topic continues.
	_ = hub.IngestInternal(ctx, "t1", kept, Event{ID: "k1", Topic: kept, Data: []byte(`{}`)}, 0)
	if !waitFor(func() bool { return sink.countID("k1") == 1 }, time.Second) {
		t.Fatal("kept topic stopped delivering after revocation")
	}
}

// TestRace_ConcurrentFanout exercises concurrent enqueue + subscribe/unsubscribe
// (run with -race).
func TestRace_ConcurrentFanout(t *testing.T) {
	hub, _ := newTestHub()
	ctx := context.Background()
	topic := "run-status:wr:t1:svc:res/R"
	urn := "wr:t1:svc:res/R"

	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			sink := &fakeSink{}
			c := hub.AddConn(fmt.Sprintf("c-%d", g), fmt.Sprintf("u-%d", g), "t1", "user", nil, "sse", "", sink, time.Time{})
			for i := 0; i < 50; i++ {
				hub.Subscribe(ctx, c, "t1", topic, urn, "")
				_ = hub.IngestInternal(ctx, "t1", topic, Event{ID: fmt.Sprintf("%d-%d", g, i), Topic: topic, Data: []byte(`{}`)}, 0)
				if i%7 == 0 {
					hub.Unsubscribe(c, "t1", topic)
				}
			}
			c.Close(0, "done")
		}(g)
	}
	wg.Wait()
}

func TestDrain_SendsReconnectThenCloses(t *testing.T) {
	hub, _ := newTestHub()
	sink := &fakeSink{}
	_ = hub.AddConn("c1", "u1", "t1", "user", nil, "sse", "", sink, time.Time{})
	hub.Drain(250, 10*time.Millisecond)
	if !waitFor(func() bool { return len(sink.controls(CtrlReconnect)) == 1 }, time.Second) {
		t.Fatal("drain did not send reconnect control")
	}
	rc := sink.controls(CtrlReconnect)[0]
	if !strings.Contains(fmt.Sprint(rc["after_ms"]), "250") {
		t.Fatalf("reconnect after_ms wrong: %v", rc)
	}
}
