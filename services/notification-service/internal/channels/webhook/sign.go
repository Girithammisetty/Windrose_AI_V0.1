// Package webhook is the outbound webhook channel: HMAC-SHA256 signing with
// dual-secret rotation, an SSRF guard resolved at send time, real HTTP POST of
// the master event envelope, a retry/backoff schedule, a per-endpoint circuit
// breaker, and dead-lettering after the schedule is exhausted (NOTIF-FR-022/023).
package webhook

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strconv"
	"strings"

	"github.com/windrose-ai/notification-service/internal/domain"
)

// SignatureHeader builds the X-Windrose-Signature value for a body at a given
// unix timestamp, one `v1=<hex>` entry per active secret (two during the 24h
// rotation overlap, AC-6). The signed string is `timestamp . body`.
func SignatureHeader(secrets []domain.WebhookSecret, timestamp int64, body []byte) string {
	var parts []string
	for _, s := range secrets {
		parts = append(parts, "v1="+sign(s.Secret, timestamp, body))
	}
	return strings.Join(parts, ",")
}

// sign computes hex(hmac_sha256(secret, timestamp + "." + body)).
func sign(secret string, timestamp int64, body []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(strconv.FormatInt(timestamp, 10)))
	mac.Write([]byte("."))
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil))
}

// Verify checks a signature header against a secret with the ±toleranceSec
// replay guard (consumer-side helper; used by tests and the consumer guide,
// AC-4). now and timestamp are unix seconds.
func Verify(header, secret string, timestamp, now, toleranceSec int64, body []byte) bool {
	if now-timestamp > toleranceSec || timestamp-now > toleranceSec {
		return false
	}
	want := sign(secret, timestamp, body)
	for _, part := range strings.Split(header, ",") {
		part = strings.TrimSpace(part)
		got := strings.TrimPrefix(part, "v1=")
		if hmac.Equal([]byte(got), []byte(want)) {
			return true
		}
	}
	return false
}

// Header names (NOTIF-FR-022).
const (
	HeaderSignature = "X-Windrose-Signature"
	HeaderTimestamp = "X-Windrose-Timestamp"
	HeaderEventID   = "X-Windrose-Event-Id"
	HeaderEventType = "X-Windrose-Event-Type"
)

// ChallengeBody is the registration handshake body (NOTIF-FR-022).
func ChallengeBody(challenge string) string {
	return fmt.Sprintf(`{"type":"endpoint.verify","challenge":%q}`, challenge)
}
