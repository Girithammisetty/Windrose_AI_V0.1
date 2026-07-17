package topics

import (
	"strings"

	"github.com/windrose-ai/go-common/event"
)

// Rule maps a Kafka event_type pattern to a topic template (RTH-FR-020 routing
// table). Match returns true when the rule applies to an event_type; Render
// produces the concrete topic string from the envelope, or ("", false) when a
// required substitution is missing (unroutable → skip-and-count).
type Rule struct {
	Name     string
	Match    func(eventType string) bool
	Template string
}

// hasPrefix / exactAny helpers keep the rule table declarative.
func prefix(p string) func(string) bool {
	return func(t string) bool { return strings.HasPrefix(t, p) }
}
func oneOf(set ...string) func(string) bool {
	return func(t string) bool {
		for _, s := range set {
			if t == s {
				return true
			}
		}
		return false
	}
}

// Rules is the code-seeded routing table (RTH-FR-020 / §6). It is persisted into
// routing_rules for ops visibility/toggle, but code is the source of truth.
var Rules = []Rule{
	{Name: "pipeline_run", Match: prefix("pipeline.run."), Template: "run-status:{resource_urn}"},
	{Name: "pipeline_step", Match: prefix("pipeline.step."), Template: "run-status:{resource_urn}"},
	{Name: "ingestion", Match: oneOf("ingestion.started", "ingestion.progress", "ingestion.completed", "ingestion.failed"), Template: "run-status:{resource_urn}"},
	// inference-service's real event types are "inference.job.*" (created/
	// started/succeeded/failed/status_changed/cancelled) — the previous
	// oneOf("inference.started","inference.completed","inference.failed")
	// never matched a single real event (task #78); prefix-match like the
	// pipeline rules above, which DO work.
	{Name: "inference", Match: prefix("inference.job."), Template: "run-status:{resource_urn}"},
	{Name: "chart_export", Match: oneOf("chart.export.completed", "chart.export.failed"), Template: "run-status:{payload.operation_urn}"},
	// case_bulk must stay ahead of the generic "case" rule below (first-match
	// wins) so case.bulk.completed keeps its own operation_urn-keyed topic.
	{Name: "case_bulk", Match: oneOf("case.bulk.completed"), Template: "run-status:{payload.operation_urn}"},
	// Generic per-case lifecycle events (assigned/unassigned/escalated/sla.*/
	// comment.added) — case-service always sets resource_urn to the case's
	// own URN (task #78: this had no rule at all before, every case event
	// was silently dropped as unroutable).
	{Name: "case", Match: prefix("case."), Template: "run-status:{resource_urn}"},
	{Name: "notification", Match: oneOf("notification.created"), Template: "notifications:{payload.user_id}"},
	{Name: "proposal", Match: oneOf("proposal.created", "proposal.approved", "proposal.rejected", "proposal.expired"), Template: "proposal:{resource_id}"},
	{Name: "agent_run", Match: oneOf("agent.run.status_changed"), Template: "run-status:{resource_urn}"},
	// experiment-service's real event types are "run.status_changed"/
	// "run.mirrored"/"run.metrics_updated" (distinct from agent_run's
	// "agent.run.status_changed" above — no collision). No rule existed for
	// these at all before (task #78).
	{Name: "experiment_run", Match: prefix("run."), Template: "run-status:{resource_urn}"},
}

// Router routes envelopes to topic strings. Disabled rules (loaded from
// routing_rules) are skipped, letting ops toggle a route without a deploy.
type Router struct {
	rules    []Rule
	disabled map[string]bool
}

// NewRouter builds a Router from the code rule table. disabled is a set of rule
// names to skip (nil = all enabled).
func NewRouter(disabled map[string]bool) *Router {
	if disabled == nil {
		disabled = map[string]bool{}
	}
	return &Router{rules: Rules, disabled: disabled}
}

// Route returns the destination topic for an envelope, or ("", false) when no
// enabled rule matches or a required substitution is absent (skip-and-count).
func (r *Router) Route(env event.Envelope) (string, bool) {
	for _, rule := range r.rules {
		if r.disabled[rule.Name] || !rule.Match(env.EventType) {
			continue
		}
		topic, ok := render(rule.Template, env)
		if !ok {
			return "", false
		}
		return topic, true
	}
	return "", false
}

// render substitutes {resource_urn}, {resource_id}, {payload.<key>} into a
// template. resource_id is the last path segment of resource_urn.
func render(tmpl string, env event.Envelope) (string, bool) {
	out := tmpl
	if strings.Contains(out, "{resource_urn}") {
		if env.ResourceURN == "" {
			return "", false
		}
		out = strings.ReplaceAll(out, "{resource_urn}", env.ResourceURN)
	}
	if strings.Contains(out, "{resource_id}") {
		id := resourceID(env.ResourceURN)
		if id == "" {
			// proposal events may carry the id in the payload instead.
			if v, ok := env.Payload["proposal_id"].(string); ok && v != "" {
				id = v
			}
		}
		if id == "" {
			return "", false
		}
		out = strings.ReplaceAll(out, "{resource_id}", id)
	}
	for {
		i := strings.Index(out, "{payload.")
		if i < 0 {
			break
		}
		j := strings.IndexByte(out[i:], '}')
		if j < 0 {
			return "", false
		}
		key := out[i+len("{payload.") : i+j]
		v, ok := env.Payload[key].(string)
		if !ok || v == "" {
			return "", false
		}
		out = out[:i] + v + out[i+j+1:]
	}
	return out, true
}

// resourceID returns the trailing "<id>" of a URN "...:<type>/<id>".
func resourceID(urn string) string {
	if i := strings.LastIndexByte(urn, '/'); i >= 0 && i < len(urn)-1 {
		return urn[i+1:]
	}
	return ""
}
