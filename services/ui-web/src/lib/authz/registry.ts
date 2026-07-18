/**
 * Capability / navigation registry (UI-FR-004 role-based view gating).
 *
 * Single source of truth mapping every nav item, route, and gated feature-action
 * to the capability (rbac action name) or role that unlocks it. The viewer's
 * roles + capabilities come from the backend (bff `viewer.roles/capabilities`,
 * sourced from rbac's permissions_flat projection). This module makes NO
 * security decision — it gates the UI for UX only; the domain services still
 * enforce every action. Fail-safe: a capability the client cannot confirm HIDES
 * the feature (absent capability → not shown).
 */
import { Database, FlaskConical, BarChart3, Briefcase, Shield, Bot, Inbox, Home, LineChart, Plug, Workflow, Terminal, DownloadCloud, Bell, TableProperties, Fingerprint, Boxes, HelpCircle } from "lucide-react";
import type { MessageKey } from "@/lib/i18n/messages";

/** The tenant-admin role short-circuits every action check (rbac BR-7). */
export const ADMIN_ROLE = "Admin";
/** The wildcard capability rbac returns for a tenant admin. */
export const WILDCARD = "*";

/** A gate is satisfied by an action capability, a role, or is always open. */
export type Gate =
  | { kind: "public" }
  | { kind: "capability"; action: string }
  | { kind: "role"; role: string };

export const publicGate: Gate = { kind: "public" };
export const cap = (action: string): Gate => ({ kind: "capability", action });
export const role = (r: string): Gate => ({ kind: "role", role: r });

/** The viewer's effective capability view (derived from bff `viewer`). */
export interface CapabilitySet {
  capabilities: ReadonlySet<string>;
  roles: ReadonlySet<string>;
  isAdmin: boolean;
}

export function toCapabilitySet(input: {
  capabilities?: string[] | null;
  roles?: string[] | null;
}): CapabilitySet {
  const capabilities = new Set(input.capabilities ?? []);
  const roles = new Set(input.roles ?? []);
  const isAdmin = capabilities.has(WILDCARD) || roles.has(ADMIN_ROLE);
  return { capabilities, roles, isAdmin };
}

/** The empty (fail-safe) capability set: nothing is allowed. */
export const EMPTY_CAPABILITIES: CapabilitySet = {
  capabilities: new Set(),
  roles: new Set(),
  isAdmin: false,
};

/** Whether a viewer with `caps` may pass `gate`. Admin passes everything. */
export function allows(gate: Gate, caps: CapabilitySet): boolean {
  switch (gate.kind) {
    case "public":
      return true;
    case "capability":
      return caps.isAdmin || caps.capabilities.has(gate.action);
    case "role":
      return caps.isAdmin || caps.roles.has(gate.role);
  }
}

/** A sidebar section header. Items sharing a `group` are rendered contiguously
 * under one header; a section appears only when ≥1 of its items is visible to
 * the viewer, so an adjuster never sees an empty "Data" heading. Items with no
 * `group` render ungrouped at their position (Home / Copilot / Admin anchors). */
export type NavGroup = "casework" | "data" | "ml" | "insights";

/** A primary navigation entry, gated by a single capability/role. */
export interface NavItem {
  key: string;
  href: string;
  icon: typeof Home;
  label: MessageKey;
  gate: Gate;
  /** Sidebar section this item belongs to (see NavGroup). Ungrouped when absent. */
  group?: NavGroup;
}

/** Section header label per group (rendered by the Sidebar when the group has
 * ≥1 visible item). Ordered for reference; actual order follows NAV_ITEMS. */
export const NAV_GROUP_LABEL: Record<NavGroup, MessageKey> = {
  casework: "nav.group.casework",
  data: "nav.group.data",
  ml: "nav.group.ml",
  insights: "nav.group.insights",
};

/**
 * Primary nav. Order is display order; items are grouped into sections (see
 * NavGroup) so each persona sees a chunked, scannable sidebar rather than a flat
 * wall of links. Each item shows ONLY when the viewer satisfies its gate, and a
 * section header shows only when ≥1 of its items is visible:
 *   adjuster      → Home · Casework(Cases, Approvals) · Insights(Dashboards) · Copilot · Notifications
 *   manager       → + Insights(Reports)
 *   datascientist → Home · Data(Datasets, Sources, Ingestions, Queries, Pipelines, Semantic Models) · ML(ML, Eval) · Insights(Dashboards, Reports) · Copilot · Notifications
 *   admin         → everything incl. Admin
 * Items sharing a group MUST stay contiguous here — the Sidebar emits a header
 * whenever the group changes between adjacent visible items.
 */
