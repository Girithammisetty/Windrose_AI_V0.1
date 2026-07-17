package subscriptions

import (
	"testing"

	gcevent "github.com/windrose-ai/go-common/event"
	"github.com/windrose-ai/notification-service/internal/domain"
)

// TestAC11_FilterMatch proves a rule with attrs.severity=[high,critical] on
// case.sla.breached fires for high but not medium (AC-11).
func TestAC11_FilterMatch(t *testing.T) {
	rule := &domain.SubscriptionRule{
		EventTypes:  []string{"case.sla.breached"},
		ResourceFtr: domain.ResourceFilter{ResourceURNPrefix: "wr:t-42:case:", Attrs: map[string][]string{"severity": {"high", "critical"}}},
	}
	high := gcevent.Envelope{EventType: "case.sla.breached", ResourceURN: "wr:t-42:case:case/1", Payload: map[string]any{"severity": "high"}}
	medium := gcevent.Envelope{EventType: "case.sla.breached", ResourceURN: "wr:t-42:case:case/1", Payload: map[string]any{"severity": "medium"}}
	other := gcevent.Envelope{EventType: "case.sla.breached", ResourceURN: "wr:t-99:case:case/1", Payload: map[string]any{"severity": "high"}}

	if !Matches(rule, high) {
		t.Error("high severity should match")
	}
	if Matches(rule, medium) {
		t.Error("medium severity must not match")
	}
	if Matches(rule, other) {
		t.Error("wrong URN prefix must not match")
	}
}

func TestWildcardEventType(t *testing.T) {
	rule := &domain.SubscriptionRule{EventTypes: []string{"case.*"}}
	if !Matches(rule, gcevent.Envelope{EventType: "case.escalated"}) {
		t.Error("case.* should match case.escalated")
	}
	if Matches(rule, gcevent.Envelope{EventType: "pipeline.run.failed"}) {
		t.Error("case.* should not match pipeline events")
	}
}
