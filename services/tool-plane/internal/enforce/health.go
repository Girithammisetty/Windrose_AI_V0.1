package enforce

import (
	"context"
	"sort"
	"strconv"
	"time"

	"github.com/windrose-ai/go-common/redisx"
	"github.com/windrose-ai/tool-plane/internal/domain"
	"github.com/windrose-ai/tool-plane/internal/events"
)

// HealthStore keeps real per-tool-version rolling health counters in Redis
// (TPL-FR-050): success/error counts, error taxonomy, and a latency sample
// window for p50/p95/p99. Written from the enforcement invoke path, read by the
// registry health API and the SLA-breach detector.
type HealthStore struct {
	r *redisx.Client
}

// NewHealthStore builds a health store over Redis.
func NewHealthStore(r *redisx.Client) *HealthStore { return &HealthStore{r: r} }

func healthKey(tool, ver string) string     { return "tp:health:" + tool + ":" + ver }
func healthLatKey(tool, ver string) string  { return "tp:health:" + tool + ":" + ver + ":lat" }
func breachKey(tool, ver string) string     { return "tp:health:" + tool + ":" + ver + ":breachstreak" }

const healthTTL = time.Hour
const latWindow = 1000

// Record ingests one backend-dispatched outcome (TPL-FR-050).
func (h *HealthStore) Record(ctx context.Context, toolID, version string, latencyMS int, ok bool, errKind string) {
	if h == nil || h.r == nil {
		return
	}
	k := healthKey(toolID, version)
	pipe := h.r.R.TxPipeline()
	pipe.HIncrBy(ctx, k, "calls", 1)
	if ok {
		pipe.HIncrBy(ctx, k, "success", 1)
	} else {
		if errKind == "" {
			errKind = "backend_error"
		}
		pipe.HIncrBy(ctx, k, "err:"+errKind, 1)
	}
	pipe.Expire(ctx, k, healthTTL)
	lk := healthLatKey(toolID, version)
	pipe.LPush(ctx, lk, latencyMS)
	pipe.LTrim(ctx, lk, 0, latWindow-1)
	pipe.Expire(ctx, lk, healthTTL)
	_, _ = pipe.Exec(ctx)
}

// HealthSnapshot is the rolling health view (TPL-FR-050).
type HealthSnapshot struct {
	Calls        int64            `json:"calls"`
	Success      int64            `json:"success"`
	ErrorsByKind map[string]int64 `json:"errors_by_kind"`
	ErrorRatePct float64          `json:"error_rate_pct"`
	P50MS        int              `json:"p50_ms"`
	P95MS        int              `json:"p95_ms"`
	P99MS        int              `json:"p99_ms"`
}

// Snapshot reads the current rolling health for a tool version.
func (h *HealthStore) Snapshot(ctx context.Context, toolID, version string) (HealthSnapshot, error) {
	var s HealthSnapshot
	s.ErrorsByKind = map[string]int64{}
	if h == nil || h.r == nil {
		return s, nil
	}
	fields, err := h.r.R.HGetAll(ctx, healthKey(toolID, version)).Result()
	if err != nil {
		return s, err
	}
	var errs int64
	for f, v := range fields {
		n, _ := strconv.ParseInt(v, 10, 64)
		switch {
		case f == "calls":
			s.Calls = n
		case f == "success":
			s.Success = n
		case len(f) > 4 && f[:4] == "err:":
			s.ErrorsByKind[f[4:]] = n
			errs += n
		}
	}
	if s.Calls > 0 {
		s.ErrorRatePct = float64(errs) / float64(s.Calls) * 100
	}
	lat, err := h.r.R.LRange(ctx, healthLatKey(toolID, version), 0, -1).Result()
	if err == nil && len(lat) > 0 {
		vals := make([]int, 0, len(lat))
		for _, x := range lat {
			if n, e := strconv.Atoi(x); e == nil {
				vals = append(vals, n)
			}
		}
		sort.Ints(vals)
		s.P50MS = percentile(vals, 0.50)
		s.P95MS = percentile(vals, 0.95)
		s.P99MS = percentile(vals, 0.99)
	}
	return s, nil
}

