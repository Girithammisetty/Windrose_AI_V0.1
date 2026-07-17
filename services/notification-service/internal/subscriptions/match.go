// Package subscriptions evaluates subscription rules against events
// (NOTIF-FR-011). A rule matches when the event type matches one of its
// patterns (exact or wildcard like case.*), the resource_urn_prefix matches,
// and every attrs constraint is satisfied by the whitelisted payload fields.
package subscriptions

import (
	"strings"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
)

// Matches reports whether rule fires for env (NOTIF-FR-011).
func Matches(rule *domain.SubscriptionRule, env gcevent.Envelope) bool {
	if !matchesEventType(rule.EventTypes, env.EventType) {
		return false
	}
	f := rule.ResourceFtr
	if f.ResourceURNPrefix != "" && !strings.HasPrefix(env.ResourceURN, f.ResourceURNPrefix) {
		return false
	}
	for field, allowed := range f.Attrs {
		if !attrMatches(env.Payload[field], allowed) {
			return false
		}
	}
	return true
}

func matchesEventType(patterns []string, eventType string) bool {
	for _, p := range patterns {
		if p == eventType {
			return true
		}
		if strings.HasSuffix(p, ".*") && strings.HasPrefix(eventType, strings.TrimSuffix(p, "*")) {
			return true
		}
		if p == "*" {
			return true
		}
	}
	return false
}

// attrMatches checks a payload value against an allowed value set (string match).
func attrMatches(v any, allowed []string) bool {
	s, ok := v.(string)
	if !ok {
		return false
	}
	for _, a := range allowed {
		if a == s {
			return true
		}
	}
	return false
}
