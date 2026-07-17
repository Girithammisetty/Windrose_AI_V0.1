// Package exec is the execution broker (QRY-FR-040..046): planning
// (resolve → rewrite → classify → guard → estimate → route → ceilings),
// per-tenant concurrency governance with FIFO queueing and per-user
// fairness (QRY-FR-044), the run loop with runtime-ceiling watchdog (BR-6),
// cancellation (QRY-FR-045), the result cache (QRY-FR-046) and the inbound
// event reactions (BRD 05 §6).
package exec

import (
	"errors"
	"sync"

	"github.com/google/uuid"
)

// Queue/slot sentinels.
var (
	// ErrSlotBusy: no slot instantly available on the sync path (BR-5).
	ErrSlotBusy = errors.New("no slot instantly available")
	// ErrQueueFull: FIFO queue overflow → 429 (QRY-FR-044).
	ErrQueueFull = errors.New("queue full")
)

// Caps are the per-tenant concurrency limits (QRY-FR-044).
type Caps struct {
	Slots      int // default 10
	AgentSlots int // agent-class sub-cap, default 3
	QueueDepth int // default 50
}

// Grant is the outcome of an admission request.
type Grant struct {
	// Ready closes when the slot is granted.
	Ready <-chan struct{}
	// Aborted closes if the queued request was aborted (cancel, suspend,
	// dataset deletion).
	Aborted <-chan struct{}
	// Pos is the queue position at admission time; 0 = granted immediately.
	Pos int

	w *waiter
}

// AbortReason is set when Aborted fires.
func (g *Grant) AbortReason() string {
	if g.w == nil {
		return ""
	}
	g.w.mu.Lock()
	defer g.w.mu.Unlock()
	return g.w.abortReason
}

type holder struct {
	user  string
	agent bool
}

type waiter struct {
	execID  uuid.UUID
	user    string
	agent   bool
	ready   chan struct{}
	aborted chan struct{}

	mu          sync.Mutex
	abortReason string
}

type tenantSlots struct {
	caps    Caps
	running map[uuid.UUID]holder
	queue   []*waiter
}

// SlotManager is the in-memory per-tenant slot and queue governor. In a
// multi-replica deployment the same admission protocol moves to Redis
// (SETNX token buckets per BRD §4.1); the broker only sees this interface.
type SlotManager struct {
	mu      sync.Mutex
	tenants map[uuid.UUID]*tenantSlots
}

func NewSlotManager() *SlotManager {
	return &SlotManager{tenants: map[uuid.UUID]*tenantSlots{}}
}

func (m *SlotManager) tenant(t uuid.UUID, caps Caps) *tenantSlots {
	ts, ok := m.tenants[t]
	if !ok {
		ts = &tenantSlots{running: map[uuid.UUID]holder{}}
		m.tenants[t] = ts
	}
	ts.caps = normalizeCaps(caps)
	return ts
}

func normalizeCaps(c Caps) Caps {
	if c.Slots <= 0 {
		c.Slots = 10
	}
	if c.AgentSlots <= 0 {
		c.AgentSlots = 3
	}
	if c.QueueDepth <= 0 {
		c.QueueDepth = 50
	}
	return c
}

// userCap: one user may hold at most half the tenant slots (QRY-FR-044).
func userCap(slots int) int {
	c := slots / 2
	if c < 1 {
		c = 1
	}
	return c
}

func (ts *tenantSlots) eligible(user string, agent bool) bool {
	if len(ts.running) >= ts.caps.Slots {
		return false
	}
	userRunning, agentRunning := 0, 0
	for _, h := range ts.running {
		if h.user == user {
			userRunning++
		}
		if h.agent {
			agentRunning++
		}
	}
	if userRunning >= userCap(ts.caps.Slots) {
		return false
	}
	if agent && agentRunning >= ts.caps.AgentSlots {
		return false
	}
	return true
}

// Acquire admits an execution. canQueue=false is the sync path: either an
// instant slot or ErrSlotBusy (BR-5: sync never queues). canQueue=true
// queues FIFO up to the depth; overflow returns ErrQueueFull (429).
func (m *SlotManager) Acquire(tenant, execID uuid.UUID, user string, agent bool, caps Caps, canQueue bool) (*Grant, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	ts := m.tenant(tenant, caps)
	if ts.eligible(user, agent) && len(ts.queue) == 0 {
		ts.running[execID] = holder{user: user, agent: agent}
		ready := make(chan struct{})
		close(ready)
		return &Grant{Ready: ready, Aborted: make(chan struct{}), Pos: 0}, nil
	}
	if !canQueue {
		return nil, ErrSlotBusy
	}
	if len(ts.queue) >= ts.caps.QueueDepth {
		return nil, ErrQueueFull
	}
	w := &waiter{execID: execID, user: user, agent: agent, ready: make(chan struct{}), aborted: make(chan struct{})}
	ts.queue = append(ts.queue, w)
	// A later waiter might be eligible even when the head is not (per-user
	// fairness skips, FIFO among eligible).
	m.promoteLocked(ts)
	pos := m.positionLocked(ts, execID)
	g := &Grant{Ready: w.ready, Aborted: w.aborted, Pos: pos, w: w}
	if pos == 0 {
		g.Pos = 0
	}
	return g, nil
}

