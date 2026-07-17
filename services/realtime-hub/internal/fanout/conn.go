package fanout

import (
	"encoding/json"
	"sync"
	"time"
)

// Buffer bounds per connection (RTH-FR-030).
const (
	MaxQueueLen   = 256
	MaxQueueBytes = 1 << 20 // 1MB
	dedupPerTopic = 2000    // bounded per-topic written-id set (BR-6)
)

// Timing knobs (RTH-FR-033/010). Vars, not consts, so tests can shrink them;
// production values are the BRD defaults.
var (
	// HeartbeatInterval is the keepalive cadence (RTH-FR-033: 15s).
	HeartbeatInterval = 15 * time.Second
	// TokenWarnBefore is how far before exp the token_refresh control is sent.
	TokenWarnBefore = 60 * time.Second
	// TokenGraceAfter is how long past exp an unrefreshed connection survives
	// before closing 4401 (RTH-FR-010: 120s).
	TokenGraceAfter = 120 * time.Second
)

// gapRange accumulates a dropped-event id range for one topic (RTH-FR-030).
type gapRange struct{ from, to string }

// Conn is one live client connection (SSE or WS). It owns a single writer
// goroutine; the fan-out loop only enqueues (non-blocking, BR-1) so one slow
// client can never add latency to another.
type Conn struct {
	ID        string
	Subject   string
	Tenant    string
	Typ       string   // JWT typ (for revocation re-evaluation, RTH-FR-013)
	Scopes    []string // JWT scopes (for revocation re-evaluation)
	Transport string   // "sse" | "ws"
	IPHash    string

	hub  *Hub
	sink Sink

	mu       sync.Mutex
	topics   map[string]struct{} // raw topic strings currently subscribed
	dataQ    []Event
	ctrlQ    [][]byte // pre-encoded control-event data
	bytes    int
	gaps     map[string]*gapRange
	seen     map[string]map[string]struct{} // topic -> written event ids (dedup)
	closed   bool
	removed  bool
	closeSet bool
	code     int
	reason   string
	exp      time.Time
	warned   bool

	notify    chan struct{}
	refreshCh chan time.Time
	done      chan struct{}
}

func newConn(id, subject, tenant, typ string, scopes []string, transport, ipHash string, hub *Hub, sink Sink, exp time.Time) *Conn {
	return &Conn{
		ID: id, Subject: subject, Tenant: tenant, Typ: typ, Scopes: scopes, Transport: transport, IPHash: ipHash,
		hub: hub, sink: sink, exp: exp,
		topics:    map[string]struct{}{},
		gaps:      map[string]*gapRange{},
		seen:      map[string]map[string]struct{}{},
		notify:    make(chan struct{}, 1),
		refreshCh: make(chan time.Time, 1),
		done:      make(chan struct{}),
	}
}

// Done is closed when the connection has fully terminated (writer exited).
func (c *Conn) Done() <-chan struct{} { return c.done }

func (c *Conn) signal() {
	select {
	case c.notify <- struct{}{}:
	default:
	}
}

// Enqueue offers one event to the connection (called from the fan-out loop). It
// never blocks. On overflow it applies the per-QoS policy (RTH-FR-030): chat
// closes 4409; state topics drop-oldest and accumulate a gap range.
func (c *Conn) Enqueue(ev Event) {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	over := len(c.dataQ) >= MaxQueueLen || c.bytes+len(ev.Data) > MaxQueueBytes
	if over {
		if ev.Chat {
			c.closeLocked(CloseTooSlow, "TOO_SLOW")
			c.mu.Unlock()
			c.signal()
			c.hub.metrics.SlowClose(true)
			return
		}
		// drop-oldest (BR-1) and record the gap for the dropped topic.
		old := c.dataQ[0]
		c.dataQ = c.dataQ[1:]
		c.bytes -= len(old.Data)
		c.recordGapLocked(old.Topic, old.ID)
		c.hub.metrics.Dropped(old.Topic)
	}
	c.dataQ = append(c.dataQ, ev)
	c.bytes += len(ev.Data)
	c.mu.Unlock()
	c.signal()
}

func (c *Conn) recordGapLocked(topic, id string) {
	g := c.gaps[topic]
	if g == nil {
		c.gaps[topic] = &gapRange{from: id, to: id}
		return
	}
	g.to = id // ids are uuidv7-monotonic per topic; extend the range
}

// EnqueueControl delivers a reliable control event (never dropped): gap, reset,
// revoked, replaced, subscribed, error, token_refresh.
func (c *Conn) EnqueueControl(ctrlType string, fields map[string]any) {
	m := map[string]any{"type": ctrlType}
	for k, v := range fields {
		m[k] = v
	}
	b, _ := json.Marshal(m)
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	c.ctrlQ = append(c.ctrlQ, b)
	c.mu.Unlock()
	c.signal()
}

// EnqueueFront prepends replayed events ahead of any live events already queued
// (RTH-FR-031/032). Replay ids are older (uuidv7-monotonic), so front-insertion
// preserves per-topic order; the writer's dedup drops any live/replay overlap.
func (c *Conn) EnqueueFront(evs []Event) {
	if len(evs) == 0 {
		return
	}
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	c.dataQ = append(append([]Event{}, evs...), c.dataQ...)
	for _, e := range evs {
		c.bytes += len(e.Data)
	}
	c.mu.Unlock()
	c.signal()
}

// RefreshExp updates the connection's token expiry after a successful refresh
// (RTH-FR-010). A different subject is rejected by the caller (BR-10).
func (c *Conn) RefreshExp(exp time.Time) {
	c.mu.Lock()
	c.exp = exp
	c.warned = false
	c.mu.Unlock()
	select {
	case c.refreshCh <- exp:
	default:
	}
}

