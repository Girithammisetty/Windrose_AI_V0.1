package domain

import "regexp"

// TopicSubscription is the config-driven regex subscription (AUD-FR-001,
// BR-11): every `<ctx>.events.v1` domain topic, the three `ai.*` topics and
// `security.*` audit emissions. Adding a new domain topic needs zero code
// change — the periodic metadata rescan picks it up (AC-13).
type TopicSubscription struct {
	re *regexp.Regexp
}

// DefaultSubscriptionPattern matches the consumed topic set. audit-service's
// own meta topic (audit.events.v1) is deliberately consumed too so meta events
// are themselves auditable, EXCEPT the DLQ topics (…dlq) which are excluded to
// avoid re-ingesting quarantined messages.
//
// ai.* alternation: tool_invoked, agent_run, proposal AND token_usage — the
// last now flows after wave 1 and must enter the immutable audit record like
// the other ai.* emissions (regression: it was previously excluded).
//
// The `security\..+` alternative matches no real topic today (there is no
// security.* producer yet); it is retained deliberately so that when a
// security emissions topic is introduced it is audited with zero code change
// (AUD-FR-001, AC-13). Leave it in place.
const DefaultSubscriptionPattern = `^([a-z0-9_]+\.events\.v1|ai\.(tool_invoked|agent_run|proposal|token_usage)\.v1|security\..+)$`

// NewSubscription compiles pattern (empty → DefaultSubscriptionPattern).
func NewSubscription(pattern string) (*TopicSubscription, error) {
	if pattern == "" {
		pattern = DefaultSubscriptionPattern
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return nil, err
	}
	return &TopicSubscription{re: re}, nil
}

// Matches reports whether topic is in scope. DLQ topics are always excluded.
func (s *TopicSubscription) Matches(topic string) bool {
	if len(topic) >= 4 && topic[len(topic)-4:] == ".dlq" {
		return false
	}
	return s.re.MatchString(topic)
}

// Filter returns the in-scope subset of the given topics.
func (s *TopicSubscription) Filter(topics []string) []string {
	var out []string
	for _, t := range topics {
		if s.Matches(t) {
			out = append(out, t)
		}
	}
	return out
}