// Release frees a slot (or removes a queue entry) and promotes waiters.
func (m *SlotManager) Release(tenant, execID uuid.UUID) {
	m.mu.Lock()
	defer m.mu.Unlock()
	ts, ok := m.tenants[tenant]
	if !ok {
		return
	}
	if _, running := ts.running[execID]; running {
		delete(ts.running, execID)
	} else {
		m.removeWaiterLocked(ts, execID, "")
	}
	m.promoteLocked(ts)
}

// Abort removes a queued execution, closing its Aborted channel with the
// reason. Returns false when the execution was not queued.
func (m *SlotManager) Abort(tenant, execID uuid.UUID, reason string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	ts, ok := m.tenants[tenant]
	if !ok {
		return false
	}
	removed := m.removeWaiterLocked(ts, execID, reason)
	if removed {
		m.promoteLocked(ts)
	}
	return removed
}

// AbortAllQueued aborts every queued execution for a tenant (suspension).
func (m *SlotManager) AbortAllQueued(tenant uuid.UUID, reason string) []uuid.UUID {
	m.mu.Lock()
	defer m.mu.Unlock()
	ts, ok := m.tenants[tenant]
	if !ok {
		return nil
	}
	var ids []uuid.UUID
	for _, w := range ts.queue {
		w.mu.Lock()
		w.abortReason = reason
		w.mu.Unlock()
		close(w.aborted)
		ids = append(ids, w.execID)
	}
	ts.queue = nil
	return ids
}

// QueuedIDs lists currently queued executions for a tenant.
func (m *SlotManager) QueuedIDs(tenant uuid.UUID) []uuid.UUID {
	m.mu.Lock()
	defer m.mu.Unlock()
	ts, ok := m.tenants[tenant]
	if !ok {
		return nil
	}
	ids := make([]uuid.UUID, 0, len(ts.queue))
	for _, w := range ts.queue {
		ids = append(ids, w.execID)
	}
	return ids
}

// Position returns the 1-based queue position, 0 when running or absent.
func (m *SlotManager) Position(tenant, execID uuid.UUID) int {
	m.mu.Lock()
	defer m.mu.Unlock()
	ts, ok := m.tenants[tenant]
	if !ok {
		return 0
	}
	return m.positionLocked(ts, execID)
}

// RunningCount reports occupied slots (metric query_queue_depth peer).
func (m *SlotManager) RunningCount(tenant uuid.UUID) int {
	m.mu.Lock()
	defer m.mu.Unlock()
	ts, ok := m.tenants[tenant]
	if !ok {
		return 0
	}
	return len(ts.running)
}

func (m *SlotManager) positionLocked(ts *tenantSlots, execID uuid.UUID) int {
	for i, w := range ts.queue {
		if w.execID == execID {
			return i + 1
		}
	}
	return 0
}

func (m *SlotManager) removeWaiterLocked(ts *tenantSlots, execID uuid.UUID, abortReason string) bool {
	for i, w := range ts.queue {
		if w.execID == execID {
			ts.queue = append(ts.queue[:i], ts.queue[i+1:]...)
			if abortReason != "" {
				w.mu.Lock()
				w.abortReason = abortReason
				w.mu.Unlock()
				close(w.aborted)
			}
			return true
		}
	}
	return false
}

// promoteLocked grants slots to eligible waiters in FIFO order, skipping
// waiters blocked by fairness caps.
func (m *SlotManager) promoteLocked(ts *tenantSlots) {
	for {
		granted := false
		for i, w := range ts.queue {
			if ts.eligible(w.user, w.agent) {
				ts.running[w.execID] = holder{user: w.user, agent: w.agent}
				ts.queue = append(ts.queue[:i], ts.queue[i+1:]...)
				close(w.ready)
				granted = true
				break
			}
			// Head-of-line blocked by tenant-wide cap: nobody behind can be
			// eligible either.
			if len(ts.running) >= ts.caps.Slots {
				return
			}
			_ = i
		}
		if !granted {
			return
		}
	}
}
