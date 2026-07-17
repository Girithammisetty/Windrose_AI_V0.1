package exec

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func caps10() Caps { return Caps{Slots: 10, AgentSlots: 3, QueueDepth: 50} }

func granted(g *Grant) bool {
	select {
	case <-g.Ready:
		return true
	default:
		return false
	}
}

// AC-7 core: cap 10, the 11th queues at position 1, starts when a slot
// frees; queue overflow → ErrQueueFull.
func TestSlotsCapQueueAndOverflow(t *testing.T) {
	m := NewSlotManager()
	tenant := uuid.New()
	var running []uuid.UUID
	for i := 0; i < 10; i++ {
		id := uuid.New()
		g, err := m.Acquire(tenant, id, uuid.NewString(), false, caps10(), true)
		require.NoError(t, err)
		require.True(t, granted(g), "slot %d grants immediately", i)
		running = append(running, id)
	}
	eleventh := uuid.New()
	g11, err := m.Acquire(tenant, eleventh, uuid.NewString(), false, caps10(), true)
	require.NoError(t, err)
	assert.False(t, granted(g11))
	assert.Equal(t, 1, g11.Pos, "11th run is queued with queue_position=1")
	assert.Equal(t, 1, m.Position(tenant, eleventh))

	// Fill the queue to 50 total.
	for i := 0; i < 49; i++ {
		_, err := m.Acquire(tenant, uuid.New(), uuid.NewString(), false, caps10(), true)
		require.NoError(t, err)
	}
	// 61st (10 running + 50 queued) overflows.
	_, err = m.Acquire(tenant, uuid.New(), uuid.NewString(), false, caps10(), true)
	assert.ErrorIs(t, err, ErrQueueFull)

	// Free one slot → the 11th is promoted FIFO.
	m.Release(tenant, running[0])
	select {
	case <-g11.Ready:
	case <-time.After(time.Second):
		t.Fatal("queued execution not promoted after release")
	}
	assert.Equal(t, 0, m.Position(tenant, eleventh))
}

// BR-5: the sync path never queues.
func TestSlotsSyncNeverQueues(t *testing.T) {
	m := NewSlotManager()
	tenant := uuid.New()
	caps := Caps{Slots: 1, AgentSlots: 1, QueueDepth: 50}
	g, err := m.Acquire(tenant, uuid.New(), "u1", false, caps, true)
	require.NoError(t, err)
	require.True(t, granted(g))
	_, err = m.Acquire(tenant, uuid.New(), "u2", false, caps, false)
	assert.ErrorIs(t, err, ErrSlotBusy)
}

// QRY-FR-044: one user may hold at most half the tenant slots.
func TestSlotsPerUserFairness(t *testing.T) {
	m := NewSlotManager()
	tenant := uuid.New()
	// greedy user takes 5 of 10
	var greedy []uuid.UUID
	for i := 0; i < 5; i++ {
		id := uuid.New()
		g, err := m.Acquire(tenant, id, "greedy", false, caps10(), true)
		require.NoError(t, err)
		require.True(t, granted(g))
		greedy = append(greedy, id)
	}
	// 6th for the same user queues despite 5 free tenant slots
	blocked := uuid.New()
	g6, err := m.Acquire(tenant, blocked, "greedy", false, caps10(), true)
	require.NoError(t, err)
	assert.False(t, granted(g6), "user cap = slots/2 (fairness)")

	// a different user overtakes the fairness-blocked head (FIFO among
	// eligible)
	gOther, err := m.Acquire(tenant, uuid.New(), "other", false, caps10(), true)
	require.NoError(t, err)
	assert.True(t, granted(gOther))

	// greedy frees one → their queued run promotes
	m.Release(tenant, greedy[0])
	select {
	case <-g6.Ready:
	case <-time.After(time.Second):
		t.Fatal("fairness-blocked run never promoted")
	}
}

// QRY-FR-044: agent-class sub-cap 3.
func TestSlotsAgentSubCap(t *testing.T) {
	m := NewSlotManager()
	tenant := uuid.New()
	for i := 0; i < 3; i++ {
		g, err := m.Acquire(tenant, uuid.New(), uuid.NewString(), true, caps10(), true)
		require.NoError(t, err)
		require.True(t, granted(g))
	}
	gAgent, err := m.Acquire(tenant, uuid.New(), uuid.NewString(), true, caps10(), true)
	require.NoError(t, err)
	assert.False(t, granted(gAgent), "4th agent run queues (sub-cap 3)")

	gUser, err := m.Acquire(tenant, uuid.New(), uuid.NewString(), false, caps10(), true)
	require.NoError(t, err)
	assert.True(t, granted(gUser), "user runs unaffected by the agent sub-cap")
}

// Tenants are isolated slot pools.
func TestSlotsTenantIsolation(t *testing.T) {
	m := NewSlotManager()
	a, b := uuid.New(), uuid.New()
	caps := Caps{Slots: 1, AgentSlots: 1, QueueDepth: 5}
	g, err := m.Acquire(a, uuid.New(), "u", false, caps, true)
	require.NoError(t, err)
	require.True(t, granted(g))
	g2, err := m.Acquire(b, uuid.New(), "u", false, caps, true)
	require.NoError(t, err)
	assert.True(t, granted(g2), "tenant B unaffected by tenant A saturation")
}

func TestSlotsAbort(t *testing.T) {
	m := NewSlotManager()
	tenant := uuid.New()
	caps := Caps{Slots: 1, AgentSlots: 1, QueueDepth: 5}
	id1, id2 := uuid.New(), uuid.New()
	g1, _ := m.Acquire(tenant, id1, "u1", false, caps, true)
	require.True(t, granted(g1))
	g2, _ := m.Acquire(tenant, id2, "u2", false, caps, true)
	require.False(t, granted(g2))

	require.True(t, m.Abort(tenant, id2, "cancel"))
	select {
	case <-g2.Aborted:
		assert.Equal(t, "cancel", g2.AbortReason())
	case <-time.After(time.Second):
		t.Fatal("abort channel never fired")
	}
	assert.False(t, m.Abort(tenant, id1, "cancel"), "running executions are not queue-abortable")
}

func TestSlotsAbortAllQueued(t *testing.T) {
	m := NewSlotManager()
	tenant := uuid.New()
	caps := Caps{Slots: 1, AgentSlots: 1, QueueDepth: 5}
	g1, _ := m.Acquire(tenant, uuid.New(), "u1", false, caps, true)
	require.True(t, granted(g1))
	var grants []*Grant
	for i := 0; i < 3; i++ {
		g, _ := m.Acquire(tenant, uuid.New(), uuid.NewString(), false, caps, true)
		grants = append(grants, g)
	}
	ids := m.AbortAllQueued(tenant, "suspended")
	assert.Len(t, ids, 3)
	for _, g := range grants {
		select {
		case <-g.Aborted:
			assert.Equal(t, "suspended", g.AbortReason())
		case <-time.After(time.Second):
			t.Fatal("abort not delivered")
		}
	}
}