// closeLocked marks the connection for close (mu held).
func (c *Conn) closeLocked(code int, reason string) {
	if c.closeSet {
		return
	}
	c.closeSet = true
	c.code = code
	c.reason = reason
}

// Close requests a graceful close with a code/reason.
func (c *Conn) Close(code int, reason string) {
	c.mu.Lock()
	c.closeLocked(code, reason)
	c.mu.Unlock()
	c.signal()
}

// run is the single writer goroutine. It flushes control events first, then
// data events (with dedup), sends 15s heartbeats, and enforces the token-
// refresh / expiry contract (RTH-FR-010). It returns when the connection closes.
func (c *Conn) run() {
	defer c.hub.remove(c)

	hb := time.NewTicker(HeartbeatInterval)
	defer hb.Stop()
	tokenTimer := time.NewTimer(c.untilWarn())
	defer tokenTimer.Stop()
	expiryTimer := time.NewTimer(c.untilHardClose())
	defer expiryTimer.Stop()

	for {
		if c.flush() {
			return // close was requested and written
		}
		select {
		case <-c.done:
			return
		case <-c.notify:
		case <-hb.C:
			if err := c.sink.WriteHeartbeat(c.hub.Degraded()); err != nil {
				c.Close(0, "write_error")
			}
		case <-tokenTimer.C:
			c.mu.Lock()
			c.warned = true
			c.mu.Unlock()
			c.EnqueueControl(CtrlTokenRefresh, map[string]any{"exp": c.expUnix()})
		case newExp := <-c.refreshCh:
			resetTimer(tokenTimer, time.Until(newExp.Add(-TokenWarnBefore)))
			resetTimer(expiryTimer, time.Until(newExp.Add(TokenGraceAfter)))
		case <-expiryTimer.C:
			c.Close(CloseTokenExpired, "TOKEN_EXPIRED")
		}
	}
}

func resetTimer(t *time.Timer, d time.Duration) {
	if !t.Stop() {
		select {
		case <-t.C:
		default:
		}
	}
	if d < 0 {
		d = 0
	}
	t.Reset(d)
}

func (c *Conn) untilWarn() time.Duration {
	if c.exp.IsZero() {
		return 100 * 365 * 24 * time.Hour // no expiry (service tokens in tests)
	}
	d := time.Until(c.exp.Add(-TokenWarnBefore))
	if d < 0 {
		d = 0
	}
	return d
}

func (c *Conn) untilHardClose() time.Duration {
	if c.exp.IsZero() {
		return 100 * 365 * 24 * time.Hour
	}
	d := time.Until(c.exp.Add(TokenGraceAfter))
	if d < 0 {
		d = 0
	}
	return d
}

func (c *Conn) expUnix() int64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.exp.IsZero() {
		return 0
	}
	return c.exp.Unix()
}

// flush drains control + data queues to the wire. It returns true when the
// connection was closed (writer should exit).
func (c *Conn) flush() bool {
	c.mu.Lock()
	// Materialize pending gaps as control events (reliable, RTH-FR-030).
	for topic, g := range c.gaps {
		b, _ := json.Marshal(map[string]any{"type": CtrlGap, "topic": topic, "from_id": g.from, "to_id": g.to})
		c.ctrlQ = append(c.ctrlQ, b)
		delete(c.gaps, topic)
	}
	ctrls := c.ctrlQ
	c.ctrlQ = nil
	data := c.dataQ
	c.dataQ = nil
	c.bytes = 0
	closeSet, code, reason := c.closeSet, c.code, c.reason
	c.mu.Unlock()

	for _, b := range ctrls {
		if err := c.sink.WriteEvent("", "control", b); err != nil {
			c.finish()
			return true
		}
	}
	for _, ev := range data {
		if ev.ID != "" && c.isDup(ev.Topic, ev.ID) {
			continue
		}
		if err := c.sink.WriteEvent(ev.ID, ev.Topic, ev.Data); err != nil {
			c.finish()
			return true
		}
	}
	if closeSet {
		_ = c.sink.Close(code, reason)
		c.finish()
		return true
	}
	return false
}

// isDup records event id for topic and reports whether it was already written
// (per-connection dedup, BR-6/AC-16). The set is bounded per topic.
func (c *Conn) isDup(topic, id string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	m := c.seen[topic]
	if m == nil {
		m = map[string]struct{}{}
		c.seen[topic] = m
	}
	if _, ok := m[id]; ok {
		return true
	}
	if len(m) >= dedupPerTopic {
		m = map[string]struct{}{} // bounded reset (ids are monotonic; overlap window is small)
		c.seen[topic] = m
	}
	m[id] = struct{}{}
	return false
}

func (c *Conn) finish() {
	c.mu.Lock()
	if !c.closed {
		c.closed = true
		close(c.done)
	}
	c.mu.Unlock()
}

// topicSet returns a snapshot of subscribed raw topic strings.
func (c *Conn) topicSet() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]string, 0, len(c.topics))
	for t := range c.topics {
		out = append(out, t)
	}
	return out
}

func (c *Conn) addTopic(raw string) {
	c.mu.Lock()
	c.topics[raw] = struct{}{}
	c.mu.Unlock()
}

func (c *Conn) removeTopic(raw string) {
	c.mu.Lock()
	delete(c.topics, raw)
	c.mu.Unlock()
}

func (c *Conn) hasTopic(raw string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.topics[raw]
	return ok
}
