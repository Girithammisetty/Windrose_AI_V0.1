// Package domain holds audit-service's pure logic (BRD 18): the master event
// envelope, canonical-JSON payload digests, the per-tenant-per-day hash chain,
// the PII policy gate, resource-URN parsing, action names and the error
// envelope. Everything here is deterministic and unit-testable with no infra.
package domain

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
)

// Actor identifies who caused an event (MASTER-FR-031/041).
type Actor struct {
	Type string `json:"type"` // user | service | agent | platform
	ID   string `json:"id"`
}

// ViaAgent carries dual attribution for OBO actions (MASTER-FR-041).
type ViaAgent struct {
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Envelope is the platform event envelope (MASTER-FR-031) as consumed off any
// `<ctx>.events.v1` / `ai.*` topic. Mirrors go-common event.Envelope on the
// wire so audit-service can decode every producer's messages.
type Envelope struct {
	EventID     uuid.UUID      `json:"event_id"`
	EventType   string         `json:"event_type"`
	TenantID    uuid.UUID      `json:"tenant_id"`
	Actor       Actor          `json:"actor"`
	ViaAgent    *ViaAgent      `json:"via_agent"`
	ResourceURN string         `json:"resource_urn"`
	OccurredAt  time.Time      `json:"occurred_at"`
	TraceID     string         `json:"trace_id"`
	Payload     map[string]any `json:"payload"`
}

// ValidateEnvelope enforces the master envelope contract (AUD-FR-002). A
// returned error means the event routes to the DLQ with reason ENVELOPE_INVALID.
func ValidateEnvelope(e Envelope) error {
	var missing []string
	if e.EventID == uuid.Nil {
		missing = append(missing, "event_id")
	}
	if strings.TrimSpace(e.EventType) == "" {
		missing = append(missing, "event_type")
	}
	if e.TenantID == uuid.Nil {
		missing = append(missing, "tenant_id")
	}
	if strings.TrimSpace(e.Actor.Type) == "" || strings.TrimSpace(e.Actor.ID) == "" {
		missing = append(missing, "actor")
	}
	if e.OccurredAt.IsZero() {
		missing = append(missing, "occurred_at")
	}
	if len(missing) > 0 {
		return fmt.Errorf("%s: missing/invalid %s", ReasonEnvelopeInvalid, strings.Join(missing, ","))
	}
	switch e.Actor.Type {
	case "user", "service", "agent", "platform":
	default:
		return fmt.Errorf("%s: actor.type %q not allowed", ReasonEnvelopeInvalid, e.Actor.Type)
	}
	return nil
}

// DLQ reasons (AUD-FR-002/006/070, BR-7).
const (
	ReasonEnvelopeInvalid = "ENVELOPE_INVALID"
	ReasonPayloadDecode   = "PAYLOAD_DECODE"
	ReasonDigestConflict  = "DIGEST_CONFLICT"
)

// CanonicalJSON returns a deterministic JSON encoding of v: object keys sorted
// recursively, no insignificant whitespace, HTML escaping disabled. This is the
// bytes the payload digest is taken over (AUD-FR-003) so the digest is stable
// across producers regardless of key order.
func CanonicalJSON(v any) []byte {
	c := canonicalize(v)
	b, err := marshalNoEscape(c)
	if err != nil {
		return []byte("null")
	}
	return b
}

func marshalNoEscape(v any) ([]byte, error) {
	var sb strings.Builder
	enc := json.NewEncoder(&sb)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return []byte(strings.TrimRight(sb.String(), "\n")), nil
}

// canonicalize recursively normalizes maps into ordered structures so encoding
// is deterministic.
func canonicalize(v any) any {
	switch t := v.(type) {
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out := make(orderedMap, 0, len(keys))
		for _, k := range keys {
			out = append(out, kv{K: k, V: canonicalize(t[k])})
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i := range t {
			out[i] = canonicalize(t[i])
		}
		return out
	default:
		return v
	}
}

type kv struct {
	K string
	V any
}
type orderedMap []kv

// MarshalJSON writes the ordered map preserving insertion (sorted) order.
func (m orderedMap) MarshalJSON() ([]byte, error) {
	var sb strings.Builder
	sb.WriteByte('{')
	for i, e := range m {
		if i > 0 {
			sb.WriteByte(',')
		}
		kb, err := marshalNoEscape(e.K)
		if err != nil {
			return nil, err
		}
		sb.Write(kb)
		sb.WriteByte(':')
		vb, err := marshalNoEscape(e.V)
		if err != nil {
			return nil, err
		}
		sb.Write(vb)
	}
	sb.WriteByte('}')
	return []byte(sb.String()), nil
}

// PayloadDigest is SHA-256 (hex) of the canonical-JSON payload (AUD-FR-003).
func PayloadDigest(payload map[string]any) string {
	sum := sha256.Sum256(CanonicalJSON(payload))
	return hex.EncodeToString(sum[:])
}

// SHA256Hex is a helper for arbitrary bytes.
func SHA256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// GenesisHash is day-1's prev hash for a tenant's chain: SHA-256(tenant||date)
// (AUD-FR-050). date is YYYY-MM-DD.
func GenesisHash(tenant uuid.UUID, date string) string {
	return SHA256Hex([]byte(tenant.String() + "|" + date))
}

// ChainHash computes the per-event chain hash (AUD-FR-050):
// SHA-256(prev_chain_hash || event_id || payload_digest || occurred_at_rfc3339nanoUTC).
func ChainHash(prev string, eventID uuid.UUID, payloadDigest string, occurredAt time.Time) string {
	var sb strings.Builder
	sb.WriteString(prev)
	sb.WriteString("|")
	sb.WriteString(eventID.String())
	sb.WriteString("|")
	sb.WriteString(payloadDigest)
	sb.WriteString("|")
	sb.WriteString(occurredAt.UTC().Format(time.RFC3339Nano))
	return SHA256Hex([]byte(sb.String()))
}

// URNParts are the service/type/id decoded from a resource URN
// (MASTER-FR-013: wr:<tenant>:<service>:<resource_type>/<resource_id>).
type URNParts struct {
	Tenant   string
	Service  string
	Type     string
	ID       string
}

// ParseURN best-effort decodes a resource URN into its parts; unknown/empty
// components come back empty (search still stores the raw URN).
func ParseURN(urn string) URNParts {
	var p URNParts
	if urn == "" {
		return p
	}
	seg := strings.SplitN(urn, ":", 4)
	if len(seg) < 4 || seg[0] != "wr" {
		return p
	}
	p.Tenant = seg[1]
	p.Service = seg[2]
	rest := seg[3]
	if i := strings.IndexByte(rest, '/'); i >= 0 {
		p.Type = rest[:i]
		p.ID = rest[i+1:]
	} else {
		p.Type = rest
	}
	return p
}

// Action derives the `<service>.<resource>.<verb>` action name for an event
// when present, from event_type. Best-effort: returns "" when not derivable.
func ActionFromEventType(service, eventType string) string {
	// event_type is `<resource>.<verb>` per MASTER-FR-035; action prefixes the service.
	if eventType == "" {
		return ""
	}
	dot := strings.IndexByte(eventType, '.')
	if dot < 0 {
		return ""
	}
	if service == "" {
		return eventType
	}
	return service + "." + eventType
}
