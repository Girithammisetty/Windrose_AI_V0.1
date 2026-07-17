package fanout

import (
	"context"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/windrose-ai/realtime-hub/internal/metrics"
)

// leader is the minimal surface the hub needs from a Lease (nil = always
// publish, e.g. single-node unit tests).
type leader interface{ IsLeader() bool }

// Bus is the cross-pod fan-out transport the hub publishes to and subscribes on
// (satisfied by *RedisBus in the runtime; in-memory doubles only in unit tests).
type Bus interface {
	Publish(ctx context.Context, tenant, topic string, ev Event) error
	Subscribe(tenant, topic string)
	Unsubscribe(tenant, topic string)
}

// Reauthorizer re-evaluates whether a subject may still subscribe to a raw
// topic (RTH-FR-013). It backs revocation: on an rbac change the hub re-checks
// each affected subscription and terminates only those now denied, so additive
// grants do not tear down still-valid subscriptions. nil => terminate on any
// revocation signal (unit-test default).
type Reauthorizer func(subject, typ string, scopes []string, tenant, rawTopic string) bool

// resSub is one resource-scoped subscription (for revocation, RTH-FR-013).
type resSub struct {
	conn *Conn
	raw  string
}

// Hub is the pod-local connection registry and fan-out engine. It holds no
// cross-pod connection state (RTH-FR-041); scale-out is via RedisBus.
type Hub struct {
	PodID   string
	bus     Bus
	replay  *Replay
	caps    *Caps
	kafkaLeader leader
	metrics *metrics.Metrics
	maxPod  int
	reauth  Reauthorizer

	mu       sync.RWMutex
	conns    map[string]*Conn            // conn_id -> conn
	topicIdx map[string]map[string]*Conn // "tenant/raw" -> conn_id -> conn
	resIdx   map[string]map[string]resSub // "tenant|urn" -> conn_id -> sub
	userIdx  map[string][]*Conn          // "tenant/user" -> conns (connect order)

	degraded atomic.Bool
	closed   atomic.Bool
}

// HubConfig configures a Hub.
type HubConfig struct {
	PodID       string
	Bus         Bus
	Replay      *Replay
	Caps        *Caps
	KafkaLeader leader
	Metrics     *metrics.Metrics
	MaxPerPod   int
}

// NewHub builds a Hub.
func NewHub(cfg HubConfig) *Hub {
	if cfg.MaxPerPod <= 0 {
		cfg.MaxPerPod = DefaultPerPod
	}
	return &Hub{
		PodID: cfg.PodID, bus: cfg.Bus, replay: cfg.Replay, caps: cfg.Caps,
		kafkaLeader: cfg.KafkaLeader, metrics: cfg.Metrics, maxPod: cfg.MaxPerPod,
		conns:    map[string]*Conn{},
		topicIdx: map[string]map[string]*Conn{},
		resIdx:   map[string]map[string]resSub{},
		userIdx:  map[string][]*Conn{},
	}
}

// SetBus attaches the Redis pub/sub bus after construction (breaks the
// hub↔bus construction cycle: the bus delivers into hub.OnBusMessage).
func (h *Hub) SetBus(b Bus) { h.bus = b }

// SetReauthorizer wires the revocation re-evaluation hook (RTH-FR-013).
func (h *Hub) SetReauthorizer(f Reauthorizer) { h.reauth = f }

// Degraded reports the current staleness hint (BR-7).
func (h *Hub) Degraded() bool { return h.degraded.Load() }

// SetDegraded toggles the staleness hint surfaced in heartbeats.
func (h *Hub) SetDegraded(v bool) { h.degraded.Store(v) }

// PodCount is the pod-local connection count (RTH-FR-040 per-pod cap).
func (h *Hub) PodCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.conns)
}

