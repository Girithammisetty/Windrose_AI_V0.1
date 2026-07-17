package domain

import (
	"fmt"
	"regexp"
	"strings"
)

// PII policy (AUD-FR-070/071, MASTER-FR-042). Payloads carry no raw PII values:
// producers reference subjects by URN + field name. This gate is defense in
// depth. Event types on the PII-clean allowlist (Schema-Registry `pii=false`
// guarantee, cached) skip scanning; everything else is pattern-scanned. A hit
// drops the payload body (digest is still kept) and the caller emits
// audit.pii_rejected naming the producing service.

// MaxPayloadJSONBytes caps inline payload storage (AUD-FR-003: ≤ 64KB).
const MaxPayloadJSONBytes = 64 * 1024

var (
	reEmail  = regexp.MustCompile(`[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}`)
	rePhone  = regexp.MustCompile(`(?:\+?\d[ \-]?){9,14}\d`)
	reSSN    = regexp.MustCompile(`\b\d{3}-\d{2}-\d{4}\b`)
	reIBAN   = regexp.MustCompile(`\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b`)
	reSecret = regexp.MustCompile(`(?i)(secret|password|passwd|api[_\-]?key|token|bearer)["'\s:=]+[A-Za-z0-9/+_\-]{12,}`)
)

// PIIResult is the outcome of the ingest PII gate.
type PIIResult struct {
	// Clean is true when the payload may be stored inline as payload_json.
	Clean bool
	// Reason names why a payload was rejected (empty when Clean or size-only).
	Reason string
	// TooLarge is true when the payload exceeds MaxPayloadJSONBytes (body
	// withheld, but not a PII violation).
	TooLarge bool
}

// PIIGate applies the policy. cleanAllow is the set of event types the Schema
// Registry guarantees PII-free (pii=false); those are trusted without scanning
// but still size-capped. All other types are pattern-scanned (BR-5).
func PIIGate(eventType string, canonicalPayload []byte, cleanAllow map[string]bool) PIIResult {
	if len(canonicalPayload) > MaxPayloadJSONBytes {
		return PIIResult{Clean: false, TooLarge: true}
	}
	if cleanAllow[eventType] {
		return PIIResult{Clean: true}
	}
	if reason, hit := scanPII(canonicalPayload); hit {
		return PIIResult{Clean: false, Reason: reason}
	}
	return PIIResult{Clean: true}
}

// scanPII returns the first matching PII pattern class, if any.
func scanPII(b []byte) (string, bool) {
	s := string(b)
	switch {
	case reSSN.MatchString(s):
		return "national_id", true
	case reEmail.MatchString(s):
		return "email", true
	case reIBAN.MatchString(s):
		return "iban", true
	case reSecret.MatchString(s):
		return "secret", true
	case rePhone.MatchString(stripStructural(s)):
		return "phone", true
	}
	return "", false
}

// stripStructural removes JSON structural noise so long numeric ids (uuids
// already excluded) don't false-positive the phone matcher on separators.
func stripStructural(s string) string {
	r := strings.NewReplacer(`"`, " ", `,`, " ", `:`, " ", `{`, " ", `}`, " ", `[`, " ", `]`, " ")
	return r.Replace(s)
}

// PayloadRef builds the source pointer stored when a body is withheld
// (AUD-FR-003): topic/partition/offset.
func PayloadRef(topic string, partition int, offset int64) string {
	return fmt.Sprintf("%s/%d/%d", topic, partition, offset)
}