func percentile(sorted []int, p float64) int {
	if len(sorted) == 0 {
		return 0
	}
	idx := int(p * float64(len(sorted)-1))
	if idx < 0 {
		idx = 0
	}
	if idx >= len(sorted) {
		idx = len(sorted) - 1
	}
	return sorted[idx]
}

// tickBreach increments the consecutive-breach streak when breached, or resets
// it to 0 otherwise, returning the current streak.
func (h *HealthStore) tickBreach(ctx context.Context, toolID, version string, breached bool) int {
	k := breachKey(toolID, version)
	if !breached {
		_ = h.r.Del(ctx, k)
		return 0
	}
	n, err := h.r.R.Incr(ctx, k).Result()
	if err != nil {
		return 0
	}
	_ = h.r.R.Expire(ctx, k, 30*time.Minute).Err()
	return int(n)
}

// QuarantineStore is the durable side the Quarantiner drives (status change +
// event emission).
type QuarantineStore interface {
	SetVersionStatus(ctx context.Context, toolID, version, status string, deprecationEndsAt *time.Time, envs []events.Envelope) error
	InsertAudit(ctx context.Context, env events.Envelope) error
}

// Quarantiner detects SLA breaches and, after a configurable number of
// consecutive breach evaluations, emits tool.sla_breached and (when
// auto-quarantine is enabled) moves the version to quarantined — which the
// pipeline then treats like killed (TPL-FR-051/AC-10).
type Quarantiner struct {
	Health    *HealthStore
	Store     QuarantineStore
	Threshold int // consecutive breach evaluations (default 10 minutes @ 1/min)
}

// Evaluate compares current rolling health against the declared SLA and advances
// the breach streak. On the first evaluation that crosses the threshold it emits
// tool.sla_breached; with autoQuarantine it also quarantines the version.
// Returns (breached, quarantined).
func (q *Quarantiner) Evaluate(ctx context.Context, toolID, version string, sla domain.DeclaredSLA, autoQuarantine bool) (bool, bool, error) {
	threshold := q.Threshold
	if threshold <= 0 {
		threshold = 10
	}
	snap, err := q.Health.Snapshot(ctx, toolID, version)
	if err != nil {
		return false, false, err
	}
	breached := (sla.P95MS > 0 && snap.P95MS > sla.P95MS) ||
		(sla.ErrorRatePct > 0 && snap.ErrorRatePct > sla.ErrorRatePct)
	streak := q.Health.tickBreach(ctx, toolID, version, breached)
	if !breached || streak < threshold {
		return breached, false, nil
	}
	// Threshold crossed: emit sla_breached (+ quarantine if enabled).
	base := events.NewEnvelope(events.TopicToolEvents, events.EvToolSLABreached, domain.PlatformTenant,
		domain.Actor{Type: "service", ID: "mcp-gateway"}, nil,
		domain.ToolURN("platform", toolID, version), "",
		map[string]any{"tool_id": toolID, "version": version, "p95_ms": snap.P95MS, "error_rate_pct": snap.ErrorRatePct, "auto_quarantine": autoQuarantine})
	if autoQuarantine {
		quarantineEv := events.NewEnvelope(events.TopicToolEvents, events.EvToolQuarantined, domain.PlatformTenant,
			domain.Actor{Type: "service", ID: "mcp-gateway"}, nil,
			domain.ToolURN("platform", toolID, version), "",
			map[string]any{"tool_id": toolID, "version": version, "reason": "sla_breach_auto_quarantine"})
		if err := q.Store.SetVersionStatus(ctx, toolID, version, domain.StatusQuarantined, nil, []events.Envelope{base, quarantineEv}); err != nil {
			return true, false, err
		}
		return true, true, nil
	}
	if err := q.Store.InsertAudit(ctx, base); err != nil {
		return true, false, err
	}
	return true, false, nil
}