// AddConn registers a new connection and starts its writer goroutine. The
// caller has already reserved the tenant/user cap (RTH-FR-040).
func (h *Hub) AddConn(id, subject, tenant, typ string, scopes []string, transport, ipHash string, sink Sink, exp time.Time) *Conn {
	c := newConn(id, subject, tenant, typ, scopes, transport, ipHash, h, sink, exp)
	h.mu.Lock()
	h.conns[id] = c
	uk := tenant + "/" + subject
	h.userIdx[uk] = append(h.userIdx[uk], c)
	h.mu.Unlock()
	if h.metrics != nil {
		h.metrics.ActiveConns.WithLabelValues(tenant, transport).Inc()
	}
	go c.run()
	return c
}

// PodFull reports whether the per-pod cap is reached (RTH-FR-040).
func (h *Hub) PodFull() bool { return h.PodCount() >= h.maxPod }

// remove tears down a connection (idempotent). Called from the writer's defer
// and from eviction.
func (h *Hub) remove(c *Conn) {
	c.mu.Lock()
	already := c.removed
	c.removed = true
	c.mu.Unlock()
	if already {
		return
	}

	h.mu.Lock()
	delete(h.conns, c.ID)
	for _, raw := range c.topicSet() {
		key := c.Tenant + "/" + raw
		if m := h.topicIdx[key]; m != nil {
			delete(m, c.ID)
			if len(m) == 0 {
				delete(h.topicIdx, key)
				h.bus.Unsubscribe(c.Tenant, raw)
			}
		}
	}
	for rk, m := range h.resIdx {
		if _, ok := m[c.ID]; ok {
			delete(m, c.ID)
			if len(m) == 0 {
				delete(h.resIdx, rk)
			}
		}
	}
	uk := c.Tenant + "/" + c.Subject
	if lst := h.userIdx[uk]; lst != nil {
		out := lst[:0]
		for _, x := range lst {
			if x != c {
				out = append(out, x)
			}
		}
		if len(out) == 0 {
			delete(h.userIdx, uk)
		} else {
			h.userIdx[uk] = out
		}
	}
	h.mu.Unlock()

	if h.caps != nil {
		h.caps.Release(context.Background(), c.Tenant, c.Subject)
	}
	if h.metrics != nil {
		h.metrics.ActiveConns.WithLabelValues(c.Tenant, c.Transport).Dec()
	}
	c.finish()
}

// EvictOldestUser closes the oldest local connection of (tenant, user) with a
// `replaced` control event and frees its cap slot synchronously (BR-11 /
// X-Replace-Oldest). Returns true when one was evicted.
func (h *Hub) EvictOldestUser(tenant, user string) bool {
	h.mu.RLock()
	lst := h.userIdx[tenant+"/"+user]
	var victim *Conn
	if len(lst) > 0 {
		victim = lst[0]
	}
	h.mu.RUnlock()
	if victim == nil {
		return false
	}
	victim.EnqueueControl(CtrlReplaced, nil)
	victim.Close(CloseAllForbidden, "REPLACED")
	h.remove(victim)
	return true
}

// SubResult is the outcome of one Subscribe.
type SubResult struct {
	Replayed int
	Reset    bool
}

