package domain

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestStateMachine(t *testing.T) {
	legal := [][2]string{
		{StatusCreated, StatusPlanning},
		{StatusPlanning, StatusRejected},
		{StatusPlanning, StatusQueued},
		{StatusPlanning, StatusRunning},
		{StatusPlanning, StatusSucceeded}, // cache hit
		{StatusQueued, StatusRunning},
		{StatusQueued, StatusCancelled},
		{StatusRunning, StatusStreamingResults},
		{StatusRunning, StatusSucceeded},
		{StatusRunning, StatusFailed},
		{StatusRunning, StatusCancelled},
		{StatusRunning, StatusCeilingExceeded},
		{StatusStreamingResults, StatusSucceeded},
		{StatusStreamingResults, StatusCancelled},
		{StatusStreamingResults, StatusCeilingExceeded},
	}
	for _, tr := range legal {
		assert.True(t, CanTransition(tr[0], tr[1]), "%s→%s must be legal", tr[0], tr[1])
	}
	illegal := [][2]string{
		{StatusSucceeded, StatusRunning},
		{StatusCancelled, StatusRunning},
		{StatusRejected, StatusQueued},
		{StatusFailed, StatusSucceeded},
		{StatusQueued, StatusStreamingResults},
		{StatusCeilingExceeded, StatusSucceeded},
	}
	for _, tr := range illegal {
		assert.False(t, CanTransition(tr[0], tr[1]), "%s→%s must be illegal", tr[0], tr[1])
	}
	for _, s := range []string{StatusSucceeded, StatusFailed, StatusCancelled, StatusRejected, StatusCeilingExceeded} {
		assert.True(t, IsTerminalStatus(s))
	}
	assert.False(t, IsTerminalStatus(StatusRunning))
}

// QRY-FR-042: defaults, agent tier, tenant overrides (lowest wins).
func TestEffectiveCeilings(t *testing.T) {
	c := EffectiveCeilings(nil, CallerUser, false)
	assert.Equal(t, int64(DefaultMaxScanBytes), c.MaxScanBytes)
	assert.Equal(t, int64(DefaultMaxRuntimeSyncS), c.MaxRuntimeS)

	c = EffectiveCeilings(nil, CallerUser, true)
	assert.Equal(t, int64(DefaultMaxRuntimeAsyncS), c.MaxRuntimeS, "V1 1600s timeout preserved as async cap")

	c = EffectiveCeilings(nil, CallerAgent, true)
	assert.Equal(t, int64(AgentMaxScanBytes), c.MaxScanBytes)
	assert.Equal(t, int64(AgentMaxResultRows), c.MaxResultRows)
	assert.Equal(t, int64(AgentMaxResultBytes), c.MaxResultBytes)

	ten := int64(10 << 30)
	c = EffectiveCeilings(&TenantLimits{MaxScanBytes: &ten}, CallerUser, true)
	assert.Equal(t, ten, c.MaxScanBytes, "TA override lowers the ceiling")

	c = EffectiveCeilings(&TenantLimits{MaxScanBytes: &ten}, CallerAgent, true)
	assert.Equal(t, int64(AgentMaxScanBytes), c.MaxScanBytes, "agent tier stays stricter than TA override")
}

func TestTenantLimitsValidate(t *testing.T) {
	tooHigh := int64(DefaultMaxScanBytes + 1)
	err := (&TenantLimits{MaxScanBytes: &tooHigh}).Validate()
	assert.Error(t, err, "overrides above platform maxima rejected")

	ok := int64(1 << 30)
	assert.NoError(t, (&TenantLimits{MaxScanBytes: &ok}).Validate())

	zero := int64(0)
	assert.Error(t, (&TenantLimits{MaxResultRows: &zero}).Validate())

	slots := 20
	assert.Error(t, (&TenantLimits{ConcurrentSlots: &slots}).Validate())
}

func TestCallerClassForTyp(t *testing.T) {
	assert.Equal(t, CallerUser, CallerClassForTyp(TypUser))
	assert.Equal(t, CallerService, CallerClassForTyp(TypService))
	assert.Equal(t, CallerAgent, CallerClassForTyp(TypAgentOBO))
	assert.Equal(t, CallerAgent, CallerClassForTyp(TypAgentAutonomous))
}
