package topics

import (
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/go-common/event"
)

func env(eventType, urn string, payload map[string]any) event.Envelope {
	return event.Envelope{
		EventID: uuid.New(), EventType: eventType, TenantID: uuid.New(),
		ResourceURN: urn, OccurredAt: time.Now(), Payload: payload,
	}
}

// TestAC15_RoutingTableContract covers every row of the RTH-FR-020 routing
// table: each event_type routes to exactly the expected topic and nothing else.
func TestAC15_RoutingTableContract(t *testing.T) {
	r := NewRouter(nil)
	cases := []struct {
		name  string
		env   event.Envelope
		want  string
		route bool
	}{
		{"pipeline_run", env("pipeline.run.status_changed", "wr:t:pipeline:run/pr-1", nil), "run-status:wr:t:pipeline:run/pr-1", true},
		{"pipeline_step", env("pipeline.step.started", "wr:t:pipeline:run/pr-1", nil), "run-status:wr:t:pipeline:run/pr-1", true},
		{"ingestion", env("ingestion.completed", "wr:t:ingestion:job/j-1", nil), "run-status:wr:t:ingestion:job/j-1", true},
		// inference-service's real event types are "inference.job.*" (task
		// #78 found the previous oneOf("inference.started",...) never
		// matched a single real event — this fixture used to encode that
		// same wrong assumption).
		{"inference", env("inference.job.failed", "wr:t:inference:job/i-1", nil), "run-status:wr:t:inference:job/i-1", true},
		{"chart_export", env("chart.export.completed", "", map[string]any{"operation_urn": "wr:t:chart:op/o-1"}), "run-status:wr:t:chart:op/o-1", true},
		{"case_bulk", env("case.bulk.completed", "", map[string]any{"operation_urn": "wr:t:case:op/o-2"}), "run-status:wr:t:case:op/o-2", true},
		// Generic case lifecycle events (task #78: no rule existed at all).
		{"case_assigned", env("case.assigned", "wr:t:case:case/c-1", nil), "run-status:wr:t:case:case/c-1", true},
		{"notification", env("notification.created", "", map[string]any{"user_id": "u-9"}), "notifications:u-9", true},
		{"proposal", env("proposal.approved", "wr:t:ai:proposal/pp-3", nil), "proposal:pp-3", true},
		{"agent_run", env("agent.run.status_changed", "wr:t:ai:run/ar-1", nil), "run-status:wr:t:ai:run/ar-1", true},
		// experiment-service's real event types (task #78: no rule existed;
		// distinct from agent_run's "agent.run.status_changed" above).
		{"experiment_run", env("run.status_changed", "wr:t:experiment:run/er-1", nil), "run-status:wr:t:experiment:run/er-1", true},
		{"unroutable_type", env("dataset.created", "wr:t:dataset:ds/1", nil), "", false},
		{"chart_missing_op", env("chart.export.completed", "", nil), "", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, ok := r.Route(c.env)
			if ok != c.route {
				t.Fatalf("route=%v want %v (topic %q)", ok, c.route, got)
			}
			if ok && got != c.want {
				t.Fatalf("topic=%q want %q", got, c.want)
			}
		})
	}
}

func TestRouter_DisabledRuleSkipped(t *testing.T) {
	r := NewRouter(map[string]bool{"notification": true})
	if _, ok := r.Route(env("notification.created", "", map[string]any{"user_id": "u-1"})); ok {
		t.Fatal("disabled rule should not route")
	}
}