export const NAV_ITEMS: NavItem[] = [
  { key: "home", href: "/", icon: Home, label: "nav.home", gate: publicGate },

  // ── Casework ──
  { key: "cases", href: "/cases", icon: Briefcase, label: "nav.cases", gate: cap("case.case.read"), group: "casework" },
  { key: "decisions", href: "/decisions", icon: TableProperties, label: "nav.decisions", gate: cap("case.disposition.read"), group: "casework" },
  { key: "inbox", href: "/inbox", icon: Inbox, label: "nav.inbox", gate: cap("ai.proposal.read"), group: "casework" },

  // ── Data ──
  { key: "data", href: "/data", icon: Database, label: "nav.datasets", gate: cap("dataset.dataset.list"), group: "data" },
  { key: "sources", href: "/data/connections", icon: Plug, label: "nav.sources", gate: cap("ingestion.connection.read"), group: "data" },
  { key: "ingestions", href: "/data/ingestions", icon: DownloadCloud, label: "nav.ingestions", gate: cap("ingestion.ingestion.read"), group: "data" },
  { key: "queries", href: "/data/queries", icon: Terminal, label: "nav.queries", gate: cap("query.query.read"), group: "data" },
  { key: "pipelines", href: "/data/pipelines", icon: Workflow, label: "nav.pipelines", gate: cap("pipeline.template.read"), group: "data" },
  { key: "semanticModels", href: "/data/semantic-models", icon: LineChart, label: "nav.semanticModels", gate: cap("semantic.model.list"), group: "data" },
  { key: "entityResolution", href: "/data/entity-resolution", icon: Fingerprint, label: "nav.entityResolution", gate: cap("dataset.entity.read"), group: "data" },
  { key: "packs", href: "/packs", icon: Boxes, label: "nav.packs", gate: cap("pack.pack.read"), group: "data" },

  // ── Machine Learning ──
  { key: "ml", href: "/ml", icon: FlaskConical, label: "nav.ml", gate: cap("experiment.experiment.read"), group: "ml" },
  /** Eval flywheel (eval-service, Tier 2a): suites/runs/gates/canaries/trends —
   * model scorecards + promotion-gate status. Sits alongside experiments/models
   * under ML; gated on the base run-read capability (eval-service requires the
   * write scope even to GET a suite, so run-read is the more representative
   * "can this persona see eval results at all" signal). */
  { key: "mlEval", href: "/ml/eval", icon: FlaskConical, label: "nav.mlEval", gate: cap("eval.run.read"), group: "ml" },

  // ── Insights ──
  { key: "dashboards", href: "/dashboards", icon: BarChart3, label: "nav.dashboards", gate: cap("chart.dashboard.read"), group: "insights" },
  /** Scheduled dashboard report subscriptions (NOTIF-FR-060, "Team Reports").
   * Gated on notification.report.read (Case Manager / Insights User / Model
   * Builder), not usage.report.read (that's the unrelated cost-oversight
   * capability, surfaced instead via CostPanel on Home / Admin > Usage). */
  { key: "reports", href: "/dashboards/reports", icon: LineChart, label: "nav.reports", gate: cap("notification.report.read"), group: "insights" },

  // ── Ungrouped anchors ──
  { key: "copilot", href: "/copilot", icon: Bot, label: "nav.copilot", gate: publicGate },
  /** In-app notification inbox + per-user delivery preferences (Tier 2b,
   * notification-service NOTIF-FR-020/012). Every seeded role holds
   * notification.inbox.read. The TopBar bell is gated identically. */
  { key: "notifications", href: "/notifications", icon: Bell, label: "nav.notifications", gate: cap("notification.inbox.read") },
  // Help Center — pack-scoped end-user + admin guides. Public so every persona
  // sees it; the page auto-scopes content to the tenant's installed pack.
  { key: "help", href: "/help", icon: HelpCircle, label: "nav.help", gate: publicGate },
  { key: "admin", href: "/admin", icon: Shield, label: "nav.admin", gate: role(ADMIN_ROLE) },
];

/**
 * Route guard rules. The guard resolves a pathname to the FIRST matching rule
 * (longest prefix first), so `/admin/users` inherits the `/admin` gate and
 * `/data/connections` inherits `/data`. Unlisted routes are public (they are
 * only reachable from a nav item that is itself gated).
 */
interface RouteRule {
  prefix: string;
  gate: Gate;
}

const ROUTE_RULES: RouteRule[] = [
  { prefix: "/admin", gate: role(ADMIN_ROLE) },
  // Data Sources sits under /data but needs the ingestion capability, not the
  // dataset one — the longer prefix wins the longest-match resolution below.
  { prefix: "/data/connections", gate: cap("ingestion.connection.read") },
  // File-upload data source wizard — needs the upload capability, not the
  // generic dataset one (longer prefix wins the longest-match resolution).
  { prefix: "/data/upload", gate: cap("ingestion.upload.create") },
  { prefix: "/data/ingestions", gate: cap("ingestion.ingestion.read") },
  { prefix: "/data/queries", gate: cap("query.query.read") },
  // Pipelines also sit under /data but need the pipeline capability.
  { prefix: "/data/pipelines", gate: cap("pipeline.template.read") },
  // Semantic models also sit under /data but need the semantic capability.
  { prefix: "/data/semantic-models", gate: cap("semantic.model.list") },
  // Entity resolution (BRD 56) also sits under /data but needs the ER read cap.
  { prefix: "/data/entity-resolution", gate: cap("dataset.entity.read") },
  { prefix: "/data", gate: cap("dataset.dataset.list") },
  // Capability packs (BRD 23): browse/install verticals.
  { prefix: "/packs", gate: cap("pack.pack.read") },
  // Eval flywheel also sits under /ml but needs the eval capability, not the
  // experiment one — the longer prefix wins the longest-match resolution below.
  { prefix: "/ml/eval", gate: cap("eval.run.read") },
  { prefix: "/ml", gate: cap("experiment.experiment.read") },
  { prefix: "/dashboards/reports", gate: cap("notification.report.read") },
  { prefix: "/dashboards", gate: cap("chart.dashboard.read") },
  { prefix: "/cases", gate: cap("case.case.read") },
  { prefix: "/inbox", gate: cap("ai.proposal.read") },
  { prefix: "/copilot", gate: publicGate },
  { prefix: "/notifications", gate: cap("notification.inbox.read") },
  { prefix: "/", gate: publicGate },
].sort((a, b) => b.prefix.length - a.prefix.length);

