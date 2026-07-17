// Package siemexport builds and publishes audit-service's external SIEM
// export contract, `audit.export.v1` (Phase 3, docs/design/siem-export.md).
//
// This is a normalized, STABLE subset of the internal domain.Record shape
// (see internal/domain/record.go) — the same row already written to
// ClickHouse/WORM — republished on its own versioned Kafka topic so a
// customer's SIEM (or a generic Kafka consumer / Kafka-Connect connector) can
// subscribe directly instead of polling the search API.
//
// ADDITIVE ONLY: publishing here is a pure side effect that runs strictly
// after a record has already been chained and inserted into ClickHouse
// (ingest/processor.go). A publish failure is logged and swallowed — it never
// fails ingest, never touches the hash chain, and never affects WORM export
// or retention. See docs/design/siem-export.md for the full schema reference,
// integration paths and the versioning/deprecation policy.
package siemexport

import (
	"context"
	"log/slog"
	"strings"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/domain"
	gcevent "github.com/windrose-ai/go-common/event"
)

// Topic is the stable, versioned external SIEM-export Kafka topic. Additive
// fields may be introduced within v1; a breaking change requires a new
// `audit.export.v2` topic (docs/design/siem-export.md, "Versioning policy").
const Topic = "audit.export.v1"

// SchemaVersion is stamped into every exported event's payload so a consumer
// can detect (rather than assume) which revision of the v1 contract it is
// reading.
const SchemaVersion = "1.0"

// Actor mirrors domain.Actor for the external contract.
type Actor struct {
	Type string `json:"type"` // user | service | agent | platform
	ID   string `json:"id"`
}

// ViaAgent mirrors domain.ViaAgent (dual attribution, MASTER-FR-041) for the
// external contract.
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Event is the audit.export.v1 record: everything is carried in the
// gcevent.Envelope's Payload (this struct is marshaled into it), while the
// envelope's own top-level fields (event_id, tenant_id, actor, resource_urn,
// occurred_at, trace_id) are populated from the same Record so a raw-Kafka
// consumer and a webhook consumer receive byte-identical JSON (both transports
// send the marshaled gcevent.Envelope — see Event.Envelope below).
type Event struct {
	SchemaVersion   string `json:"schema_version"`
	SourceEventID   string `json:"source_event_id"`   // the original internal event_id this export was derived from
	SourceEventType string `json:"source_event_type"` // the original internal event_type (e.g. "case.decision.made")
	Action          string `json:"action"`            // "<service>.<verb>" (domain.ActionFromEventType)
	ResourceService string `json:"resource_service,omitempty"`
	ResourceType    string `json:"resource_type,omitempty"`
	Outcome         string `json:"outcome"`
	PayloadDigest   string `json:"payload_digest"` // sha256 hex of the original payload — integrity reference, no raw payload is exported
	SourceTopic     string `json:"source_topic,omitempty"`
	ChainDate       string `json:"chain_date,omitempty"`
	ChainSeq        uint64 `json:"chain_seq,omitempty"`
}

// FromRecord builds the external export event from an internal audit Record
// — the exact row that was just chained and inserted into ClickHouse. It is
// read-only: never mutates rec.
func FromRecord(rec domain.Record) Event {
	return Event{
		SchemaVersion:   SchemaVersion,
		SourceEventID:   rec.EventID.String(),
		SourceEventType: rec.EventType,
		Action:          rec.Action,
		ResourceService: rec.ResourceService,
		ResourceType:    rec.ResourceType,
		Outcome:         deriveOutcome(rec.EventType),
		PayloadDigest:   rec.PayloadDigest,
		SourceTopic:     rec.SourceTopic,
		ChainDate:       rec.ChainDate,
		ChainSeq:        rec.ChainSeq,
	}
}

// exportEventID derives a STABLE event id for the exported envelope from the
// source record's own event_id — same input always yields the same output
// (idempotent across DLQ redrives/reprocessing), but DISTINCT from it.
//
// This distinction matters: several platform services (e.g.
// notification-service) run ONE Kafka consumer group across every topic they
// subscribe to and dedup on a GLOBAL "evt:dedup:<event_id>" key that is not
// topic-scoped. Reusing the source event_id verbatim here would collide with
// that keyspace whenever a consumer subscribes to both the source domain
// topic (e.g. case.events.v1) AND audit.export.v1 — the second arrival would
// be silently treated as an already-seen duplicate and dropped before ever
// reaching the handler. Deriving a distinct id (namespaced by Topic) avoids
// that cross-topic collision entirely; SourceEventID in the payload preserves
// the correlation back to the original record for SIEM consumers.
func exportEventID(sourceEventID uuid.UUID) uuid.UUID {
	return uuid.NewSHA1(uuid.NameSpaceOID, []byte(Topic+":"+sourceEventID.String()))
}

