// Package fanout is the heart of realtime-hub: the pod-local connection
// registry, per-connection backpressure with the slow-client drop policy
// (RTH-FR-030), sticky-less horizontal scale-out over real Redis pub/sub
// (RTH-FR-041), the Redis Streams replay buffer with Last-Event-ID resume
// (RTH-FR-031), the leader lease for replay writes (RTH-FR-042), and per
// tenant/user connection caps (RTH-FR-040). There is no in-memory-only fan-out
// path: every cross-pod hop is real Redis. In-memory doubles exist only in
// unit tests.
package fanout

import "encoding/json"

// Event is one deliverable event on a topic. ID is the producer event_id
// (uuidv7) so ordering/resume is stable end-to-end (RTH-FR-004). Control/
// heartbeat messages carry an empty ID and never advance the resume cursor.
type Event struct {
	ID    string          `json:"id"`
	Topic string          `json:"topic"`
	Data  json.RawMessage `json:"data"`
	Chat  bool            `json:"chat"`
}

// Control event types (wire "control" event data, §5).
const (
	CtrlGap          = "gap"
	CtrlReset        = "reset"
	CtrlReconnect    = "reconnect"
	CtrlRevoked      = "revoked"
	CtrlReplaced     = "replaced"
	CtrlTokenRefresh = "token_refresh"
	CtrlSubscribed   = "subscribed"
	CtrlError        = "error"
	CtrlHeartbeat    = "heartbeat"
)

// WS/SSE close codes (§5).
const (
	CloseTokenExpired   = 4401
	CloseAllForbidden   = 4403
	CloseTooSlow        = 4409
	CloseServerDrain    = 1012
)

// Sink is the wire writer for one connection — SSE writes id/event/data frames
// and ": hb" comments; WebSocket writes JSON frames. Implementations must be
// safe for use from a single writer goroutine.
type Sink interface {
	// WriteEvent writes a data event. id may be empty (control events).
	WriteEvent(id, event string, data []byte) error
	// WriteHeartbeat writes the 15s keepalive (SSE comment / WS pong).
	WriteHeartbeat(degraded bool) error
	// Close terminates the connection with a close code + reason.
	Close(code int, reason string) error
}