/** The gate protecting a given route (longest-prefix match; default public). */
export function gateForPath(pathname: string): Gate {
  for (const rule of ROUTE_RULES) {
    if (rule.prefix === "/") {
      if (pathname === "/") return rule.gate;
      continue;
    }
    if (pathname === rule.prefix || pathname.startsWith(rule.prefix + "/")) {
      return rule.gate;
    }
  }
  return publicGate;
}

/**
 * Feature-action gates used for in-page controls (buttons/menu items). Keeping
 * them here (not inline) keeps the "what unlocks this" answer in one auditable
 * place. A control is rendered only when its gate passes; the server re-checks.
 */
export const FEATURE_GATES = {
  bulkAssignCases: cap("case.case.assign"),
  bulkApproveProposals: cap("ai.proposal.approve"),
  approveProposal: cap("ai.proposal.approve"),
  createConnection: cap("ingestion.connection.create"),
  /** Start a resumable chunked upload (ingestion-service POST /uploads). Note:
   * no seeded tenant role holds ingestion.upload.* today (only Admin, which
   * bypasses action checks entirely) — same situation as promoteModel below;
   * this stays hidden for non-admin personas until the seed grants it. */
  createUpload: cap("ingestion.upload.create"),
  buildPipeline: cap("pipeline.template.create"),
  viewCostPanel: cap("usage.report.read"),
  manageUsers: cap("identity.user.list"),
  /** Invite a new user (identity-service POST /users/invite). */
  inviteUser: cap("identity.user.admin"),
  /** Create a workspace (rbac-service POST /workspaces). */
  createWorkspace: cap("rbac.workspace.create"),
  /** Manage group membership (rbac-service group member PUT/DELETE). */
  manageGroupMembers: cap("rbac.group.assign"),
  /** Create a Team (rbac-service POST /groups, group_type=permission). */
  createTeam: cap("rbac.group.create"),
  /** Edit a Team's name/description (rbac-service PATCH /groups/{id}). */
  updateTeam: cap("rbac.group.update"),
  /** Delete a Team (rbac-service DELETE /groups/{id}). */
  deleteTeam: cap("rbac.group.delete"),
  /** Bind/unbind a role on a Team (rbac-service PUT|DELETE /groups/{id}/roles/{roleId}). */
  assignTeamRole: cap("rbac.group.update"),
  /** Create a budget (usage-service POST /budgets). */
  createBudget: cap("usage.budget.create"),
  /** Edit a budget's limit/degrade action (usage-service PATCH /budgets/{id}). */
  updateBudget: cap("usage.budget.update"),
  /** Delete a budget (usage-service DELETE /budgets/{id}). */
  deleteBudget: cap("usage.budget.delete"),
  /** Create a draft rate card (usage-service POST /rate-cards). Platform-only —
   * no seeded tenant role holds this; hidden for every demo persona by design. */
  createRateCard: cap("usage.ratecard.create"),
  /** Activate a rate card (usage-service POST /rate-cards/{id}/activate). Platform-only. */
  activateRateCard: cap("usage.ratecard.update"),
  /** View detected spend anomalies (usage-service GET /anomalies). */
  viewAnomalies: cap("usage.anomaly.read"),
  /** Dismiss a detected anomaly (usage-service POST /anomalies/{id}/dismiss). */
  dismissAnomaly: cap("usage.anomaly.update"),
  /** Archive a dataset (dataset-service DELETE /datasets/{id}). */
  archiveDataset: cap("dataset.dataset.delete"),
  /** Restore an archived dataset (dataset-service POST /datasets/{id}/restore). */
  restoreDataset: cap("dataset.dataset.update"),
  /** Edit a dataset's name/description (dataset-service PATCH /datasets/{id}). */
  editDataset: cap("dataset.dataset.update"),
  /** BRD 56: read entity-resolution runs / clusters / merge candidates
   * (dataset-service GET resolution-runs endpoints). */
  viewEntityResolution: cap("dataset.entity.read"),
  /** BRD 56: run a resolution + materialize the golden-record dataset
   * (dataset-service POST entity-resolution / materialize). */
  runEntityResolution: cap("dataset.entity.execute"),
  /** BRD 56: open a four-eyes merge proposal on a candidate (agent-runtime
   * POST /entity-merges; the caller must hold dataset.entity.merge). */
  proposeEntityMerge: cap("dataset.entity.merge"),
  /** BRD 23: browse the capability-pack catalog + installs. */
  viewPacks: cap("pack.pack.read"),
  /** BRD 23: install / uninstall a capability pack into a workspace. */
  installPack: cap("pack.install.execute"),
  /** Archive/restore a dashboard (chart-service POST .../archive, PATCH .../restore
   * — both authorized as chart.dashboard.update, the canonical flag-flip verb). */
  archiveDashboard: cap("chart.dashboard.update"),
  /** Archive an experiment (experiment-service DELETE /experiments/{id}). */
  archiveExperiment: cap("experiment.experiment.delete"),
  /** Restore an archived experiment (experiment-service PATCH /experiments/{id}/restore). */
  restoreExperiment: cap("experiment.experiment.update"),
  /** Author dashboards + charts (create dashboard, add chart, delete). Manager +
   * datascientist both hold chart.dashboard.create. */
  createDashboard: cap("chart.dashboard.create"),
  /** Create an ML experiment (experiment-service POST /experiments). Model Builder
   * holds experiment.experiment.create. */
  createExperiment: cap("experiment.experiment.create"),
  /** Submit a batch inference job (inference-service POST /inferences). Model
   * Builder holds inference.job.create. */
  createInferenceJob: cap("inference.job.create"),
  /**
   * REQUEST a model-version promotion (experiment-service POST
   * /models/{id}/versions/{v}/promote → guard experiment.model.update). This only
   * OPENS a pending promotion; a SEPARATE person then approves it via
   * experiment.promotion.approve (four-eyes — self-approval is forbidden by the
   * service). NB: NO seeded role holds EITHER grant — not even Model Builder or
   * Admin's explicit list — so this control stays hidden for every human persona
   * until the grants are added (see the RBAC note in the wave report).
   */
  promoteModel: cap("experiment.model.update"),
  /**
   * APPROVE/REJECT a pending promotion (experiment-service POST
   * /promotions/{id}/decision → guard experiment.promotion.approve). The
   * service also forbids self-approval (four-eyes) — the UI additionally
   * hides this for the promotion's own requester (see the approval-queue
   * panel on the model detail page), not just gate on the capability.
   */
  decidePromotion: cap("experiment.promotion.approve"),
  /** Subscribe a dashboard to a scheduled email digest (notification-service
   * POST /reports). Case Manager, Insights User and Model Builder all hold
   * notification.report.create, mirroring their chart.dashboard.create grant. */
  createReportSubscription: cap("notification.report.create"),
  /** Pause/resume/edit a report subscription (notification-service PATCH /reports/{id}). */
  updateReportSubscription: cap("notification.report.update"),
  /** Delete a report subscription (notification-service DELETE /reports/{id}). */
  deleteReportSubscription: cap("notification.report.delete"),
  /** Create a semantic model (semantic-service POST /models). */
  createSemanticModel: cap("semantic.model.create"),
  /** Edit a semantic model's header/draft definition (semantic-service PATCH
   * /models/{id}, PATCH .../versions/{v}, POST .../versions). */
  updateSemanticModel: cap("semantic.model.update"),
  /** Delete a semantic model (semantic-service DELETE /models/{id}). */
  deleteSemanticModel: cap("semantic.model.delete"),
  /** Submit a semantic model draft for review (semantic-service POST
   * .../submit) — same update grant governs the author-side workflow. */
  submitSemanticModelVersion: cap("semantic.model.update"),
  /** Approve/reject an in-review semantic model version (semantic-service POST
   * .../approve|reject). The service ALSO forbids self-review (four-eyes,
   * SEM-FR-007) — the UI hides this for the version's own author in addition
   * to gating on the capability (see SemanticVersionReview). */
  approveSemanticModelVersion: cap("semantic.model.approve"),
  /** Compile / preview a model (semantic-service POST /compile). */
  compileSemanticModel: cap("semantic.compile.execute"),

  /**
   * Set/lift an AGENT kill switch (agent-runtime POST|DELETE
   * /registry/kill-switches). agent-runtime now authorizes these on the rbac
   * ai.agent.admin capability (P4 — operators still bypass by scope server-side),
   * so we gate on the capability, letting a tenant-defined custom role carrying
   * it unlock the control instead of only the built-in Admin.
   */
  createAgentKillSwitch: cap("ai.agent.admin"),
  liftAgentKillSwitch: cap("ai.agent.admin"),
  /** Set a tool kill switch (tool-plane POST /kill-switches). */
  createToolKillSwitch: cap("tool.kill.create"),
  /** Lift a tool kill switch (tool-plane DELETE /kill-switches/{id}). */
  liftToolKillSwitch: cap("tool.kill.delete"),

  /** Browse/search agent memory records (memory-service GET /memories). */
  browseMemory: cap("memory.memory.read"),
  /** View tenant memory stats (memory-service GET /stats). */
  viewMemoryStats: cap("memory.stats.read"),
  /**
   * Start a right-to-be-forgotten erasure (memory-service POST /erasure) —
   * compliance-sensitive, IRREVERSIBLE. Gated on memory.erasure.create.
   */
  requestMemoryErasure: cap("memory.erasure.create"),

  /** Run the authz "why was I denied" debug trace (rbac-service POST
   * /authz/explain). Needs audit.log.read. */
  explainAuthz: cap("audit.log.read"),

  /** Verify chain integrity for one tenant-day (audit-service POST
   * /audit/verify). */
  verifyChainIntegrity: cap("audit.chain.execute"),
  /** Generate a SOC2 or AI-decision-log compliance pack (audit-service POST
   * /compliance/*). */
  generateCompliancePack: cap("audit.compliance.read"),

  // ==========================================================================
  // Tier 2a: eval (eval-service) — eval flywheel gates.
  // ==========================================================================
  /** Read/create a suite (eval-service requires eval.suite.write for BOTH the
   * GET and the POST route — there is no separate read scope). */
  viewEvalSuite: cap("eval.suite.write"),
  createEvalSuite: cap("eval.suite.write"),
  /** Read runs (eval-service GET /runs, /runs/{id}). */
  viewEvalRuns: cap("eval.run.read"),
  /** Start/cancel a real scoring run (eval-service POST /runs, /runs/{id}/cancel). */
  createEvalRun: cap("eval.run.execute"),
  cancelEvalRun: cap("eval.run.execute"),
  /** Read eval dataset versions (eval-service GET /datasets). */
  viewEvalDatasets: cap("eval.dataset.read"),
  /** Create/freeze an eval dataset version (eval-service POST /datasets, .../freeze). */
  manageEvalDatasets: cap("eval.dataset.write"),
  /** Read the case curation queue (eval-service GET /cases). */
  viewEvalCases: cap("eval.case.read"),
  /** Create/promote/attest/reject/retire/edit a curation-queue case (eval-service
   * POST /cases, .../promote, .../attest, .../reject, .../retire, PATCH /cases/{id}). */
  curateEvalCase: cap("eval.case.curate"),
  /** Register/activate a scorer (eval-service POST /scorers, .../activate).
   * The same scope also gates listing (GET /scorers requires eval.scorer.admin). */
  manageEvalScorers: cap("eval.scorer.admin"),
  /** Read a gate verdict (eval-service GET /gates, /gates/{id}). */
  viewEvalGates: cap("eval.gate.read"),
  /** Create/ingest-samples/stop/read a canary A/B comparison (eval-service
   * POST /canaries, .../samples, .../stop, GET /canaries/{id}). */
  manageEvalCanaries: cap("eval.canary.manage"),
  /** Read score-trend series — the model-version scorecard data (eval-service
   * GET /trends). */
  viewEvalTrends: cap("eval.trends.read"),
  /** Read/set SLO rollups + targets (eval-service GET /slos, POST /slos/targets). */
  viewEvalSlos: cap("eval.slo.read"),

  // ==========================================================================
  // Tier 2a: ai-gateway admin — provider catalog, ladders, ai-gateway's own
  // budgets, virtual keys, guardrail policy.
  // ==========================================================================
  /** Read the provider/deployment catalog (ai-gateway GET /admin/providers) —
   * also requires the platform-operator scope server-side (require_operator). */
  viewAiProviders: cap("ai.provider.read"),
  /** Create/patch/drain a provider deployment (ai-gateway POST/PATCH/.../drain)
   * — also requires the platform-operator scope server-side. */
  manageAiProviders: cap("ai.provider.write"),
  /** Read a routing ladder (ai-gateway GET /admin/ladders/{class}). */
  viewAiLadders: cap("ai.ladder.read"),
  /** Replace a routing ladder (ai-gateway PUT /admin/ladders/{class}); a
   * platform-scoped ladder additionally needs the platform-operator scope. */
  manageAiLadders: cap("ai.ladder.write"),
  /** Read ai-gateway's own LLM-spend budgets (ai-gateway GET /admin/budgets) —
   * distinct from usage-service's viewCostPanel/createBudget above. */
  viewAiBudgets: cap("ai.budget.read"),
  /** Create/patch/delete an ai-gateway budget (ai-gateway POST/PATCH/DELETE
   * /admin/budgets); a platform-scoped budget additionally needs the
   * platform-operator scope. */
  manageAiBudgets: cap("ai.budget.write"),
  /** Read live spend against ai-gateway budgets (ai-gateway GET /admin/spend). */
  viewAiSpend: cap("ai.spend.read"),
  /** Read the virtual-key list (ai-gateway GET /admin/keys) — never carries secrets. */
  viewAiKeys: cap("ai.key.read"),
  /** Issue/revoke/rotate a virtual key (ai-gateway POST /admin/keys, .../revoke,
   * .../rotate) — issuing/rotating shows the secret ONCE. */
  manageAiKeys: cap("ai.key.write"),
  /** Read the guardrail policy (ai-gateway GET /admin/guardrails). */
  viewAiGuardrails: cap("ai.guardrail.read"),
  /** Replace the guardrail policy (ai-gateway PUT /admin/guardrails); disabling
   * PII redaction additionally needs the platform-operator scope server-side. */
  manageAiGuardrails: cap("ai.guardrail.write"),

  // ==========================================================================
  // Tier 2b: notification-service — inbox, preferences, rules, webhooks,
  // templates, admin ops. notification-service self-registers its full action
  // manifest with rbac at boot, so every action below exists in the catalog;
  // only inbox/preference/report are bound to seeded non-admin roles — the
  // rule/webhook/template/admin surfaces are effectively Admin-only today
  // (Admin passes via the wildcard).
  // ==========================================================================
  /** Read the in-app inbox + unread count (GET /notifications*). */
  viewNotifications: cap("notification.inbox.read"),
  /** Read own preferences (GET /preferences). */
  viewNotificationPreferences: cap("notification.preference.read"),
  /** Replace own preferences (PUT /preferences). */
  updateNotificationPreferences: cap("notification.preference.update"),
  /** List subscription rules (GET /rules). */
  viewNotificationRules: cap("notification.rule.read"),
  /** Create a subscription rule (POST /rules). */
  createNotificationRule: cap("notification.rule.create"),
  /** Patch a subscription rule (PATCH /rules/{id}). */
  updateNotificationRule: cap("notification.rule.update"),
  /** Delete a subscription rule (DELETE /rules/{id}). */
  deleteNotificationRule: cap("notification.rule.delete"),
  /** List webhooks + delivery history (GET /webhooks*). */
  viewNotificationWebhooks: cap("notification.webhook.read"),
  /** Register a webhook endpoint (POST /webhooks — real challenge handshake). */
  createNotificationWebhook: cap("notification.webhook.create"),
  /** Patch a webhook / rotate its secret (PATCH, POST .../rotate-secret). */
  updateNotificationWebhook: cap("notification.webhook.update"),
  /** Delete a webhook endpoint (DELETE /webhooks/{id}). */
  deleteNotificationWebhook: cap("notification.webhook.delete"),
  /** Manually requeue a delivery (POST .../redeliver). */
  redeliverNotificationWebhook: cap("notification.webhook.execute"),
  /** List template versions + render previews (GET /templates, POST .../preview). */
  viewNotificationTemplates: cap("notification.template.read"),
  /** Create a draft template version (POST /templates). */
  createNotificationTemplate: cap("notification.template.create"),
  /** Publish a template version (POST /templates/{key}/publish). */
  publishNotificationTemplate: cap("notification.template.update"),
  /** Delivery stats + suppression list (GET /admin/stats, /admin/suppressions). */
  viewNotificationOps: cap("notification.admin.read"),
  /** Clear an email suppression (DELETE /admin/suppressions). */
  clearEmailSuppression: cap("notification.suppression.delete"),

  // ==========================================================================
  // Tier 2b: tool-plane registry admin — catalog lifecycle, enablement, BYO.
  // ==========================================================================
  /** Browse the tool catalog / versions / health / schema (GET /tools*). */
  viewToolCatalog: cap("tool.tool.read"),
  /** Register a catalog tool (POST /tools). */
  registerTool: cap("tool.tool.create"),
  /** Add/publish/deprecate a version (POST /tools/{id}/versions, .../publish,
   * .../deprecate). */
  updateToolVersion: cap("tool.tool.update"),
  /** Retire a version (POST .../retire) — permanent removal from the callable set. */
  retireToolVersion: cap("tool.tool.delete"),
  /** Toggle the caller-tenant's enablement (PUT /tenants/self/tools/{id}). */
  setToolEnablement: cap("tool.enablement.update"),
  /** Submit an external tool for review (POST /byo). */
  submitByoTool: cap("tool.byo.create"),
  /** List + decide the BYO queue (GET /byo, POST /byo/{id}/approve|reject). */
  decideByoTool: cap("tool.byo.approve"),

  // ==========================================================================
  // Tier 2b: agent-runtime catalog/registry. The tenant-level surfaces now
  // authorize on the rbac ai.agent.* capability (P4), so they gate on the cap —
  // a tenant custom role carrying it unlocks them, not only the built-in Admin.
  // Publishing an agent VERSION stays platform-operator (agent catalog is
  // platform-owned), so it remains an Admin-role gate.
  // ==========================================================================
  /** Browse agent definitions/versions (GET /registry/agents* — ai.agent.read). */
  viewAgentCatalog: cap("ai.agent.read"),
  /** Publish an agent version (POST .../publish — operator scope downstream). */
  publishAgentVersion: role(ADMIN_ROLE),
  /** Read/write the caller-tenant's per-agent config (GET/PUT
   * /registry/tenants/self/agents/{key} — ai.agent.admin downstream). */
  manageTenantAgentConfig: cap("ai.agent.admin"),
  /** Author a tenant custom agent + guardrail envelope (BRD 53 inc2b, POST
   * /registry/tenants/self/agents — ai.agent.admin downstream). */
  createCustomAgent: cap("ai.agent.admin"),
  /** Run history list (agent-runtime GET /runs) is open to any tenant
   * principal downstream; the browse page reuses the proposal-inbox read
   * gate so it appears exactly for personas who work with agent activity. */
  viewAgentRunHistory: cap("ai.proposal.read"),

  // ==========================================================================
  // Tier 4a: data-plane secondary CRUD/lifecycle — saved-query authoring,
  // execution history, ingestion schedules + run lifecycle, connection edit,
  // dataset consumers/versions/re-profile, verified NL↔SQL pairs, semantic
  // bootstrap, pipeline run/template lifecycle.
  // ==========================================================================
  /** Author a saved query (query-service POST /queries). */
  createSavedQuery: cap("query.query.create"),
  /** Edit a saved query — every update opens a new immutable version (PATCH /queries/{id}). */
  updateSavedQuery: cap("query.query.update"),
  /** Soft-delete a saved query (DELETE /queries/{id}). */
  deleteSavedQuery: cap("query.query.delete"),
  /** Browse execution history (query-service GET /executions). */
  viewQueryExecutions: cap("query.execution.read"),
  /** Cancel a queued/running execution (POST /executions/{id}/cancel — cancel
   * rides the execute capability, not a separate verb). */
  cancelQueryExecution: cap("query.execution.execute"),
  /** Tenant query-stats rollup (GET /stats/queries). */
  viewQueryStats: cap("query.stats.read"),

  /** Edit a saved connection (ingestion-service PATCH /connections/{id});
   * secrets merge write-only. */
  updateConnection: cap("ingestion.connection.update"),
  /** Live sample-rows preview from a saved connection (POST .../preview —
   * read-authorized, never persisted). */
  previewConnection: cap("ingestion.connection.read"),
  /** Cancel an uncommitted ingestion run (POST /ingestions/{id}/cancel). */
  cancelIngestion: cap("ingestion.ingestion.execute"),
  /** Retry a FAILED ingestion as a fresh cloned run (POST .../retry). */
  retryIngestion: cap("ingestion.ingestion.execute"),
  /** Re-run a TERMINAL ingestion's config as a new job (POST .../reingest —
   * creates a new run, so it rides the create verb). */
  reingestIngestion: cap("ingestion.ingestion.create"),
  /** Browse recurring schedules (ingestion-service GET /schedules). */
  viewIngestionSchedules: cap("ingestion.schedule.read"),
  /** Create a recurring schedule (POST /schedules). */
  createIngestionSchedule: cap("ingestion.schedule.create"),
  /** Edit/pause/resume a schedule (PATCH /schedules/{id}, POST .../pause|resume). */
  updateIngestionSchedule: cap("ingestion.schedule.update"),
  /** Delete a schedule (DELETE /schedules/{id}). */
  deleteIngestionSchedule: cap("ingestion.schedule.delete"),
  /** Force one immediate fire (POST /schedules/{id}/run_now). */
  runIngestionScheduleNow: cap("ingestion.schedule.execute"),

  /** Enqueue a decision write-back (ingestion-service POST /writebacks, INS-FR-061). */
  createWriteback: cap("ingestion.writeback.create"),
  /** Browse the write-backs admin list (GET /writebacks). */
  viewWritebacks: cap("ingestion.writeback.read"),
  /** Approve/reject a pending write-back (four-eyes; POST .../approve|reject). */
  approveWriteback: cap("ingestion.writeback.approve"),
  /** Retry a failed write-back delivery (POST .../retry). */
  retryWriteback: cap("ingestion.writeback.execute"),

  /** Manually trigger a dataset re-profile (dataset-service POST
   * /datasets/{id}/versions/{n}/profile, 202 async). */
  reprofileDataset: cap("dataset.profile.execute"),

  /** Browse verified NL↔SQL pairs (semantic-service GET /verified-queries). */
  viewVerifiedQueries: cap("semantic.verified_query.read"),
  /** Author a verified pair as a draft (POST /verified-queries). */
  createVerifiedQuery: cap("semantic.verified_query.create"),
  /** Edit a draft/rejected pair, submit for review, or archive (PATCH, POST
   * .../submit, POST .../archive — all authorized as update). */
  updateVerifiedQuery: cap("semantic.verified_query.update"),
  /** Approve/reject a pending pair (POST .../approve|reject). The service ALSO
   * forbids self-review (four-eyes, SEM-FR-040) — the UI hides this for the
   * pair's own author in addition to gating on the capability. */
  approveVerifiedQuery: cap("semantic.verified_query.approve"),
  /** Auto-draft a semantic model from dataset schemas (semantic-service POST
   * /models/{id}/bootstrap — rewrites the open draft, so it rides update). */
  bootstrapSemanticModel: cap("semantic.model.update"),

  // ==========================================================================
  // Tier 4b: case ops — lifecycle transitions, comments/timeline, CSV export,
  // disposition catalog, custom case-fields, SLA policy (case-service).
  // ==========================================================================
  /** Assign/reassign/unassign a case (case-service POST /cases/{id}/assign|unassign). */
  assignCase: cap("case.case.assign"),
  /** Start work on a case (case-service POST /cases/{id}/start — the execute verb). */
  startCase: cap("case.case.execute"),
  /** Resolve/reopen/close/escalate a case + add/edit/delete comments — the
   * service authorizes all of these as the single update verb. */
  manageCase: cap("case.case.update"),
  /** Start an async CSV export + download the result (case-service POST
   * /cases/export, GET /operations/{id}/download). */
  exportCases: cap("case.case.export"),
  /** Create a disposition catalog entry (case-service POST /dispositions). */
  manageDispositions: cap("case.disposition.create"),
  /** Edit a disposition (case-service PATCH /dispositions/{id}). */
  updateDisposition: cap("case.disposition.update"),
  /** Create/delete custom case-field configs (case-service POST|DELETE
   * /case-fields — authorized as the case update verb). */
  manageCaseFields: cap("case.case.update"),
  /** Replace the workspace SLA policy (case-service PUT /sla-policy). */
  manageSlaPolicy: cap("case.case.admin"),

  // ==========================================================================
  // Tier 4b: identity/rbac admin — user + service-account lifecycle, workspace
  // lifecycle + content grants, custom roles, content groups.
  // ==========================================================================
  /** Rename/deactivate/resend-invite/delete a directory user (identity-service
   * PATCH /users/{id}, POST .../deactivate, POST .../invite/resend, DELETE). */
  manageUserLifecycle: cap("identity.user.admin"),
  /** Create/rotate/revoke a service account (identity-service POST
   * /service-accounts, POST .../rotate, DELETE). The api_key is shown ONCE. */
  manageServiceAccounts: cap("identity.service_account.admin"),
  /** Edit a workspace's name/description/public flag + link/unlink content
   * groups (rbac-service PATCH /workspaces/{id}, PUT|DELETE
   * .../content-groups/{gid}). */
  updateWorkspace: cap("rbac.workspace.update"),
  /** Archive/restore a workspace (rbac-service POST /workspaces/{id}/archive|restore). */
  adminWorkspace: cap("rbac.workspace.admin"),
  /** Create a custom role (rbac-service POST /roles). */
  createRole: cap("rbac.role.create"),
  /** Rename a custom role / replace its action set (rbac-service PATCH
   * /roles/{id}, PUT /roles/{id}/actions). System roles are immutable — the
   * UI additionally hides these controls for system rows. */
  updateRole: cap("rbac.role.update"),
  /** Delete a custom role (rbac-service DELETE /roles/{id}). */
  deleteRole: cap("rbac.role.delete"),
  /** Look up effective access for a resource URN (rbac-service GET
   * /grants?resource_urn=). */
  listGrants: cap("rbac.grant.list"),
  /** Create a content grant (rbac-service POST /grants). */
  createGrant: cap("rbac.grant.create"),
  /** Delete a content grant (rbac-service DELETE /grants/{id}). */
  deleteGrant: cap("rbac.grant.delete"),
  /** Create a content-type group (rbac-service POST /groups, group_type=content
   * — same create action as Teams; the type is what differs). */
  createContentGroup: cap("rbac.group.create"),

  // ==========================================================================
  // Tier 4b: ml ops — run registration/notes, experiment edit, model cards
  // (experiment-service) + inference job lifecycle and scoring schedules
  // (inference-service).
  // ==========================================================================
  /** Register a finished run as a model version (experiment-service POST
   * /experiments/{eid}/runs/{rid}/register). */
  registerModel: cap("experiment.model.create"),
  /** Edit an experiment's name/description/note (experiment-service PATCH
   * /experiments/{id}). */
  updateExperiment: cap("experiment.experiment.update"),
  /** Set/delete a run's note (experiment-service PUT|DELETE /runs/{id}/note —
   * both authorized as experiment.run.update). */
  updateRun: cap("experiment.run.update"),
  /** Edit a model card's human overlay (experiment-service PATCH
   * /models/{id}/versions/{v}/card). */
  updateModelCard: cap("experiment.model_card.update"),
  /** Cancel a queued/submitted/running inference job (inference-service POST
   * /inferences/{id}/cancel — the update verb). */
  cancelInferenceJob: cap("inference.job.update"),
  /** Delete a TERMINAL inference job (inference-service DELETE /inferences/{id}).
   * Retry rides createInferenceJob above (POST .../retry → inference.job.create). */
  deleteInferenceJob: cap("inference.job.delete"),
  /** Browse scoring schedules + their fire history (inference-service GET
   * /schedules, /schedules/{id}/fires). */
  readInferenceSchedules: cap("inference.schedule.read"),
  /** Create a scoring schedule (inference-service POST /schedules). */
  createInferenceSchedule: cap("inference.schedule.create"),
  /** Edit/pause/resume/trigger a schedule (inference-service PATCH
   * /schedules/{id}, POST .../pause|resume|trigger — all inference.schedule.update;
   * trigger is NOT a separate execute action). */
  updateInferenceSchedule: cap("inference.schedule.update"),
  /** Delete a schedule (inference-service DELETE /schedules/{id}). */
  deleteInferenceSchedule: cap("inference.schedule.delete"),

  /** Terminate a live pipeline run (pipeline-orchestrator PUT /runs/{id}/terminate). */
  terminatePipelineRun: cap("pipeline.run.execute"),
  /** Retry a FAILED pipeline run — creates a NEW run (POST /runs/{id}/retry). */
  retryPipelineRun: cap("pipeline.run.create"),
  /** View a run's compiled manifest + resolved parameters (GET /runs/{id}/manifest). */
  viewPipelineRunManifest: cap("pipeline.run.read"),
  /** Clone a template (POST /pipelines/{id}/clone). */
  clonePipelineTemplate: cap("pipeline.template.create"),
  /** Activate a template version / restore an archived template (POST
   * .../versions/{v}/activate, PATCH .../restore). */
  updatePipelineTemplate: cap("pipeline.template.update"),
  /** Compile the active version to an Argo manifest (POST /pipelines/{id}/compile). */
  compilePipelineTemplate: cap("pipeline.template.execute"),
  /** Archive a template (DELETE /pipelines/{id}; system templates 409). */
  deletePipelineTemplate: cap("pipeline.template.delete"),

  /** Browse recurring pipeline schedules (pipeline-orchestrator GET
   * /pipeline-schedules). */
  viewPipelineSchedules: cap("pipeline.schedule.read"),
  /** Create a recurring pipeline schedule (POST /pipeline-schedules). */
  createPipelineSchedule: cap("pipeline.schedule.create"),
  /** Pause/resume a schedule (POST /pipeline-schedules/{id}/pause|resume). */
  updatePipelineSchedule: cap("pipeline.schedule.update"),
  /** Delete a schedule (DELETE /pipeline-schedules/{id}). */
  deletePipelineSchedule: cap("pipeline.schedule.delete"),
  /** Force one immediate fire (POST /pipeline-schedules/{id}/run-now). */
  runPipelineScheduleNow: cap("pipeline.schedule.execute"),
} as const;