// Envelope wraps rec + its derived Event into the platform's standard
// gcevent.Envelope (matching the convention every other Windrose service
// publishes with — MASTER-FR-031). tenant_id, actor, resource_urn,
// occurred_at and trace_id are preserved at the envelope's top level so a
// consumer gets accurate attribution without unpacking the payload.
// envelope.EventID is a distinct, deterministically-derived id (see
// exportEventID) — NOT the source record's event_id, to avoid a cross-topic
// Kafka-dedup collision; the original id is preserved as SourceEventID in the
// payload. envelope.EventType is always the constant Topic name
// ("audit.export.v1") — NOT the original per-action event_type — so a single
// webhook subscription / registry mapping matches every exported record
// regardless of what kind of audit event produced it.
func Envelope(rec domain.Record) gcevent.Envelope {
	ev := FromRecord(rec)
	payload := map[string]any{
		"schema_version":    ev.SchemaVersion,
		"source_event_id":   ev.SourceEventID,
		"source_event_type": ev.SourceEventType,
		"action":            ev.Action,
		"resource_service":  ev.ResourceService,
		"resource_type":     ev.ResourceType,
		"outcome":           ev.Outcome,
		"payload_digest":    ev.PayloadDigest,
		"source_topic":      ev.SourceTopic,
		"chain_date":        ev.ChainDate,
		"chain_seq":         ev.ChainSeq,
	}
	env := gcevent.Envelope{
		EventID:     exportEventID(rec.EventID),
		EventType:   Topic,
		TenantID:    rec.TenantID,
		Actor:       gcevent.Actor{Type: rec.ActorType, ID: rec.ActorID},
		ResourceURN: rec.ResourceURN,
		OccurredAt:  rec.OccurredAt,
		TraceID:     rec.TraceID,
		Payload:     payload,
	}
	if rec.ViaAgentID != "" {
		env.ViaAgent = &gcevent.ViaAgent{AgentID: rec.ViaAgentID, Version: rec.ViaAgentVersion}
		payload["via_agent"] = map[string]any{"agent_id": rec.ViaAgentID, "version": rec.ViaAgentVersion}
	}
	if rec.OboUserID != "" {
		payload["obo_user_id"] = rec.OboUserID
	}
	return env
}

// deriveOutcome is a best-effort classification of an event's result from its
// event_type suffix — the same heuristic style as
// internal/compliance/compliance.go:decisionOutcome (used for the EU AI Act
// decision log), extended with a couple of generic verbs relevant to a
// security/audit outcome (denied/failed). It is intentionally best-effort:
// audit-service's internal Record has no dedicated outcome field today, so
// this derivation — not a stored column — is what the external contract
// exposes. See docs/design/siem-export.md for the full value set.
func deriveOutcome(eventType string) string {
	switch {
	case strings.Contains(eventType, "denied"):
		return "denied"
	case strings.Contains(eventType, "rejected"):
		return "rejected"
	case strings.Contains(eventType, "failed"):
		return "failed"
	case strings.Contains(eventType, "expired"):
		return "expired"
	case strings.Contains(eventType, "approved"),
		strings.Contains(eventType, "succeeded"),
		strings.Contains(eventType, "completed"),
		strings.Contains(eventType, "created"),
		strings.Contains(eventType, "updated"),
		strings.Contains(eventType, "deleted"):
		return "success"
	default:
		return "recorded"
	}
}

// Publisher publishes an envelope to a topic (satisfied by the real
// go-common Kafka *kafka.Producer already wired in cmd/server for DLQ + meta
// events — no new Kafka client is created for this sink).
type Publisher interface {
	Publish(ctx context.Context, topic string, env gcevent.Envelope) error
}

// Exporter publishes audit.export.v1 events. Nil-safe: a zero-value *Exporter
// (or one with a nil Pub) is a no-op, so wiring it is optional.
type Exporter struct {
	Pub Publisher
	Log *slog.Logger
}

// New builds an Exporter over a real Publisher.
func New(pub Publisher) *Exporter { return &Exporter{Pub: pub, Log: slog.Default()} }

func (x *Exporter) log() *slog.Logger {
	if x != nil && x.Log != nil {
		return x.Log
	}
	return slog.Default()
}

// Publish sends rec's normalized export event to Topic. Best-effort: this
// sink has zero bearing on ingest correctness, the hash chain, or WORM
// retention — a failure here is logged and swallowed, never returned, so it
// can never cause a retry/DLQ of the underlying domain event (which has
// already been durably chained + stored by the time this is called).
func (x *Exporter) Publish(ctx context.Context, rec domain.Record) {
	if x == nil || x.Pub == nil {
		return
	}
	env := Envelope(rec)
	if err := x.Pub.Publish(ctx, Topic, env); err != nil {
		x.log().Warn("siem export publish failed", "event_id", rec.EventID.String(), "event_type", rec.EventType, "err", err)
	}
}
