// Package meta emits audit-service's own meta-audit events to audit.events.v1
// (BRD 18 §6): audit.searched, audit.export_sealed, audit.integrity_violation,
// audit.pii_rejected, audit.dlq_redriven. audit-service does NOT re-emit the
// events it consumes — only these meta events (auditors are audited, AC-10).
package meta

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"log/slog"

	"github.com/google/uuid"

	gcevent "github.com/windrose-ai/go-common/event"
)

// Topic is audit-service's meta-audit topic.
const Topic = "audit.events.v1"

// Publisher publishes an envelope to a topic (real go-common Kafka producer).
type Publisher interface {
	Publish(ctx context.Context, topic string, env gcevent.Envelope) error
}

// Emitter builds + publishes meta events.
type Emitter struct {
	Pub Publisher
	Log *slog.Logger
}

// New builds an Emitter.
func New(pub Publisher) *Emitter { return &Emitter{Pub: pub, Log: slog.Default()} }

func (e *Emitter) emit(ctx context.Context, eventType string, tenant uuid.UUID, actor gcevent.Actor, urn string, payload map[string]any) {
	if e == nil || e.Pub == nil {
		return
	}
	env := gcevent.New(eventType, tenant, actor, urn, "", payload)
	if err := e.Pub.Publish(ctx, Topic, env); err != nil {
		if e.Log != nil {
			e.Log.Warn("meta event publish failed", "event_type", eventType, "err", err)
		}
	}
}

// FilterDigest is the stable digest of a search/export filter (AUD-FR-032):
// auditors are audited, but the filter itself is not stored verbatim.
func FilterDigest(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])
}

// Searched records that an admin ran a search/export (AUD-FR-032, AC-10).
func (e *Emitter) Searched(ctx context.Context, tenant uuid.UUID, actorID, filterDigest string, breakglass bool) {
	e.emit(ctx, "audit.searched", tenant, gcevent.Actor{Type: "user", ID: actorID},
		"wr:"+tenant.String()+":audit:audit/search",
		map[string]any{"filter_digest": filterDigest, "breakglass": breakglass})
}

// ExportSealed records a sealed WORM batch (AUD-FR-020/021, §6).
func (e *Emitter) ExportSealed(ctx context.Context, tenant uuid.UUID, date, manifestHash string) {
	e.emit(ctx, "audit.export_sealed", tenant, gcevent.Actor{Type: "service", ID: "audit-service"},
		"wr:"+tenant.String()+":audit:export/"+date,
		map[string]any{"date": date, "manifest_sha256": manifestHash})
}

// IntegrityViolation records a failed self-verification (AUD-FR-051/052, US-12).
func (e *Emitter) IntegrityViolation(ctx context.Context, tenant uuid.UUID, date string, firstMismatchSeq uint64) {
	e.emit(ctx, "audit.integrity_violation", tenant, gcevent.Actor{Type: "service", ID: "audit-service"},
		"wr:"+tenant.String()+":audit:chain/"+date,
		map[string]any{"date": date, "first_mismatch_seq": firstMismatchSeq})
}

// PIIRejected records that a payload body was dropped for a PII hit (AUD-FR-070,
// AC-3), naming the producing service.
func (e *Emitter) PIIRejected(ctx context.Context, tenant uuid.UUID, producingService, eventType, reason string) {
	e.emit(ctx, "audit.pii_rejected", tenant, gcevent.Actor{Type: "service", ID: "audit-service"},
		"wr:"+tenant.String()+":audit:pii/"+eventType,
		map[string]any{"source_service": producingService, "event_type": eventType, "pii_class": reason})
}

// DLQRedriven records a redrive (AUD-FR-006, AC-15).
func (e *Emitter) DLQRedriven(ctx context.Context, tenant uuid.UUID, actorID, topic string, count int) {
	e.emit(ctx, "audit.dlq_redriven", tenant, gcevent.Actor{Type: "user", ID: actorID},
		"wr:"+tenant.String()+":audit:dlq/"+topic,
		map[string]any{"topic": topic, "count": count})
}
