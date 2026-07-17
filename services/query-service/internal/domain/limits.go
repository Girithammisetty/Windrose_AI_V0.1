package domain

import (
	"fmt"
	"time"

	"github.com/google/uuid"
)

// Ceilings are the enforced cost limits for one execution (QRY-FR-042).
type Ceilings struct {
	MaxScanBytes   int64 `json:"max_scan_bytes"`
	MaxRuntimeS    int64 `json:"max_runtime_s"`
	MaxResultBytes int64 `json:"max_result_bytes"`
	MaxResultRows  int64 `json:"max_result_rows"`
}

// Platform defaults and maxima (QRY-FR-042). Tenant admins may only lower
// values; the platform maxima are the defaults.
const (
	DefaultMaxScanBytes     = 50 << 30 // 50 GB
	AgentMaxScanBytes       = 5 << 30  // 5 GB
	DefaultMaxRuntimeSyncS  = 300      // interactive
	DefaultMaxRuntimeAsyncS = 1600     // V1 timeout preserved as the async cap
	DefaultMaxResultBytes   = 1 << 30  // 1 GB
	AgentMaxResultBytes     = 50 << 20 // 50 MB
	DefaultMaxResultRows    = 5_000_000
	AgentMaxResultRows      = 10_000

	// Concurrency governance (QRY-FR-044).
	DefaultTenantSlots   = 10
	DefaultAgentSubSlots = 3
	MaxQueueDepth        = 50

	// Agent LIMIT injection cap (QRY-FR-022).
	AgentInjectedLimit = 10_000

	// Sync-mode admission (QRY-FR-043): plan must say small.
	SyncMaxEstimatedScanBytes = 10 << 20 // ≤ 10MB expected
)

// TenantLimits are TA-configurable downward overrides (QRY-FR-042, US-7).
type TenantLimits struct {
	TenantID         uuid.UUID `json:"-"`
	MaxScanBytes     *int64    `json:"max_scan_bytes,omitempty"`
	MaxRuntimeS      *int64    `json:"max_runtime_s,omitempty"`
	MaxResultBytes   *int64    `json:"max_result_bytes,omitempty"`
	MaxResultRows    *int64    `json:"max_result_rows,omitempty"`
	ConcurrentSlots  *int      `json:"concurrent_slots,omitempty"`
	WarehousePrimary bool      `json:"warehouse_primary,omitempty"` // routing rule 1 (§4.3)
	UpdatedBy        string    `json:"updated_by,omitempty"`
}

// Validate rejects overrides above platform maxima (QRY-FR-042).
func (t *TenantLimits) Validate() error {
	check := func(name string, v *int64, max int64) error {
		if v == nil {
			return nil
		}
		if *v <= 0 || *v > max {
			return EValidationDetails("tenant limit out of range",
				map[string]string{name: fmt.Sprintf("must be in (0, %d]", max)})
		}
		return nil
	}
	if err := check("max_scan_bytes", t.MaxScanBytes, DefaultMaxScanBytes); err != nil {
		return err
	}
	if err := check("max_runtime_s", t.MaxRuntimeS, DefaultMaxRuntimeAsyncS); err != nil {
		return err
	}
	if err := check("max_result_bytes", t.MaxResultBytes, DefaultMaxResultBytes); err != nil {
		return err
	}
	if err := check("max_result_rows", t.MaxResultRows, DefaultMaxResultRows); err != nil {
		return err
	}
	if t.ConcurrentSlots != nil && (*t.ConcurrentSlots <= 0 || *t.ConcurrentSlots > DefaultTenantSlots) {
		return EValidationDetails("tenant limit out of range",
			map[string]string{"concurrent_slots": fmt.Sprintf("must be in (0, %d]", DefaultTenantSlots)})
	}
	return nil
}

// EffectiveCeilings merges platform defaults, agent tier, and tenant
// overrides — the lowest value always wins (QRY-FR-042).
func EffectiveCeilings(limits *TenantLimits, caller CallerClass, async bool) Ceilings {
	c := Ceilings{
		MaxScanBytes:   DefaultMaxScanBytes,
		MaxRuntimeS:    DefaultMaxRuntimeSyncS,
		MaxResultBytes: DefaultMaxResultBytes,
		MaxResultRows:  DefaultMaxResultRows,
	}
	if async {
		c.MaxRuntimeS = DefaultMaxRuntimeAsyncS
	}
	if caller == CallerAgent {
		c.MaxScanBytes = min64(c.MaxScanBytes, AgentMaxScanBytes)
		c.MaxResultBytes = min64(c.MaxResultBytes, AgentMaxResultBytes)
		c.MaxResultRows = min64(c.MaxResultRows, AgentMaxResultRows)
	}
	if limits != nil {
		if limits.MaxScanBytes != nil {
			c.MaxScanBytes = min64(c.MaxScanBytes, *limits.MaxScanBytes)
		}
		if limits.MaxRuntimeS != nil {
			c.MaxRuntimeS = min64(c.MaxRuntimeS, *limits.MaxRuntimeS)
		}
		if limits.MaxResultBytes != nil {
			c.MaxResultBytes = min64(c.MaxResultBytes, *limits.MaxResultBytes)
		}
		if limits.MaxResultRows != nil {
			c.MaxResultRows = min64(c.MaxResultRows, *limits.MaxResultRows)
		}
	}
	return c
}

// MaxRuntime is the runtime ceiling as a duration.
func (c Ceilings) MaxRuntime() time.Duration { return time.Duration(c.MaxRuntimeS) * time.Second }

func min64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
