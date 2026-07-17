// Package registry is the code-owned event→notification mapping registry
// (NOTIF-FR-002, BRD 19 §6). For each notifiable event_type it declares the
// default audience derivation, default channels, template key, severity class,
// digestible flag, the whitelisted template variables (BR-5) and the
// whitelisted resource_filter attribute fields (BR-12). Adding a mapping is a
// PR-reviewed change; unmapped events are ignored by the pipeline.
package registry

import "github.com/windrose-ai/go-common/event"

// AudienceRole is a derived-audience group resolved via the rbac projection
// (NOTIF-FR-013) rather than a literal payload principal.
type AudienceRole string

const (
	RoleWorkspaceManagers AudienceRole = "workspace_managers"
	RoleTenantAdmins      AudienceRole = "tenant_admins"
	RoleWorkspaceSubs     AudienceRole = "workspace_subscribers"
)

// AudienceRef is one source of default recipients for an event.
type AudienceRef struct {
	// PayloadField reads env.Payload[field]; the value may be a string or a
	// []string of user ids (e.g. "assignee", "approvers").
	PayloadField string
	// Role resolves a group of recipients via the rbac projection.
	Role AudienceRole
}

// Mapping is one event_type's notification policy.
type Mapping struct {
	EventType   string
	Audience    []AudienceRef
	Channels    []string
	TemplateKey string
	Class       string // info | warning | critical
	Digestible  bool
	// Variables is the whitelisted template variable schema for this event
	// type: name → declared type. Templates may reference only these (BR-5).
	Variables map[string]string
	// FilterAttrs is the whitelist of resource_filter.attrs fields a rule may
	// reference for this event type (BR-12).
	FilterAttrs []string
}

// Registry is the loaded mapping set keyed by event_type.
type Registry struct {
	byType map[string]Mapping
}

// Default builds the initial registry (BRD 19 §6 table).
func Default() *Registry {
	r := &Registry{byType: map[string]Mapping{}}
	for _, m := range initialMappings() {
		r.byType[m.EventType] = m
	}
	return r
}

// Lookup returns the mapping for an event type, ok=false when unmapped.
func (r *Registry) Lookup(eventType string) (Mapping, bool) {
	m, ok := r.byType[eventType]
	return m, ok
}

// EventTypes returns every mapped event type (consumer subscription + tests).
func (r *Registry) EventTypes() []string {
	out := make([]string, 0, len(r.byType))
	for t := range r.byType {
		out = append(out, t)
	}
	return out
}

// SeverityFor returns the severity class for an event type (default info).
func (r *Registry) SeverityFor(eventType string) string {
	if m, ok := r.byType[eventType]; ok {
		return m.Class
	}
	return "info"
}

// Whitelisted reports whether a resource_filter attr field is allowed for an
// event type (BR-12).
func (r *Registry) Whitelisted(eventType, field string) bool {
	m, ok := r.byType[eventType]
	if !ok {
		return false
	}
	for _, f := range m.FilterAttrs {
		if f == field {
			return true
		}
	}
	return false
}