// Subscribe registers conn for a tenant-scoped topic, wires the cross-pod
// subscription, replays any missed events after lastEventID (RTH-FR-031), and
// records the resource index for revocation (RTH-FR-013). Idempotent (BR-5).
func (h *Hub) Subscribe(ctx context.Context, c *Conn, tenant, raw, urn, lastEventID string) SubResult {
	if c.hasTopic(raw) {
		c.EnqueueControl(CtrlSubscribed, map[string]any{"topic": raw, "replayed": 0})
		return SubResult{}
	}
	key := tenant + "/" + raw
	h.mu.Lock()
	if h.topicIdx[key] == nil {
		h.topicIdx[key] = map[string]*Conn{}
	}
	newChannel := len(h.topicIdx[key]) == 0
	h.topicIdx[key][c.ID] = c
	if urn != "" {
		rk := tenant + "|" + urn
		if h.resIdx[rk] == nil {
			h.resIdx[rk] = map[string]resSub{}
		}
		h.resIdx[rk][c.ID] = resSub{conn: c, raw: raw}
	}
	h.mu.Unlock()
	c.addTopic(raw)
	if newChannel {
		h.bus.Subscribe(tenant, raw)
	}

	// Replay before live (RTH-FR-031/032).
	var res SubResult
	if h.replay != nil {
		evs, reset, err := h.replay.Resume(ctx, tenant, raw, lastEventID)
		if err == nil {
			if reset {
				res.Reset = true
				c.EnqueueControl(CtrlReset, map[string]any{"topic": raw})
				if h.metrics != nil {
					h.metrics.ReplayResets.Inc()
				}
			} else if len(evs) > 0 {
				res.Replayed = len(evs)
				c.EnqueueFront(evs)
				if h.metrics != nil {
					h.metrics.ReplayHits.Inc()
				}
			}
		}
	}
	c.EnqueueControl(CtrlSubscribed, map[string]any{"topic": raw, "replayed": res.Replayed})
	return res
}

// Unsubscribe removes a topic from conn (idempotent, BR-5).
func (h *Hub) Unsubscribe(c *Conn, tenant, raw string) {
	if !c.hasTopic(raw) {
		return
	}
	key := tenant + "/" + raw
	h.mu.Lock()
	if m := h.topicIdx[key]; m != nil {
		delete(m, c.ID)
		if len(m) == 0 {
			delete(h.topicIdx, key)
			h.bus.Unsubscribe(tenant, raw)
		}
	}
	for rk, m := range h.resIdx {
		if s, ok := m[c.ID]; ok && s.raw == raw {
			delete(m, c.ID)
			if len(m) == 0 {
				delete(h.resIdx, rk)
			}
		}
	}
	h.mu.Unlock()
	c.removeTopic(raw)
}

// deliverLocal fans one live event out to every local connection subscribed to
// (tenant, topic). Non-blocking per connection (BR-1). This is the RedisBus
// onMsg callback — the single delivery path to connections.
func (h *Hub) deliverLocal(tenant, topic string, ev Event) {
	key := tenant + "/" + topic
	h.mu.RLock()
	conns := make([]*Conn, 0, len(h.topicIdx[key]))
	for _, c := range h.topicIdx[key] {
		conns = append(conns, c)
	}
	h.mu.RUnlock()
	for _, c := range conns {
		c.Enqueue(ev)
	}
}

// OnBusMessage is the callback wired into RedisBus.
func (h *Hub) OnBusMessage(tenant, topic string, ev Event) { h.deliverLocal(tenant, topic, ev) }

// IngestKafka publishes a routed Kafka event into the fan-out system. Only the
// leader writes the replay buffer and republishes to the bus (RTH-FR-041/042);
// non-leaders drop (they consume Kafka only to be warm for failover).
func (h *Hub) IngestKafka(ctx context.Context, tenant, topic string, ev Event) error {
	if h.kafkaLeader != nil && !h.kafkaLeader.IsLeader() {
		return nil
	}
	return h.publish(ctx, tenant, topic, ev)
}

// IngestInternal publishes a low-latency internal event (chat tokens) into the
// fan-out system from whichever pod received it (RTH-FR-021). Not leader-gated;
// replay XADD dedups by event_id so a Kafka+internal duplicate delivers once
// (AC-16).
func (h *Hub) IngestInternal(ctx context.Context, tenant, topic string, ev Event, ttl time.Duration) error {
	if ttl == 0 {
		// Ephemeral (BR-13): skip the replay buffer, publish live only.
		return h.bus.Publish(ctx, tenant, topic, ev)
	}
	return h.publish(ctx, tenant, topic, ev)
}

func (h *Hub) publish(ctx context.Context, tenant, topic string, ev Event) error {
	start := time.Now()
	fresh := true
	if h.replay != nil {
		var err error
		fresh, err = h.replay.Append(ctx, tenant, topic, ev)
		if err != nil {
			return err
		}
	}
	if !fresh {
		return nil // already published by the other source (dedup)
	}
	err := h.bus.Publish(ctx, tenant, topic, ev)
	if h.metrics != nil {
		h.metrics.FaninWriteSec.Observe(time.Since(start).Seconds())
	}
	return err
}

