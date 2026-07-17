// Package topics implements the realtime-hub topic model (RTH-FR-003): the four
// topic schemes, their grammar/validation, per-topic-class QoS (RTH-FR-034),
// and the tenant-scoped internal key construction (BR-3). Topic strings that a
// client sends are always re-keyed as "<tenant-from-JWT>/<topic>" so no
// client-supplied tenant ever reaches a subscription key.
package topics

import (
	"errors"
	"fmt"
	"strings"
	"time"
)

// Scheme is one of the four topic schemes (RTH-FR-003).
type Scheme string

const (
	SchemeRunStatus     Scheme = "run-status"     // pipeline/ingestion/inference/operation runs
	SchemeChat          Scheme = "chat"           // agent chat token stream (by session)
	SchemeAgentRun      Scheme = "agent_run"      // agent-run stream (token/tool_call/proposal/run_completed), by run id
	SchemeNotifications Scheme = "notifications"  // in-app pushes
	SchemeProposal      Scheme = "proposal"       // proposal decision updates
)

// ErrInvalidTopic is returned by Parse for an unknown scheme or malformed topic
// (mapped to the wire error code INVALID_TOPIC).
var ErrInvalidTopic = errors.New("INVALID_TOPIC")

// Overflow policies (RTH-FR-030/034).
const (
	OverflowGap        = "gap"        // drop-oldest + emit a gap control event (state topics)
	OverflowDisconnect = "disconnect" // close 4409 TOO_SLOW (chat)
)

// QoS is the per-topic-class quality-of-service profile (RTH-FR-034).
type QoS struct {
	Class        Scheme
	Overflow     string
	ReplayMax    int64         // max ring-buffer entries
	ReplayWindow time.Duration // max ring-buffer age
}

// Topic is a parsed, validated topic reference.
type Topic struct {
	Raw    string // the exact client string, e.g. run-status:wr:t-42:pipeline:run/pr-881
	Scheme Scheme
	Ident  string // everything after the first ":" — a URN, session id, user id, or proposal id
}

// Parse validates raw and splits it into scheme + identifier. Only the first
// ":" is a delimiter, so run-status URNs (which themselves contain ":") survive.
func Parse(raw string) (Topic, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return Topic{}, ErrInvalidTopic
	}
	i := strings.IndexByte(raw, ':')
	if i <= 0 || i == len(raw)-1 {
		return Topic{}, ErrInvalidTopic
	}
	scheme := Scheme(raw[:i])
	ident := raw[i+1:]
	switch scheme {
	case SchemeRunStatus, SchemeChat, SchemeAgentRun, SchemeNotifications, SchemeProposal:
	default:
		return Topic{}, ErrInvalidTopic
	}
	if strings.TrimSpace(ident) == "" {
		return Topic{}, ErrInvalidTopic
	}
	// run-status idents are URNs; enforce the URN shape so a bare id can't be
	// smuggled past the cross-tenant check (BR-3).
	if scheme == SchemeRunStatus && !strings.HasPrefix(ident, "wr:") {
		return Topic{}, ErrInvalidTopic
	}
	return Topic{Raw: raw, Scheme: scheme, Ident: ident}, nil
}

// QoS returns the topic-class QoS profile (RTH-FR-034).
func (t Topic) QoS() QoS {
	if t.Scheme == SchemeChat {
		return QoS{Class: SchemeChat, Overflow: OverflowDisconnect, ReplayMax: 1000, ReplayWindow: 10 * time.Minute}
	}
	// agent_run is a token stream; keep the replay ring (gap overflow) so a client
	// that subscribes a beat after the run starts still receives the early tokens.
	return QoS{Class: t.Scheme, Overflow: OverflowGap, ReplayMax: 1000, ReplayWindow: 10 * time.Minute}
}

// IsChat reports whether the topic uses the chat QoS (disconnect-on-overflow).
func (t Topic) IsChat() bool { return t.Scheme == SchemeChat }

// URNTenant extracts the tenant segment from a run-status URN ident
// (wr:<tenant>:<service>:<type>/<id>). Returns "" when the ident is not a URN.
func URNTenant(urn string) string {
	parts := strings.SplitN(urn, ":", 3)
	if len(parts) < 3 || parts[0] != "wr" {
		return ""
	}
	return parts[1]
}

// Key is the tenant-scoped internal subscription key (BR-3): "<tenant>/<raw>".
// The tenant always comes from the verified JWT, never the client string.
func Key(tenant, raw string) string { return tenant + "/" + raw }

// ProposalURN builds the resource URN a proposal topic authorizes against
// (RTH-FR-003: realtime.proposal.read on the proposal URN).
func ProposalURN(tenant, proposalID string) string {
	return fmt.Sprintf("wr:%s:ai:proposal/%s", tenant, proposalID)
}