// ExtractPayloadPrincipals reads the literal principals for an audience ref
// from an event payload (string or []string).
func ExtractPayloadPrincipals(env event.Envelope, field string) []string {
	v, ok := env.Payload[field]
	if !ok {
		return nil
	}
	switch t := v.(type) {
	case string:
		if t == "" {
			return nil
		}
		return []string{t}
	case []string:
		return t
	case []any:
		var out []string
		for _, e := range t {
			if s, ok := e.(string); ok && s != "" {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

func initialMappings() []Mapping {
	inApp := []string{"in_app"}
	inAppEmail := []string{"in_app", "email"}
	return []Mapping{
		{
			EventType: "case.assigned", Audience: []AudienceRef{{PayloadField: "assignee"}},
			Channels: inAppEmail, TemplateKey: "case.assigned", Class: "warning", Digestible: false,
			Variables:   map[string]string{"CaseNumber": "int", "Severity": "string", "DueDate": "time", "AssignerName": "string", "DeepLink": "url", "WorkspaceName": "string"},
			FilterAttrs: []string{"severity", "workspace_id"},
		},
		{
			EventType: "case.sla.breached", Audience: []AudienceRef{{PayloadField: "prior_assignee"}, {Role: RoleWorkspaceManagers}},
			Channels: inAppEmail, TemplateKey: "case.sla.breached", Class: "critical", Digestible: false,
			Variables:   map[string]string{"CaseNumber": "int", "Severity": "string", "DeepLink": "url"},
			FilterAttrs: []string{"severity", "workspace_id"},
		},
		{
			EventType: "case.unassigned", Audience: []AudienceRef{{PayloadField: "prior_assignee"}, {Role: RoleWorkspaceManagers}},
			Channels: inAppEmail, TemplateKey: "case.unassigned", Class: "critical", Digestible: false,
			Variables:   map[string]string{"CaseNumber": "int", "Reason": "string", "DeepLink": "url"},
			FilterAttrs: []string{"reason", "severity"},
		},
		{
			EventType: "case.sla.warning", Audience: []AudienceRef{{PayloadField: "assignee"}},
			Channels: inApp, TemplateKey: "case.sla.warning", Class: "warning", Digestible: false,
			Variables:   map[string]string{"CaseNumber": "int", "DeepLink": "url"},
			FilterAttrs: []string{"severity"},
		},
		{
			EventType: "case.escalated", Audience: []AudienceRef{{PayloadField: "escalation_target"}},
			Channels: inAppEmail, TemplateKey: "case.escalated", Class: "critical", Digestible: false,
			Variables:   map[string]string{"CaseNumber": "int", "Severity": "string", "DeepLink": "url"},
			FilterAttrs: []string{"severity"},
		},
		{
			EventType: "case.comment.added", Audience: []AudienceRef{{PayloadField: "assignee"}},
			Channels: inApp, TemplateKey: "case.comment.added", Class: "info", Digestible: true,
			Variables:   map[string]string{"CaseNumber": "int", "CommenterName": "string", "DeepLink": "url"},
			FilterAttrs: []string{"severity"},
		},
		{
			EventType: "chart.export.completed", Audience: []AudienceRef{{PayloadField: "initiator"}},
			Channels: inApp, TemplateKey: "chart.export.completed", Class: "info", Digestible: false,
			Variables:   map[string]string{"ChartName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "chart.export.failed", Audience: []AudienceRef{{PayloadField: "initiator"}},
			Channels: inApp, TemplateKey: "chart.export.failed", Class: "info", Digestible: false,
			Variables:   map[string]string{"ChartName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "pipeline.run.failed", Audience: []AudienceRef{{PayloadField: "owner"}},
			Channels: inAppEmail, TemplateKey: "pipeline.run.failed", Class: "critical", Digestible: false,
			Variables:   map[string]string{"PipelineName": "string", "RunID": "string", "DeepLink": "url"},
			FilterAttrs: []string{"pipeline_id"},
		},
		{
			EventType: "inference.failed", Audience: []AudienceRef{{PayloadField: "owner"}},
			Channels: inAppEmail, TemplateKey: "inference.failed", Class: "critical", Digestible: false,
			Variables:   map[string]string{"ModelName": "string", "DeepLink": "url"},
			FilterAttrs: []string{"model_id"},
		},
		{
			EventType: "pipeline.run.completed", Audience: []AudienceRef{{PayloadField: "owner"}},
			Channels: inApp, TemplateKey: "pipeline.run.completed", Class: "info", Digestible: true,
			Variables:   map[string]string{"PipelineName": "string", "DeepLink": "url"},
			FilterAttrs: []string{"pipeline_id"},
		},
		{
			EventType: "inference.completed", Audience: []AudienceRef{{PayloadField: "owner"}},
			Channels: inApp, TemplateKey: "inference.completed", Class: "info", Digestible: true,
			Variables:   map[string]string{"ModelName": "string", "DeepLink": "url"},
			FilterAttrs: []string{"model_id"},
		},
		{
			EventType: "ingestion.completed", Audience: []AudienceRef{{PayloadField: "owner"}},
			Channels: inApp, TemplateKey: "ingestion.completed", Class: "info", Digestible: true,
			Variables:   map[string]string{"DatasetName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "ingestion.failed", Audience: []AudienceRef{{PayloadField: "owner"}},
			Channels: inApp, TemplateKey: "ingestion.failed", Class: "info", Digestible: true,
			Variables:   map[string]string{"DatasetName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "dataset.version.created", Audience: []AudienceRef{{PayloadField: "owner"}, {Role: RoleWorkspaceSubs}},
			Channels: inApp, TemplateKey: "dataset.version.created", Class: "info", Digestible: true,
			Variables:   map[string]string{"DatasetName": "string", "Version": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "experiment.model.promoted", Audience: []AudienceRef{{Role: RoleWorkspaceSubs}},
			Channels: inApp, TemplateKey: "experiment.model.promoted", Class: "info", Digestible: true,
			Variables:   map[string]string{"ModelName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "proposal.created", Audience: []AudienceRef{{PayloadField: "approvers"}},
			Channels: inAppEmail, TemplateKey: "proposal.created", Class: "warning", Digestible: false,
			Variables:   map[string]string{"ProposalTitle": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "proposal.approved", Audience: []AudienceRef{{PayloadField: "proposer"}},
			Channels: inApp, TemplateKey: "proposal.approved", Class: "info", Digestible: true,
			Variables:   map[string]string{"ProposalTitle": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "proposal.rejected", Audience: []AudienceRef{{PayloadField: "proposer"}},
			Channels: inApp, TemplateKey: "proposal.rejected", Class: "info", Digestible: true,
			Variables:   map[string]string{"ProposalTitle": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "usage.budget.threshold", Audience: []AudienceRef{{Role: RoleTenantAdmins}},
			Channels: inAppEmail, TemplateKey: "usage.budget.threshold", Class: "critical", Digestible: false,
			Variables:   map[string]string{"BudgetName": "string", "Percent": "int", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "usage.budget.exhausted", Audience: []AudienceRef{{Role: RoleTenantAdmins}},
			Channels: inAppEmail, TemplateKey: "usage.budget.exhausted", Class: "critical", Digestible: false,
			Variables:   map[string]string{"BudgetName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "identity.user.created", Audience: []AudienceRef{{PayloadField: "user_id"}},
			Channels: []string{"email"}, TemplateKey: "identity.user.created", Class: "info", Digestible: false,
			Variables:   map[string]string{"UserName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "rbac.grant.created", Audience: []AudienceRef{{PayloadField: "grantee"}},
			Channels: inApp, TemplateKey: "rbac.grant.created", Class: "info", Digestible: true,
			Variables:   map[string]string{"ResourceName": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			EventType: "security.cross_tenant_denied", Audience: []AudienceRef{{Role: RoleTenantAdmins}},
			Channels: inApp, TemplateKey: "security.cross_tenant_denied", Class: "critical", Digestible: false,
			Variables:   map[string]string{"Path": "string", "DeepLink": "url"},
			FilterAttrs: []string{},
		},
		{
			// audit.export.v1 (Phase 3 SIEM export, docs/design/siem-export.md):
			// deliberately no in-app/email audience — this mapping exists only so
			// Process's Registry.Lookup succeeds and reaches deliverWebhooks
			// (pipeline.go). Delivery is exclusively via a tenant's
			// webhook_endpoint configured with event_types: ["audit.export.v1"];
			// nothing here resolves recipients or renders a template.
			EventType: "audit.export.v1", Audience: nil,
			Channels: nil, TemplateKey: "", Class: "info", Digestible: false,
			Variables:   map[string]string{},
			FilterAttrs: []string{},
		},
	}
}