// Revoke re-evaluates every subscription whose topic references urn and
// terminates only those the caller can no longer subscribe to, sending a
// `revoked` control event and unsubscribing that topic while others continue
// (RTH-FR-013 / AC-6). When no Reauthorizer is wired it terminates all affected
// subscriptions (unit-test default). Additive grant changes therefore keep
// still-authorized subscriptions alive. Returns the number terminated.
func (h *Hub) Revoke(tenant, urn string) int {
	rk := tenant + "|" + urn
	h.mu.RLock()
	subs := make([]resSub, 0, len(h.resIdx[rk]))
	for _, s := range h.resIdx[rk] {
		subs = append(subs, s)
	}
	reauth := h.reauth
	h.mu.RUnlock()
	terminated := 0
	for _, s := range subs {
		if reauth != nil && reauth(s.conn.Subject, s.conn.Typ, s.conn.Scopes, tenant, s.raw) {
			continue // still authorized after the change (e.g. additive grant)
		}
		s.conn.EnqueueControl(CtrlRevoked, map[string]any{"topic": s.raw})
		h.Unsubscribe(s.conn, tenant, s.raw)
		if h.metrics != nil {
			h.metrics.Revocations.Inc()
		}
		terminated++
	}
	return terminated
}

// Drain sends a `reconnect` control to every connection then closes 1012 after
// the grace period (RTH-FR-033 / AC-9). Clients reconnect immediately elsewhere
// and resume from the replay buffer.
func (h *Hub) Drain(afterMS int, grace time.Duration) {
	h.closed.Store(true)
	h.mu.RLock()
	conns := make([]*Conn, 0, len(h.conns))
	for _, c := range h.conns {
		conns = append(conns, c)
	}
	h.mu.RUnlock()
	for _, c := range conns {
		c.EnqueueControl(CtrlReconnect, map[string]any{"after_ms": afterMS})
	}
	time.Sleep(grace)
	for _, c := range conns {
		c.Close(CloseServerDrain, "server_drain")
	}
}

// Connections returns a snapshot for the admin API (RTH-FR-044).
func (h *Hub) Connections(tenant string) []ConnInfo {
	h.mu.RLock()
	defer h.mu.RUnlock()
	out := []ConnInfo{}
	for _, c := range h.conns {
		if tenant != "" && c.Tenant != tenant {
			continue
		}
		out = append(out, ConnInfo{ID: c.ID, Subject: c.Subject, Tenant: c.Tenant,
			Transport: c.Transport, Topics: c.topicSet()})
	}
	return out
}

// ConnByID returns a pod-local connection by id, or nil. Incremental-subscribe
// and token-refresh side channels target the pod holding the connection
// (RTH-FR-001); a request that lands on another pod gets a 404 (the edge routes
// these by conn_id).
func (h *Hub) ConnByID(id string) *Conn {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.conns[id]
}

// KillConnection force-closes a connection by id (admin, RTH-FR-044).
func (h *Hub) KillConnection(id string) bool {
	h.mu.RLock()
	c := h.conns[id]
	h.mu.RUnlock()
	if c == nil {
		return false
	}
	c.Close(CloseServerDrain, "admin_kill")
	h.remove(c)
	return true
}

// ConnInfo is an admin view of one connection.
type ConnInfo struct {
	ID        string   `json:"id"`
	Subject   string   `json:"subject"`
	Tenant    string   `json:"tenant"`
	Transport string   `json:"transport"`
	Topics    []string `json:"topics"`
}

// PubSubHealthy pings the replay/bus Redis for readiness (RTH-FR / AC-13).
func PubSubHealthy(ctx context.Context, rdb redis.UniversalClient) bool {
	return rdb.Ping(ctx).Err() == nil
}
