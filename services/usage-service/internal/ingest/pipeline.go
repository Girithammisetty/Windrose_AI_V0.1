package ingest

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/usage-service/internal/domain"
)

// RawStore persists raw meter records idempotently and evaluates budgets in the
// same transaction (implemented by store.PG). InsertRaw returns the count of
// rows actually inserted (0 on a full duplicate — USG-FR-011).
type RawStore interface {
	InsertRaw(ctx context.Context, recs []domain.MeterRecord) (inserted int, err error)
}

// BudgetEvaluator is called after ingest to (re)evaluate budgets affected by
// the just-ingested meter keys/scopes (USG-FR-031). Errors are logged, not
// fatal — raw is the source of truth and a periodic sweep also evaluates.
type BudgetEvaluator interface {
	EvaluateAfterIngest(ctx context.Context, tenant uuid.UUID, meterKeys map[string]bool) error
}

// Metrics is the observability surface (USG-FR-012/015 counters).
type Metrics interface {
	IncUnmapped(eventType string)
	IncIngested(meterKey string, n int)
	ObserveIngestLag(seconds float64)
}

// Pipeline is the idempotent ingest pipeline (BRD 17 §6 handler chain):
// envelope -> mapping lookup -> raw insert (unique-constraint dedup) ->
// budget evaluation. Redis event_id dedup is applied one layer up by the
// go-common consumer group; the unique constraint makes replays no-ops even
// when Redis is unavailable (AC-2, AC-14).
type Pipeline struct {
	idx     index
	raw     RawStore
	budgets BudgetEvaluator
	metrics Metrics
}

// NewPipeline builds a Pipeline over the given mapping catalog.
func NewPipeline(mappings []Mapping, raw RawStore, budgets BudgetEvaluator, metrics Metrics) *Pipeline {
	return &Pipeline{idx: newIndex(mappings), raw: raw, budgets: budgets, metrics: metrics}
}

// Handle processes one master envelope. Returning an error triggers the
// consumer group's retry/DLQ path (MASTER-FR-033); an unmapped event is NOT an
// error (USG-FR-015) — it increments the unmapped counter and returns nil.
func (p *Pipeline) Handle(ctx context.Context, env gcevent.Envelope) error {
	maps := p.idx.lookup(env.EventType)
	if len(maps) == 0 {
		if p.metrics != nil {
			p.metrics.IncUnmapped(env.EventType)
		}
		return nil
	}

	recs := make([]domain.MeterRecord, 0, len(maps))
	meterKeys := map[string]bool{}
	for _, m := range maps {
		if m.Filter != nil && !m.Filter(env.Payload) {
			continue
		}
		qty, ok := quantity(m, env.Payload)
		if !ok {
			continue // missing quantity: skip this meter, not a poison message
		}
		if qty == 0 {
			continue // zero-quantity rows (e.g. llm_output_tokens=0) are noise
		}
		// The raw unique key includes time (the partition key), so the record
		// time must be DETERMINISTIC for a given event or a replay of an event
		// missing occurred_at would bypass the constraint. Use the envelope
		// occurred_at; when absent, derive a stable time from the uuidv7 event
		// id (which encodes its creation time) so replays collide correctly.
		recTime := eventTime(env.OccurredAt, env.EventID)
		rec := domain.MeterRecord{
			Time:     recTime,
			TenantID: env.TenantID,
			MeterKey: m.MeterKey,
			Quantity: qty,
			Cloud:    dimStr(env.Payload, m.DimPaths["cloud"], "aws"),
			EventID:  env.EventID,
			Late:     isLate(recTime),
		}
		rec.WorkspaceID = dimPtr(env.Payload, m.DimPaths["workspace_id"])
		rec.UserID = dimPtr(env.Payload, m.DimPaths["user_id"])
		rec.AgentID = dimPtr(env.Payload, m.DimPaths["agent_id"])
		rec.Model = dimPtr(env.Payload, m.DimPaths["model"])
		rec.ResourceURN = dimPtr(env.Payload, m.DimPaths["resource_urn"])
		recs = append(recs, rec)
		meterKeys[m.MeterKey] = true
	}
	if len(recs) == 0 {
		return nil
	}

	inserted, err := p.raw.InsertRaw(ctx, recs)
	if err != nil {
		return err // retryable
	}
	if p.metrics != nil {
		for k := range meterKeys {
			p.metrics.IncIngested(k, 1)
		}
		p.metrics.ObserveIngestLag(time.Since(recs[0].Time).Seconds())
	}
	// Only re-evaluate budgets when something actually landed (replays that
	// insert nothing must not re-fire threshold events — AC-2/AC-3).
	if inserted > 0 && p.budgets != nil {
		if err := p.budgets.EvaluateAfterIngest(ctx, env.TenantID, meterKeys); err != nil {
			// Non-fatal: raw is durable; the periodic sweep re-evaluates.
			return nil
		}
	}
	return nil
}

// eventTime returns a deterministic record time for an event: the envelope
// occurred_at when set, else the timestamp embedded in the uuidv7 event id, else
// the Unix epoch (a stable sentinel). Determinism keeps replays idempotent
// against the raw unique constraint even when Redis dedup is bypassed.
func eventTime(occurredAt time.Time, eventID uuid.UUID) time.Time {
	if !occurredAt.IsZero() {
		return occurredAt
	}
	if eventID.Version() == 7 {
		sec, nsec := eventID.Time().UnixTime()
		return time.Unix(sec, nsec).UTC()
	}
	return time.Unix(0, 0).UTC()
}

func isLate(t time.Time) bool {
	if t.IsZero() {
		return false
	}
	return time.Since(t) > time.Hour
}

// quantity extracts the metered quantity for a mapping.
func quantity(m Mapping, payload map[string]any) (float64, bool) {
	if m.QuantityPath == "" {
		return m.QuantityConst, true
	}
	v, ok := getPath(payload, m.QuantityPath)
	if !ok {
		return 0, false
	}
	return toFloat(v)
}

// getPath resolves a dotted path against a nested map[string]any payload.
func getPath(payload map[string]any, path string) (any, bool) {
	if path == "" {
		return nil, false
	}
	parts := strings.Split(path, ".")
	var cur any = payload
	for _, part := range parts {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, false
		}
		cur, ok = m[part]
		if !ok {
			return nil, false
		}
	}
	return cur, true
}

func toFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case string:
		f, err := strconv.ParseFloat(n, 64)
		return f, err == nil
	}
	return 0, false
}

// dimStr extracts a string dimension with a default.
func dimStr(payload map[string]any, path, def string) string {
	if path == "" {
		return def
	}
	if v, ok := getPath(payload, path); ok {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	return def
}

// dimPtr extracts an optional string dimension (nil when absent/empty —
// unknown dims stored as NULL, never dropped, USG-FR-002).
func dimPtr(payload map[string]any, path string) *string {
	if path == "" {
		return nil
	}
	v, ok := getPath(payload, path)
	if !ok {
		return nil
	}
	s, ok := v.(string)
	if !ok || s == "" {
		return nil
	}
	return &s
}
