/**
 * Query-key factory (UI-FR-040): keys namespaced [module, resource, id|filters].
 * The EventBridge patches caches by matching against these keys.
 */
export const qk = {
  me: () => ["platform", "me"] as const,
  tenants: () => ["platform", "tenants"] as const,
  user: (id: string) => ["platform", "user", id] as const,

  datasets: (filters: unknown) => ["data", "datasets", filters] as const,
  dataset: (id: string) => ["data", "dataset", id] as const,

  connectorTypes: () => ["data", "connectorTypes"] as const,
  connections: (filters: unknown) => ["data", "connections", filters] as const,
  connection: (id: string) => ["data", "connection", id] as const,

  ingestions: (filters: unknown) => ["data", "ingestions", filters] as const,
  ingestion: (id: string) => ["data", "ingestion", id] as const,
  writebacks: (filters: unknown) => ["data", "writebacks", filters] as const,
  writeback: (id: string) => ["data", "writeback", id] as const,
  decisionModels: () => ["data", "decisionModels"] as const,
  decisionModel: (id: string) => ["data", "decisionModel", id] as const,
  decisionModelVersions: (id: string) => ["data", "decisionModelVersions", id] as const,
  resolutionRuns: (datasetId: string) => ["data", "resolutionRuns", datasetId] as const,
  resolutionRun: (id: string) => ["data", "resolutionRun", id] as const,
  mergeCandidates: (runId: string) => ["data", "mergeCandidates", runId] as const,
  ontologyEntities: (workspaceId: string) => ["data", "ontologyEntities", workspaceId] as const,
  modelArchetypes: (workspaceId: string) => ["ml", "modelArchetypes", workspaceId] as const,
  packs: () => ["packs", "catalog"] as const,
  pack: (name: string) => ["packs", "pack", name] as const,
  packInstalls: (workspaceId: string) => ["packs", "installs", workspaceId] as const,
  packInstall: (id: string) => ["packs", "install", id] as const,
  upload: (id: string) => ["data", "upload", id] as const,
  datasetLineage: (urn: string, direction: string) => ["data", "lineage", urn, direction] as const,

  savedQueries: (filters: unknown) => ["data", "savedQueries", filters] as const,
  savedQuery: (id: string) => ["data", "savedQuery", id] as const,
  savedQueryVersions: (queryId: string) => ["data", "savedQueryVersions", queryId] as const,
  queryExecutions: (filters: unknown) => ["data", "queryExecutions", filters] as const,
  queryExecution: (id: string) => ["data", "queryExecution", id] as const,
  queryStats: (since?: string) => ["data", "queryStats", since ?? null] as const,

  ingestionSchedules: () => ["data", "ingestionSchedules"] as const,
  ingestionSchedule: (id: string) => ["data", "ingestionSchedule", id] as const,
  connectionPreview: (id: string, input: unknown) => ["data", "connectionPreview", id, input] as const,

  datasetConsumers: (id: string) => ["data", "datasetConsumers", id] as const,
  datasetVersions: (datasetId: string) => ["data", "datasetVersions", datasetId] as const,
  similarDatasets: (datasetId: string) => ["data", "similarDatasets", datasetId] as const,

  datasetSchema: (datasetId: string, version?: number) => ["data", "datasetSchema", datasetId, version ?? null] as const,
  datasetRows: (datasetId: string, vars: unknown) => ["data", "datasetRows", datasetId, vars] as const,
  datasetAggregate: (datasetId: string, vars: unknown) => ["data", "datasetAggregate", datasetId, vars] as const,

  pipelineStepTypes: () => ["pipelines", "stepTypes"] as const,
  algorithmTemplates: () => ["pipelines", "algorithmTemplates"] as const,
  pipelines: (filters: unknown) => ["pipelines", "templates", filters] as const,
  pipeline: (id: string) => ["pipelines", "template", id] as const,
  pipelineRuns: (filters: unknown) => ["pipelines", "runs", filters] as const,
  pipelineRunManifest: (id: string) => ["pipelines", "runManifest", id] as const,
  pipelineTemplateVersions: (templateId: string) => ["pipelines", "templateVersions", templateId] as const,
  pipelineSchedules: () => ["pipelines", "schedules"] as const,

  cases: (filters: unknown) => ["cases", "list", filters] as const,
  case: (id: string) => ["cases", "case", id] as const,
  // Tier 4b: case ops
  caseTimeline: (caseId: string) => ["cases", "timeline", caseId] as const,
  caseOperation: (id: string) => ["cases", "operation", id] as const,
  dispositions: () => ["cases", "dispositions"] as const,
  caseFields: (queryUrn?: string) => ["cases", "caseFields", queryUrn ?? null] as const,

  proposals: (filters: unknown) => ["agentic", "proposals", filters] as const,
  proposal: (id: string) => ["agentic", "proposal", id] as const,
  agentRun: (id: string) => ["agentic", "run", id] as const,
  learningLoop: () => ["agentic", "learningLoop"] as const,

  experiments: (filters: unknown) => ["ml", "experiments", filters] as const,
  experiment: (id: string) => ["ml", "experiment", id] as const,
  archivedExperiments: (filters: unknown) => ["ml", "archivedExperiments", filters] as const,
  run: (id: string) => ["ml", "run", id] as const,
  models: (filters: unknown) => ["ml", "models", filters] as const,
  model: (id: string) => ["ml", "model", id] as const,
  promotions: (modelId: string, version: number) => ["ml", "promotions", modelId, version] as const,
  inferenceJobs: (filters: unknown) => ["ml", "inferenceJobs", filters] as const,
  inferenceJob: (id: string) => ["ml", "inferenceJob", id] as const,
  // Tier 4b: ml ops
  bestRun: (experimentId: string, metric: string, direction: string) =>
    ["ml", "bestRun", experimentId, metric, direction] as const,
  compareRuns: (runIds: readonly string[]) => ["ml", "compareRuns", [...runIds].sort()] as const,
  runNote: (runId: string) => ["ml", "runNote", runId] as const,
  runArtifacts: (runId: string) => ["ml", "runArtifacts", runId] as const,
  runMetricHistory: (runId: string) => ["ml", "runMetricHistory", runId] as const,
  modelCard: (modelId: string, version: number) => ["ml", "modelCard", modelId, version] as const,
  inferenceSchedules: () => ["ml", "inferenceSchedules"] as const,
  inferenceScheduleFires: (scheduleId: string) => ["ml", "inferenceScheduleFires", scheduleId] as const,

  dashboards: (workspaceId: string, filters: unknown) =>
    ["dashboards", "list", workspaceId, filters] as const,
  dashboard: (id: string) => ["dashboards", "dashboard", id] as const,
  chartDrillTarget: (chartId: string, dimension: string) =>
    ["dashboards", "chartDrillTarget", chartId, dimension] as const,
  archivedDashboards: (workspaceId: string) => ["dashboards", "archived", workspaceId] as const,
  chartTypes: () => ["dashboards", "chartTypes"] as const,
  semanticModels: (workspaceId: string) => ["dashboards", "semanticModels", workspaceId] as const,
  semanticModel: (name: string) => ["dashboards", "semanticModel", name] as const,

  reportSubscriptions: (dashboardId?: string) => ["dashboards", "reportSubscriptions", dashboardId ?? null] as const,

  costPanel: (workspaceId: string, from: string, to: string) =>
    ["usage", "costPanel", workspaceId, from, to] as const,
  budgets: () => ["usage", "budgets"] as const,
  rateCards: () => ["usage", "rateCards"] as const,
  anomalies: (status?: string) => ["usage", "anomalies", status ?? null] as const,

  // semantic model authoring
  semanticModelList: (filters: unknown) => ["semantic", "models", filters] as const,
  semanticModelDetail: (id: string) => ["semantic", "model", id] as const,
  semanticModelVersions: (modelId: string) => ["semantic", "versions", modelId] as const,
  semanticModelVersion: (modelId: string, versionNo: number) =>
    ["semantic", "version", modelId, versionNo] as const,
  verifiedQueries: (filters: unknown) => ["semantic", "verifiedQueries", filters] as const,
  verifiedQuerySearch: (vars: unknown) => ["semantic", "verifiedQuerySearch", vars] as const,
  semanticOperation: (id: string) => ["semantic", "operation", id] as const,

  // admin
  users: (filters: unknown) => ["admin", "users", filters] as const,
  assignableUsers: () => ["assignableUsers"] as const,
  workspaces: (filters: unknown) => ["admin", "workspaces", filters] as const,
  groups: (filters: unknown) => ["admin", "groups", filters] as const,
  groupMembers: (groupId: string) => ["admin", "groupMembers", groupId] as const,
  groupRoles: (groupId: string) => ["admin", "groupRoles", groupId] as const,
  userGroups: (userId: string) => ["admin", "userGroups", userId] as const,
  roles: () => ["admin", "roles"] as const,
  serviceAccounts: () => ["admin", "serviceAccounts"] as const,
  tenant: (id: string) => ["admin", "tenant", id] as const,
  // Tier 4b: identity/rbac admin — effective access lookup per resource URN.
  contentGrants: (resourceUrn: string) => ["admin", "contentGrants", resourceUrn] as const,
  auditEvents: (filters: unknown) => ["admin", "auditEvents", filters] as const,
  agentKillSwitches: () => ["admin", "agentKillSwitches"] as const,
  toolKillSwitches: () => ["admin", "toolKillSwitches"] as const,
  memories: (filters: unknown) => ["admin", "memories", filters] as const,
  memory: (id: string) => ["admin", "memory", id] as const,
  memoryStats: () => ["admin", "memoryStats"] as const,
  erasure: (id: string) => ["admin", "erasure", id] as const,
  complianceOperation: (id: string) => ["admin", "complianceOperation", id] as const,

  // Tier 2a: eval (eval-service)
  evalSuite: (suiteId: string, version?: number) => ["eval", "suite", suiteId, version ?? null] as const,
  evalRuns: (filters: unknown) => ["eval", "runs", filters] as const,
  evalRun: (id: string) => ["eval", "run", id] as const,
  evalDatasets: (filters: unknown) => ["eval", "datasets", filters] as const,
  evalCases: (filters: unknown) => ["eval", "cases", filters] as const,
  evalScorers: () => ["eval", "scorers"] as const,
  evalCanary: (comparisonId: string) => ["eval", "canary", comparisonId] as const,
  evalTrends: (agentKey: string, scorer?: string, window?: string) =>
    ["eval", "trends", agentKey, scorer ?? null, window ?? null] as const,
  evalSlos: (agentKey: string, window?: string) => ["eval", "slos", agentKey, window ?? null] as const,

  // Tier 2b: notification-service (inbox/preferences/rules/webhooks/templates/ops)
  notifications: (filters: unknown) => ["notifications", "inbox", filters] as const,
  notificationUnreadCount: () => ["notifications", "unreadCount"] as const,
  notificationPreferences: () => ["notifications", "preferences"] as const,
  notificationRules: () => ["notifications", "rules"] as const,
  notificationWebhooks: () => ["notifications", "webhooks"] as const,
  notificationWebhookDeliveries: (webhookId: string) => ["notifications", "webhookDeliveries", webhookId] as const,
  notificationTemplates: (key: string) => ["notifications", "templates", key] as const,
  notificationDeliveryStats: (window?: string) => ["notifications", "deliveryStats", window ?? null] as const,
  emailSuppressions: () => ["notifications", "suppressions"] as const,

  // Tier 2b: tool-plane registry admin
  tools: (filters: unknown) => ["tools", "catalog", filters] as const,
  toolHealth: (toolId: string) => ["tools", "health", toolId] as const,
  toolSchema: (toolId: string, version?: string) => ["tools", "schema", toolId, version ?? null] as const,
  byoSubmissions: (status?: string) => ["tools", "byo", status ?? null] as const,

  // Tier 2b: agent-runtime catalog/registry
  agentDefinitions: () => ["agentic", "definitions"] as const,
  agentCeilings: () => ["agentic", "ceilings"] as const,
  agentVersions: (agentKey: string) => ["agentic", "versions", agentKey] as const,
  tenantAgentConfig: (agentKey: string) => ["agentic", "tenantConfig", agentKey] as const,
  agentRuns: (filters: unknown) => ["agentic", "runs", filters] as const,

  // Tier 2a: ai-gateway admin
  aiProviders: () => ["aigateway", "providers"] as const,
  aiLadder: (requestClass: string) => ["aigateway", "ladder", requestClass] as const,
  aiBudgets: (scopeType?: string) => ["aigateway", "budgets", scopeType ?? null] as const,
  aiSpend: (scopeType: string, scopeRef: string, window?: string) =>
    ["aigateway", "spend", scopeType, scopeRef, window ?? null] as const,
  aiCostBreakdown: (windowHours: number) => ["aigateway", "costBreakdown", windowHours] as const,
  aiKeys: () => ["aigateway", "keys"] as const,
  aiGuardrailPolicy: () => ["aigateway", "guardrailPolicy"] as const,
} as const;
