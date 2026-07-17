package domain

// Friendly, task-shaped preset roles seeded per tenant as EDITABLE custom roles
// (system=false), on top of the 10 immutable system roles. They give a tenant
// admin plain, non-technical starting points — a claim adjuster maps onto
// "Reviewer", not "Case Analyst"/"Use case Admin" — that they can assign as-is,
// clone-and-customize, rename, or delete. Generic (not vertical-hardcoded) so
// they hold across insurance/health/banking tenants.
//
// Bundles compose ONLY canonical-catalog actions; PresetRoleSeeds filters each
// through CatalogMap() so a mistyped action is dropped, never FK-violating the
// role_actions -> actions constraint (which would break tenant provisioning).

// PresetRoleNames is the ordered set of preset role names (also used to detect
// collisions with system role names in tests).
func PresetRoleNames() []string {
	return []string{"Viewer", "Reviewer", "Analyst", "Builder"}
}

// PresetRoleSeeds returns the preset roles with catalog-validated action sets.
func PresetRoleSeeds() []RoleSeed {
	raw := map[string][]string{
		// Read-only across the working surfaces: see cases, dashboards, data.
		"Viewer": {
			"case.case.read", "case.case.list", "case.disposition.read",
			"chart.dashboard.read", "chart.dashboard.list", "chart.chart.read", "chart.chart.list",
			"dataset.dataset.read", "dataset.dataset.list",
			"semantic.model.read", "semantic.model.list",
			"query.execution.read", "query.execution.list",
			"ai.proposal.read", "ai.proposal.list",
			"notification.inbox.read",
		},
		// Operational case worker: triage + resolve cases, approve AI proposals,
		// use the copilot. The everyday front-line role.
		"Reviewer": {
			"case.case.read", "case.case.list", "case.case.update", "case.case.assign",
			"case.disposition.read", "case.disposition.create", "case.disposition.update",
			"ai.proposal.read", "ai.proposal.list", "ai.proposal.approve",
			"ai.agent_session.read", "ai.agent_session.create", "ai.agent_session.execute",
			"memory.memory.read",
			"chart.dashboard.read", "chart.chart.read",
			"semantic.model.read", "semantic.compile.execute",
			"query.execution.read", "query.execution.execute",
			"dataset.dataset.read",
			"notification.inbox.read",
		},
		// Insights explorer + dashboard author over the governed semantic layer.
		"Analyst": {
			"dataset.dataset.read", "dataset.dataset.list",
			"query.query.read", "query.query.create", "query.query.execute",
			"query.execution.read", "query.execution.execute", "query.execution.export",
			"chart.dashboard.read", "chart.dashboard.list", "chart.dashboard.create", "chart.dashboard.update",
			"chart.chart.read", "chart.chart.list", "chart.chart.create", "chart.chart.update",
			"semantic.model.read", "semantic.model.list", "semantic.measure.read", "semantic.compile.execute",
			"notification.report.read", "notification.report.create",
			"usage.report.read",
		},
		// Data/ML builder: onboard data, build pipelines/experiments, run inference.
		"Builder": {
			"ingestion.connection.read", "ingestion.connection.list", "ingestion.connection.create",
			"ingestion.connection.update", "ingestion.connection.execute",
			"ingestion.ingestion.read", "ingestion.ingestion.list", "ingestion.ingestion.create", "ingestion.ingestion.execute",
			"ingestion.upload.read", "ingestion.upload.create", "ingestion.upload.execute",
			"dataset.dataset.read", "dataset.dataset.list", "dataset.dataset.create", "dataset.dataset.update",
			"dataset.profile.read", "dataset.profile.execute", "dataset.lineage.read",
			"pipeline.template.read", "pipeline.template.list", "pipeline.template.create",
			"pipeline.run.read", "pipeline.run.list", "pipeline.run.create", "pipeline.run.execute",
			"pipeline.component.read", "pipeline.component.list", "pipeline.algorithm.read", "pipeline.algorithm.list",
			"experiment.experiment.read", "experiment.experiment.list", "experiment.experiment.create", "experiment.experiment.update",
			"experiment.run.read", "experiment.run.list", "experiment.run.create", "experiment.run.execute",
			"experiment.model.read", "experiment.model.list", "experiment.model.update",
			"experiment.model_card.read", "experiment.model_card.update",
			"inference.job.read", "inference.job.list", "inference.job.create", "inference.job.execute",
			"inference.schedule.read", "inference.schedule.list", "inference.schedule.create",
			"semantic.model.read", "query.execution.read", "query.execution.execute",
			"chart.dashboard.read", "ai.agent_session.read", "ai.agent_session.create", "ai.agent_session.execute",
		},
	}
	cat := CatalogMap()
	keep := func(actions []string) []string {
		out := make([]string, 0, len(actions))
		for _, a := range actions {
			if _, ok := cat[a]; ok {
				out = append(out, a)
			}
		}
		return out
	}
	seeds := make([]RoleSeed, 0, len(raw))
	for _, name := range PresetRoleNames() {
		seeds = append(seeds, RoleSeed{Name: name, Actions: keep(raw[name])})
	}
	return seeds
}
