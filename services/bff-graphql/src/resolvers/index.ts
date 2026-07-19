/**
 * Resolvers. Each maps 1:1 onto a documented domain-service endpoint and does
 * nothing but call the real client, reshape the response, and (for nested
 * fields) hydrate through a dataloader. No authz, no business logic.
 */
import type { GraphQLResolveInfo } from "graphql";
import type { GraphQLContext } from "../context.js";
import { DownstreamError, ErrorCode, gqlError } from "../errors/errors.js";
import { toConnection, toLimitCursor, type ConnectionArgs } from "../pagination.js";
import { JSONScalar, DateTimeScalar, DateScalar } from "../schema/scalars.js";
import type { ChartDataDTO, ChartDTO } from "../clients/chart.js";
import type { ChartSourceInputBody } from "../clients/chart.js";
import { budgetScopeString } from "../clients/usage.js";
import {
  mapUser, mapDataset, mapProfile, mapCase, mapDashboard, mapChart,
  // Tier 4b: case ops (lifecycle, comments/timeline, export, catalog, SLA).
  mapCaseComment, mapCaseActivity, mapCaseOperation, mapDisposition, mapCaseField, mapCaseSlaPolicy,
  mapChartType, mapChartShapedData,
  mapProposal, mapAgentRun, mapAgentKillSwitch, mapToolKillSwitch, mapExperiment, mapRun, mapModel,
  mapRegistryModel, mapPromotion, mapInferenceJob,
  // Tier 4b: ml ops (register/notes/artifacts + validate/schedules).
  mapRegisterModelResult, mapRunNote, mapRunArtifact, mapCompatibilityReport, mapInferenceSchedule,
  mapMemoryRecord, mapErasureRequest,
  mapConnectorType, mapConnection, mapConnectionTest, mapWriteback,
  mapIngestion, mapUpload, mapLineage, mapSavedQuery, mapQueryResult,
  // Tier 4a: data-plane secondary CRUD/lifecycle.
  mapSavedQueryVersion, mapQueryExecution, mapQueryStats,
  mapIngestionSchedule, mapScheduleRunNow, mapConnectionPreview,
  mapDatasetConsumers, mapSimilarDataset, mapDatasetVersion, mapReprofile,
  mapVerifiedQuery, mapVerifiedQuerySearchHit, mapSemanticOperation,
  mapPipelineTemplateVersion, mapCompiledPipelineManifest, mapPipelineRunManifest,
  mapPipelineStepType, mapAlgorithmTemplate, mapPipelineTemplate, mapPipelineRun, mapValidationReport,
  mapPipelineSchedule,
  mapSemanticModel,
  mapDatasetSchema, mapSemanticModelSummary, mapSemanticModelVersion, mapSemanticCompileResult,
  mapWorkspace, mapGroup, mapGroupMember, mapRole, mapAuthzExplanation, mapServiceAccount, mapTenant, mapAuditEvent,
  // Tier 4b: identity/rbac admin (lifecycle, roles, grants, bulk membership).
  mapCreatedServiceAccount, mapEffectiveAccessEntry, mapContentGrant, mapBulkGroupMembershipResult,
  mapBudget, mapRateCard, mapAnomaly, mapReportSubscription,
  mapChainVerifyResult, mapComplianceJob,
  decisionAction, urnId,
  mapEvalSuite, mapEvalRun, mapEvalCaseResult, mapEvalDataset, mapEvalCase, mapEvalScorer,
  mapEvalGateResult, mapEvalCanary, mapEvalTrendPoint, mapEvalSloRow,
  mapAiProvider, mapAiLadder, mapAiBudget, mapAiSpendRow, mapAiVirtualKey, mapAiGuardrailPolicy,
  mapAiCostBreakdown, // ADDED: provider-agnostic + cost-detail breakdown
  // Tier 2b: notification-service + tool-plane registry + agent catalog.
  mapNotification, mapNotificationPreferences, mapNotificationRule,
  mapWebhookEndpoint, mapWebhookDelivery, mapNotificationTemplate, mapEmailSuppression,
  mapTool, mapToolVersion, mapToolHealth, mapTenantToolSettings, mapByoSubmission,
  mapAgentDefinition, mapAgentVersionInfo, mapTenantAgentConfig, mapAgentRunListItem,
  mapDecisionModel, mapBatchEvaluate,
  mapResolutionRun, mapResolutionRunDetail, mapResolveEntities, mapMergeCandidate,
  mapEntityMergeProposal, mapMaterializeResolved, mapOntologyEntity,
  mapPack, mapPackInstall, mapPackInstallPlan, mapPackUninstall, mapPackComplete,
} from "../schema/map.js";

/** GraphQL ChartSourceInput (camel) -> chart-service source body (snake). */
interface ChartSourceInputGQL { position: number; sourceType: string; sourceUrn: string }
function mapSourcesInput(sources?: ChartSourceInputGQL[] | null): ChartSourceInputBody[] | undefined {
  return sources?.map((s) => ({ position: s.position, source_type: s.sourceType, source_urn: s.sourceUrn }));
}

/** For nullable single-entity fields, a downstream 404 resolves to null with no
 * error entry — tenant-isolation masking is indistinguishable by design (BR-3). */
async function nullOn404<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch (e) {
    if (e instanceof DownstreamError && e.httpStatus === 404) return null;
    throw e;
  }
}

/** After a sync query execution, fetch its first results page (only when the run
 * actually succeeded) and combine both into one QueryResult. A failed execution
 * carries its error inline — the UI renders it without a second call. */
async function hydrateResult(
  ctx: GraphQLContext,
  exec: { execution_id?: string; id?: string; status?: string },
  limit: number,
) {
  const execId = exec.execution_id ?? exec.id;
  let results = null;
  if (execId && exec.status === "succeeded") {
    results = await ctx.clients.query.results(execId, limit);
  }
  return mapQueryResult(exec, results);
}

const lower = (v?: string | null): string | undefined => (v ? v.toLowerCase() : undefined);

/** Whitelisted aggregation functions for the quick-chart path. */
const QUICK_AGGS = new Set(["count", "sum", "avg", "min", "max"]);

/** DuckDB-quote an identifier (double internal quotes). Callers MUST pass a
 * name already validated against the dataset's real column set. */
const qIdent = (id: string): string => `"${id.replace(/"/g, '""')}"`;

/** Build a governed GROUP-BY over a dataset's {{dataset()}} macro. `dimension`
 * and `measure` must already be validated against the real column set; `agg`
 * against QUICK_AGGS. Values are aggregated in the engine, never in the BFF.
 * Numeric aggregates cast the (string-typed bronze) column to double. */
function buildAggregateSql(args: {
  datasetName: string;
  dimension: string;
  measure: string | null;
  agg: string;
  limit: number;
}): { sql: string; valueLabel: string } {
  const { datasetName, dimension, measure, agg, limit } = args;
  const valueLabel = measure ? `${agg}_${measure}` : agg;
  let valueExpr: string;
  if (agg === "count") {
    valueExpr = measure ? `count(${qIdent(measure)})` : "count(*)";
  } else {
    // sum/avg/min/max need a numeric operand (bronze columns are strings).
    valueExpr = `${agg}(cast(${qIdent(measure as string)} as double))`;
  }
  // Escape single quotes in the dataset name before splicing it into the
  // {{dataset('...')}} macro string. Names are user-controlled (no charset
  // restriction), so a raw `'` would break out of the macro and let arbitrary
  // SQL be spliced between two macro references. Doubling `'` keeps it a literal.
  const safeName = datasetName.replace(/'/g, "''");
  const sql =
    `SELECT ${qIdent(dimension)} AS ${qIdent(dimension)}, ` +
    `${valueExpr} AS ${qIdent(valueLabel)} ` +
    `FROM {{dataset('${safeName}')}} ` +
    `GROUP BY ${qIdent(dimension)} ` +
    `ORDER BY 2 DESC ` +
    `LIMIT ${limit}`;
  return { sql, valueLabel };
}

/** Tier 2b: GraphQL NotificationRuleInput (camel) -> notification-service rule
 * body (snake). Shared by create + update. */
interface NotificationRuleInputGQL {
  scope?: string;
  subjectType?: string;
  subjectId?: string;
  eventTypes?: string[];
  resourceFilter?: { resource_urn_prefix?: string; attrs?: Record<string, string[]> };
  channels?: string[];
  digestEnabled?: boolean;
  digestWindow?: string;
  active?: boolean;
}
function ruleBodyOf(input: NotificationRuleInputGQL) {
  return {
    scope: input.scope,
    subject_type: input.subjectType,
    subject_id: input.subjectId,
    event_types: input.eventTypes,
    resource_filter: input.resourceFilter,
    channels: input.channels,
    digest_enabled: input.digestEnabled,
    digest_window: input.digestWindow,
    active: input.active,
  };
}

/** Tier 4a: GraphQL SavedQueryInput (camel) -> query-service savedQueryReq
 * (snake). Absent fields stay absent so PATCH leaves them unchanged (the Go
 * handler distinguishes nil pointers from set values). */
interface SavedQueryInputGQL {
  name?: string;
  description?: string;
  sqlText?: string;
  variables?: {
    name: string;
    type: string;
    required?: boolean;
    default?: unknown;
    allowedValues?: unknown[];
    min?: number;
    max?: number;
  }[];
  tags?: string[];
  moduleNames?: string[];
}
function savedQueryBodyOf(input: SavedQueryInputGQL) {
  return {
    name: input.name,
    description: input.description,
    sql_text: input.sqlText,
    variables: input.variables?.map((v) => ({
      name: v.name,
      type: v.type,
      required: v.required,
      default: v.default,
      allowed_values: v.allowedValues,
      min: v.min,
      max: v.max,
    })),
    tags: input.tags,
    module_names: input.moduleNames,
  };
}

/** Tier 4a: GraphQL ingestion-schedule inputs (camel) -> schedules.py bodies
 * (snake). Create and update share the timing/template fields. */
interface ScheduleTimingInputGQL {
  cron?: string;
  intervalSeconds?: number;
  timezone?: string;
  ingestionTemplate?: Record<string, unknown>;
  overlapPolicy?: string;
  enabled?: boolean;
}
function scheduleUpdateBodyOf(input: ScheduleTimingInputGQL) {
  return {
    cron: input.cron,
    interval_seconds: input.intervalSeconds,
    timezone: input.timezone,
    ingestion_template: input.ingestionTemplate,
    overlap_policy: input.overlapPolicy as "skip" | "buffer_one" | undefined,
    enabled: input.enabled,
  };
}

/** Resolved viewer caps; `degraded` is true when the rbac lookup failed and the
 * empty roles/capabilities are the fail-closed fallback, not real grants. */
interface ViewerCaps {
  roles: string[];
  capabilities: string[];
  degraded: boolean;
  workspaceName: string;
}

/** The `me` resolver's return shape; carries a per-request memoized caps promise. */
interface ViewerParent {
  userId: string;
  tenantId: string;
  type: string;
  scopes: string[];
  workspaceId: string;
  _caps?: Promise<ViewerCaps>;
  _tenant?: Promise<{ name: string | null; displayName: string | null }>;
  _labels?: Promise<Array<{ key: string; value: string }>>;
}

/** Fetch the caller's rbac roles+capabilities once per request; fail-safe to []
 * but flag the degradation (capsDegraded) so the UI can say "permissions
 * unavailable" instead of silently hiding the whole nav. */
function viewerCaps(parent: ViewerParent, ctx: GraphQLContext): Promise<ViewerCaps> {
  if (!parent._caps) {
    parent._caps = ctx.clients.rbac
      .meCapabilities()
      .then((c) => ({
        roles: c.roles, capabilities: c.capabilities, degraded: false,
        workspaceName: c.workspaceName,
      }))
      .catch(() => ({ roles: [], capabilities: [], degraded: true, workspaceName: "" }));
  }
  return parent._caps;
}

/** Fetch the caller's tenant name once per request (identity /tenants/self);
 * display-only — a failure yields nulls, never an error. */
function viewerTenant(parent: ViewerParent, ctx: GraphQLContext) {
  if (!parent._tenant) {
    parent._tenant = ctx.clients.identity
      .tenantSelf()
      .then((t) => ({ name: t.name ?? null, displayName: t.display_name ?? null }))
      .catch(() => ({ name: null, displayName: null }));
  }
  return parent._tenant;
}

/** Fetch the caller's tenant UI-label overrides once per request (identity
 * /tenants/self/labels). Display-only — a failure yields [] so the UI falls
 * back to its base i18n catalog, never an error. */
function viewerLabels(parent: ViewerParent, ctx: GraphQLContext): Promise<Array<{ key: string; value: string }>> {
  if (!parent._labels) {
    parent._labels = ctx.clients.identity
      .tenantLabels()
      .then((r) => Object.entries(r.labels ?? {}).map(([key, value]) => ({ key, value })))
      .catch(() => []);
  }
  return parent._labels;
}

/** Flatten one data entry into {chart_id, rows, columns, meta, error}. Both the
 * batch endpoint (entry = {chart_id, data: ShapedResult, error}) and the single
 * GET (body = {data: ShapedResult, meta}) nest the shaped rows/columns under a
 * `data` object; read through it so Chart.data sees real rows either way. */
function shapedOf(entry: any): ChartDataDTO {
  if (!entry || typeof entry !== "object") return {};
  const shaped = entry.data && typeof entry.data === "object" && !Array.isArray(entry.data) ? entry.data : entry;
  return {
    chart_id: entry.chart_id ?? shaped.chart_id,
    rows: shaped.rows,
    columns: shaped.columns,
    graph: shaped.graph ?? entry.graph,
    artifact: shaped.artifact ?? entry.artifact,
    meta: shaped.meta ?? entry.meta,
    error: entry.error,
  };
}

/** POST /dashboards/{id}/data answers {data:{results:[...]}}; unwrap to the
 * per-chart entries and flatten each so chart_id + rows/columns are top-level. */
function normalizeBatch(res: any): ChartDataDTO[] {
  const results = Array.isArray(res)
    ? res
    : Array.isArray(res?.results)
      ? res.results
      : Array.isArray(res?.data?.results)
        ? res.data.results
        : Array.isArray(res?.data)
          ? res.data
          : [];
  return results.map(shapedOf);
}

function chartDataResult(entry: ChartDataDTO | undefined) {
  if (!entry) return null;
  if (entry.error?.code) {
    // Per-chart failure surfaces as its own error entry, verbatim code (AC-6).
    throw gqlError(entry.error.code as any, entry.error.message ?? "chart data error", {
      service: "chart-service",
    });
  }
  return {
    rows: entry.rows ?? null,
    columns: entry.columns ?? null,
    graph: entry.graph ?? null,
    artifact: entry.artifact ?? null,
    meta: entry.meta ?? null,
  };
}

export const resolvers = {
  JSON: JSONScalar,
  DateTime: DateTimeScalar,
  Date: DateScalar,

  Node: {
    __resolveType: (obj: { __typename?: string }) => obj.__typename ?? null,
  },

  Query: {
    me: (_p: unknown, _a: unknown, ctx: GraphQLContext) => {
      const c = ctx.identity.claims;
      return {
        userId: c.sub ?? "",
        tenantId: c.tenant_id ?? "",
        type: c.typ ?? "user",
        scopes: c.scopes ?? [],
        // Defensive OR: honor the clean claim, but also a token that carries the
        // platform.admin scope without the (newer) boolean.
        isPlatformAdmin: c.platform_admin === true || (c.scopes ?? []).includes("platform.admin"),
        workspaceId: c.workspace_id ?? "",
      };
    },

    user: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.identity.user(a.id).then((d) => mapUser(ctx, d))),

    // ---- admin: identity user directory + service accounts + tenant ----------
    users: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.identity.users(limit, cursor);
      return toConnection(page, (d) => mapUser(ctx, d));
    },

    // Member-safe assignee picker (no admin scope) — identity forwards the
    // caller's JWT and enforces the member-safe /users/assignable tier.
    assignableUsers: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.identity.assignableUsers(limit, cursor);
      return toConnection(page, (d) => mapUser(ctx, d));
    },

    serviceAccounts: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.identity.serviceAccounts(limit, cursor);
      return toConnection(page, (d) => mapServiceAccount(ctx, d));
    },

    tenant: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.identity.tenant(a.id).then((d) => mapTenant(ctx, d))),

    // Cross-tenant list for the platform-admin all-tenants view. identity's
    // requireSuperAdmin gate enforces (a tenant admin's JWT is rejected there).
    tenants: (_p: unknown, a: { limit?: number }, ctx: GraphQLContext) =>
      ctx.clients.identity
        .tenants(a.limit ?? 200)
        .then((p) => p.data.map((d) => mapTenant(ctx, d))),

    // ---- admin: rbac workspaces + groups -------------------------------------
    workspaces: async (
      _p: unknown,
      a: ConnectionArgs & { archived?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.rbac.workspaces(limit, cursor, a.archived);
      return toConnection(page, (d) => mapWorkspace(ctx, d));
    },

    workspace: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.rbac.workspace(a.id).then((d) => mapWorkspace(ctx, d))),

    // BYO-P4: the caller tenant's OIDC IdP. 404 (never configured) → the
    // "unconfigured" shape rather than null, so the admin screen always renders.
    tenantIdp: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.identity.tenantIdp()).then((d) =>
        d
          ? { __typename: "TenantIdpConfig" as const, configured: true, issuer: d.issuer, clientId: d.client_id, discoveryUrl: d.discovery_url, enabled: d.enabled, updatedAt: d.updated_at ?? null }
          : { __typename: "TenantIdpConfig" as const, configured: false, issuer: null, clientId: null, discoveryUrl: null, enabled: false, updatedAt: null },
      ),

    groups: async (
      _p: unknown,
      a: ConnectionArgs & { type?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.rbac.groups(limit, cursor, a.type);
      return toConnection(page, (d) => mapGroup(ctx, d));
    },

    group: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.rbac.group(a.id).then((d) => mapGroup(ctx, d))),

    groupMembers: async (
      _p: unknown,
      a: ConnectionArgs & { groupId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.rbac.groupMembers(a.groupId, limit, cursor);
      return (page.data ?? []).map(mapGroupMember);
    },

    groupRoles: async (
      _p: unknown,
      a: ConnectionArgs & { groupId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.rbac.groupRoles(a.groupId, limit, cursor);
      return (page.data ?? []).map(mapRole);
    },

    userGroups: async (
      _p: unknown,
      a: ConnectionArgs & { userId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.rbac.userGroups(a.userId, limit, cursor);
      return (page.data ?? []).map((d) => mapGroup(ctx, d));
    },

    roles: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.rbac.roles(limit, cursor);
      return toConnection(page, (d) => mapRole(d));
    },

    // ---- Tier 4b: identity/rbac admin — content grants -----------------------
    contentGrants: async (_p: unknown, a: { resourceUrn: string }, ctx: GraphQLContext) => {
      const rows = await ctx.clients.rbac.grants(a.resourceUrn);
      return rows.map(mapEffectiveAccessEntry);
    },

    explainAuthz: async (
      _p: unknown,
      a: { input: { userId: string; typ?: string; scopes?: string[]; action: string; resourceUrn?: string; workspaceId?: string } },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.explainAuthz({
        user_id: a.input.userId, typ: a.input.typ, scopes: a.input.scopes,
        action: a.input.action, resource_urn: a.input.resourceUrn, workspace_id: a.input.workspaceId,
      });
      return mapAuthzExplanation(d);
    },

    // ---- admin: audit trail (WORM compliance search) -------------------------
    auditEvents: async (
      _p: unknown,
      a: ConnectionArgs & {
        from?: string; to?: string; eventType?: string; action?: string;
        actorId?: string; actorType?: string; resourceUrn?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      // audit-service requires from/to (RFC3339, <=92d window). Default to the
      // last 7 days when the client omits them.
      const to = a.to ?? new Date().toISOString();
      const from = a.from ?? new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
      const page = await ctx.clients.audit.search({
        from, to,
        eventType: a.eventType, action: a.action,
        actorId: a.actorId, actorType: a.actorType,
        resourceUrn: a.resourceUrn,
        resourceMatch: a.resourceUrn ? "prefix" : undefined,
        limit, cursor,
      });
      return toConnection(page, (d) => mapAuditEvent(ctx, d));
    },

    complianceOperation: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.audit.operation(a.id).then((d) => mapComplianceJob(d))),

    dataset: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.dataset.dataset(a.id).then((d) => mapDataset(ctx, d))),

    datasets: async (
      _p: unknown,
      a: ConnectionArgs & { q?: string; filter?: { status?: string; tags?: string } },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.dataset.datasets({
        q: a.q, limit, cursor, status: a.filter?.status, tags: a.filter?.tags,
      });
      return toConnection(page, (d) => mapDataset(ctx, d));
    },

    // ---- ingestion: connector catalog + connections (JWT passthrough) --------
    connectorTypes: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.ingestion.connectorTypes().then((ts) => ts.map(mapConnectorType)),

    connection: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.ingestion.connection(a.id).then((d) => mapConnection(ctx, d))),

    connections: async (
      _p: unknown,
      a: ConnectionArgs & { q?: string; connectorType?: string; trafficDirection?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.ingestion.connections({
        q: a.q, connectorType: a.connectorType, trafficDirection: a.trafficDirection, limit, cursor,
      });
      return toConnection(page, (d) => mapConnection(ctx, d));
    },

    // ---- decision write-back / SoR sync (ingestion-service GET /writebacks{,/{id}}) --
    writeback: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.ingestion.writeback(a.id).then((d) => mapWriteback(ctx, d))),

    writebacks: async (
      _p: unknown,
      a: { status?: string; workspaceId?: string; first?: number },
      ctx: GraphQLContext,
    ) => {
      const rows = await ctx.clients.ingestion.writebacks({
        status: a.status, workspaceId: a.workspaceId, limit: a.first ?? 50,
      });
      return rows.map((d) => mapWriteback(ctx, d));
    },

    // ---- ingestion runs (ingestion-service GET /ingestions, JWT passthrough) --
    ingestions: async (
      _p: unknown,
      a: ConnectionArgs & { status?: string; mode?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.ingestion.ingestions({
        status: a.status, ingestionMode: a.mode, limit, cursor,
      });
      return toConnection(page, (d) => mapIngestion(ctx, d));
    },

    ingestion: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.ingestion.ingestion(a.id).then((d) => mapIngestion(ctx, d))),

    upload: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.ingestion.upload(a.id).then(mapUpload)),

    // ---- Tier 4a: recurring ingestion schedules + source preview --------------
    ingestionSchedules: async (
      _p: unknown,
      a: ConnectionArgs,
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.ingestion.schedules({ limit, cursor });
      return toConnection(page, (d) => mapIngestionSchedule(ctx, d));
    },

    ingestionSchedule: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.ingestion.schedule(a.id).then((d) => mapIngestionSchedule(ctx, d))),

    connectionPreview: async (
      _p: unknown,
      a: { id: string; input: { table?: string; path?: string; query?: string; limit?: number } },
      ctx: GraphQLContext,
    ) =>
      mapConnectionPreview(
        await ctx.clients.ingestion.previewConnection(a.id, {
          table: a.input.table,
          path: a.input.path,
          query: a.input.query,
          limit: a.input.limit ?? undefined,
        }),
      ),

    // ---- dataset lineage (dataset-service GET /lineage, JWT passthrough) ------
    datasetLineage: async (
      _p: unknown,
      a: { urn: string; direction?: string; depth?: number },
      ctx: GraphQLContext,
    ) => mapLineage(await ctx.clients.dataset.lineage(a.urn, a.direction ?? "both", a.depth ?? undefined)),

    // ---- Tier 4a: dataset consumers / versions / similarity -------------------
    datasetConsumers: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapDatasetConsumers(await ctx.clients.dataset.consumers(a.id)),

    datasetVersions: async (
      _p: unknown,
      a: ConnectionArgs & { datasetId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.dataset.versions(a.datasetId, limit, cursor);
      return toConnection(page, mapDatasetVersion);
    },

    /** Similarity search seeded from the target dataset's own columns: the
     * current version's schema map when populated, else the profile's inferred
     * columns (same fallback datasetSchema uses). The seed dataset itself is
     * filtered out of the ranked hits. */
    similarDatasets: async (_p: unknown, a: { datasetId: string }, ctx: GraphQLContext) => {
      const ds = await ctx.clients.dataset.dataset(a.datasetId);
      let schema: Record<string, unknown> | undefined;
      let columns: string[] | undefined;
      const versionNo = ds.current_version?.version_no;
      if (versionNo != null) {
        const v = await ctx.clients.dataset.version(a.datasetId, versionNo);
        if (v.schema && Object.keys(v.schema).length > 0) {
          schema = v.schema as Record<string, unknown>;
          columns = Object.keys(v.schema);
        }
      }
      if (!columns || columns.length === 0) {
        try {
          const p = await ctx.clients.dataset.profile(a.datasetId);
          columns = (p.columns ?? []).map((c) => c.name).filter(Boolean);
        } catch (e) {
          if (!(e instanceof DownstreamError && e.httpStatus === 404)) throw e;
        }
      }
      if ((!columns || columns.length === 0) && !schema) {
        // Honest empty result: nothing to search by (no schema, no profile).
        return [];
      }
      const hits = await ctx.clients.dataset.similar({ schema, columns });
      return hits
        .map(mapSimilarDataset)
        .filter((h) => h.id !== a.datasetId && (h.urn == null || urnId(h.urn) !== a.datasetId));
    },

    // ---- saved queries (query-service GET /queries, JWT passthrough) ---------
    savedQueries: async (
      _p: unknown,
      a: ConnectionArgs & { workspaceId?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.query.savedQueries({ workspaceId: a.workspaceId, limit, cursor });
      return toConnection(page, (d) => mapSavedQuery(ctx, d));
    },

    savedQuery: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.query.savedQuery(a.id).then((d) => mapSavedQuery(ctx, d))),

    // ---- Tier 4a: saved-query versions + execution history (query-service) ----
    savedQueryVersions: async (
      _p: unknown,
      a: ConnectionArgs & { queryId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.query.versions(a.queryId, limit, cursor);
      return toConnection(page, mapSavedQueryVersion);
    },

    queryExecutions: async (
      _p: unknown,
      a: ConnectionArgs & { status?: string; savedQueryId?: string; since?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.query.executions({
        limit, cursor, status: a.status, savedQueryId: a.savedQueryId, since: a.since,
      });
      return toConnection(page, (d) => mapQueryExecution(ctx, d));
    },

    queryExecution: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.query.execution(a.id).then((d) => mapQueryExecution(ctx, d))),

    queryStats: async (
      _p: unknown,
      a: { since?: string; limit?: number },
      ctx: GraphQLContext,
    ) => mapQueryStats(await ctx.clients.query.stats(a.since, a.limit ?? undefined)),

    dashboard: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.chart.dashboard(a.id).then((d) => mapDashboard(ctx, d))),

    dashboards: async (
      _p: unknown,
      a: ConnectionArgs & { workspaceId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.chart.dashboards(a.workspaceId, limit, cursor);
      return toConnection(page, (d) => mapDashboard(ctx, d));
    },

    archivedDashboards: async (
      _p: unknown,
      a: ConnectionArgs & { workspaceId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.chart.dashboards(a.workspaceId, limit, cursor, true);
      return toConnection(page, (d) => mapDashboard(ctx, d));
    },

    reportSubscription: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.notification.reportSubscription(a.id).then((d) => mapReportSubscription(ctx, d))),

    reportSubscriptions: async (
      _p: unknown,
      a: ConnectionArgs & { dashboardId?: string | null },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.notification.reportSubscriptions(a.dashboardId ?? undefined, limit, cursor);
      return toConnection(page, (d) => mapReportSubscription(ctx, d));
    },

    // ---- charts: catalog + single chart + live preview (JWT passthrough) -----
    chartTypes: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.chart.chartTypes().then((ts) => ts.map(mapChartType)),

    chart: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.chart.chart(a.id).then((d) => mapChart(ctx, d))),

    chartPreview: async (
      _p: unknown,
      a: {
        input: {
          chartType: string; config: Record<string, unknown>;
          displayMeta?: Record<string, unknown>; sources?: ChartSourceInputGQL[];
        };
      },
      ctx: GraphQLContext,
    ) => {
      // Preview resolves an UNSAVED spec, so dashboardId/name are ignored here.
      const d = await ctx.clients.chart.preview({
        chart_type: a.input.chartType,
        config: a.input.config,
        display_meta: a.input.displayMeta,
        sources: mapSourcesInput(a.input.sources),
      });
      return mapChartShapedData(d);
    },

    // ---- semantic models: field pickers for the chart editor (JWT passthrough)
    semanticModels: (_p: unknown, a: { workspaceId?: string }, ctx: GraphQLContext) =>
      // Headers only — dimensions/measures resolve to [] here; the editor calls
      // semanticModel(name) to hydrate the definition (no per-item N+1).
      ctx.clients.semantic.models(a.workspaceId).then((ms) => ms.map((m) => mapSemanticModel(ctx, m))),

    semanticModel: async (_p: unknown, a: { name: string }, ctx: GraphQLContext) => {
      // Resolve name -> id from the model list, then hydrate the published
      // definition (dimensions/measures). Two top-level calls, not a per-item fan-out.
      const models = await ctx.clients.semantic.models();
      const model = models.find((m) => m.name === a.name);
      if (!model) return null;
      // A model with no published version answers 409 MODEL_NOT_PUBLISHED (404 if
      // it vanished mid-flight) — both mean "no definition yet", surfaced as empty
      // dimensions/measures rather than an error (the header still exists).
      let def = null;
      try {
        def = (await ctx.clients.semantic.definition(model.id)).definition ?? null;
      } catch (e) {
        if (!(e instanceof DownstreamError && (e.httpStatus === 404 || e.downstreamCode === "MODEL_NOT_PUBLISHED"))) {
          throw e;
        }
      }
      return mapSemanticModel(ctx, model, def);
    },

    // ---- dataset row browse (server-paged/sorted/filtered) -------------------
    datasetRows: async (
      _p: unknown,
      a: {
        datasetId: string;
        offset?: number;
        limit?: number;
        sort?: string | null;
        dir?: string | null;
        filters?: { col: string; op: string; value: string }[] | null;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.dataset.rows(a.datasetId, {
        offset: a.offset ?? 0,
        limit: a.limit ?? 50,
        sort: a.sort ?? null,
        dir: a.dir ?? null,
        filters: a.filters ?? [],
      });
      return {
        columns: d.columns,
        rows: d.rows,
        total: d.total,
        filtered: d.filtered,
        offset: d.offset,
        limit: d.limit,
        truncated: d.truncated ?? false,
      };
    },

    // Resolve a chart selection to the real detail-row browse behind it, so a
    // dashboard drill can open cases anchored to (dataset_urn, row_pk). Only
    // semantic-measure charts resolve (increment 1); saved-query/other -> null.
    chartDrillTarget: async (
      _p: unknown,
      a: { chartId: string; dimension: string },
      ctx: GraphQLContext,
    ): Promise<{ datasetId: string; datasetUrn: string; column: string } | null> => {
      const chart = await ctx.clients.chart.chart(a.chartId).catch(() => null);
      if (!chart) return null;
      const sources = (chart.sources ?? [])
        .slice()
        .sort((x, y) => (x.position ?? 0) - (y.position ?? 0));
      const primary = sources[0];
      if (!primary || primary.source_type !== "semantic_measure") return null;

      const dm = (chart.display_meta ?? {}) as Record<string, unknown>;
      const cfg = (chart.config ?? {}) as Record<string, unknown>;
      const modelName = (dm.semantic_model as string) ?? (cfg.model as string);
      if (!modelName) return null;
      const wsId = (dm.workspace_id as string) || undefined;

      const models = await ctx.clients.semantic.listModels(wsId, 200).catch(() => null);
      const model = (models?.data ?? []).find((m) => m.name === modelName);
      if (!model) return null;

      const defRes = await ctx.clients.semantic.definition(model.id).catch(() => null);
      const def = (defRes?.definition ?? {}) as {
        entities?: { name?: string; dataset_urn?: string }[];
        dimensions?: { name?: string; column?: string; entity?: string }[];
      };
      const dim = (def.dimensions ?? []).find((x) => x.name === a.dimension);
      if (!dim) return null;
      const column = dim.column ?? dim.name;
      if (!column) return null;

      const entities = def.entities ?? [];
      const entity = entities.find((e) => e.name === dim.entity) ?? entities[0];
      const datasetUrn = entity?.dataset_urn;
      if (!datasetUrn) return null;
      const m = /dataset\/([0-9a-fA-F-]+)$/.exec(datasetUrn);
      const datasetId = m?.[1];
      if (!datasetId) return null;

      return { datasetId, datasetUrn, column };
    },

    datasetAggregate: async (
      _p: unknown,
      a: { datasetId: string; dimension: string; measure?: string | null; agg: string; limit?: number },
      ctx: GraphQLContext,
    ) => {
      const agg = a.agg.toLowerCase();
      if (!QUICK_AGGS.has(agg)) {
        throw gqlError(
          ErrorCode.VALIDATION_FAILED,
          `unsupported agg '${a.agg}'; use one of ${[...QUICK_AGGS].join(", ")}`,
        );
      }
      if (agg !== "count" && !a.measure) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, `agg '${agg}' requires a measure column`);
      }
      // Validate dimension/measure against the dataset's REAL columns (the
      // physical table the macro resolves to), so we never interpolate a
      // user-typed identifier that isn't a real column.
      const [ds, head] = await Promise.all([
        ctx.clients.dataset.dataset(a.datasetId),
        ctx.clients.dataset.rows(a.datasetId, { limit: 1 }),
      ]);
      const cols = new Set(head.columns);
      if (!cols.has(a.dimension)) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, `unknown dimension column '${a.dimension}'`);
      }
      if (a.measure && !cols.has(a.measure)) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, `unknown measure column '${a.measure}'`);
      }
      const limit = Math.max(1, Math.min(a.limit ?? 50, 500));
      const { sql } = buildAggregateSql({
        datasetName: ds.name ?? "",
        dimension: a.dimension,
        measure: a.measure ?? null,
        agg,
        limit,
      });
      const exec = await ctx.clients.query.runSQL({ sql, mode: "sync", limit });
      const result = await hydrateResult(ctx, exec, limit);
      return {
        columns: (result.columns ?? []).map((c) => c.name),
        rows: result.rows ?? [],
        sql,
      };
    },

    // ---- semantic model authoring (JWT passthrough) --------------------------
    datasetSchema: async (_p: unknown, a: { datasetId: string; version?: number }, ctx: GraphQLContext) => {
      // The version's authoritative schema map, falling back to profile columns
      // when it's empty (see mapDatasetSchema doc — a real pre-existing gap on
      // this deployment's older-ingested datasets, not fabricated data).
      const version = a.version != null
        ? await ctx.clients.dataset.version(a.datasetId, a.version)
        : ((await ctx.clients.dataset.versions(a.datasetId, 1)).data ?? [])[0] ?? null;
      let profileColumns = null;
      if (!version?.schema || Object.keys(version.schema).length === 0) {
        try {
          profileColumns = (await ctx.clients.dataset.profile(a.datasetId)).columns ?? null;
        } catch (e) {
          if (!(e instanceof DownstreamError && e.httpStatus === 404)) throw e;
        }
      }
      return mapDatasetSchema(version ?? null, profileColumns);
    },

    semanticModelList: async (
      _p: unknown,
      a: ConnectionArgs & { workspaceId?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.semantic.listModels(a.workspaceId, limit, cursor);
      return toConnection(page, (d) => mapSemanticModelSummary(ctx, d));
    },

    semanticModelDetail: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.semantic.model(a.id).then((d) => mapSemanticModelSummary(ctx, d))),

    semanticModelVersions: async (
      _p: unknown,
      a: ConnectionArgs & { modelId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.semantic.versions(a.modelId, limit, cursor);
      return toConnection(page, (d) => mapSemanticModelVersion(ctx, a.modelId, d));
    },

    semanticModelVersion: (_p: unknown, a: { modelId: string; versionNo: number }, ctx: GraphQLContext) =>
      nullOn404(
        ctx.clients.semantic
          .version(a.modelId, a.versionNo)
          .then((d) => mapSemanticModelVersion(ctx, a.modelId, d)),
      ),

    compileSemanticModel: async (
      _p: unknown,
      a: {
        input: {
          model: string; workspaceId?: string; metrics: string[];
          dimensions?: { name: string; grain?: string }[];
          filters?: { dimension: string; op: string; values?: unknown[] }[];
          limit?: number; dialect?: string; draftVersionNo?: number; validate?: boolean;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const { result, validationError } = await ctx.clients.semantic.compile(
        {
          model: a.input.model,
          workspace_id: a.input.workspaceId,
          metrics: a.input.metrics,
          dimensions: a.input.dimensions,
          filters: a.input.filters,
          limit: a.input.limit,
          dialect: a.input.dialect,
        },
        { validate: a.input.validate ?? false, draftVersionNo: a.input.draftVersionNo ?? undefined },
      );
      return mapSemanticCompileResult(result, validationError);
    },

    // ---- Tier 4a: verified NL↔SQL pairs + async operations (semantic-service) --
    verifiedQueries: async (
      _p: unknown,
      a: ConnectionArgs & { workspaceId?: string; status?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.semantic.verifiedQueries({
        workspaceId: a.workspaceId, status: lower(a.status), limit, cursor,
      });
      return toConnection(page, (d) => mapVerifiedQuery(ctx, d));
    },

    verifiedQuery: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.semantic.verifiedQuery(a.id).then((d) => mapVerifiedQuery(ctx, d))),

    verifiedQuerySearch: async (
      _p: unknown,
      a: { query: string; workspaceId: string; topK?: number },
      ctx: GraphQLContext,
    ) => {
      const hits = await ctx.clients.semantic.verifiedQuerySearch(
        a.query, a.workspaceId, a.topK ?? undefined);
      return hits.map(mapVerifiedQuerySearchHit);
    },

    semanticOperation: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.semantic.operation(a.id).then(mapSemanticOperation)),

    case: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.case.case(a.id).then((d) => mapCase(ctx, d))),

    caseSearch: async (
      _p: unknown,
      a: ConnectionArgs & { q?: string; filter?: { status?: string; severity?: string; assignee?: string } },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.case.search({
        q: a.q, limit, cursor,
        status: a.filter?.status, severity: a.filter?.severity, assignee: a.filter?.assignee,
      });
      return toConnection(page, (d) => mapCase(ctx, d));
    },

    // ---- Tier 4b: case ops (case-service) -----------------------------------
    caseTimeline: async (
      _p: unknown,
      a: ConnectionArgs & { caseId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.case.timeline(a.caseId, limit, cursor);
      return toConnection(page, (d) => mapCaseActivity(ctx, d));
    },

    caseOperation: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.case.operation(a.id).then(mapCaseOperation)),

    dispositions: async (_p: unknown, _a: unknown, ctx: GraphQLContext) => {
      const rows = await ctx.clients.case.dispositions();
      return rows.map((d) => mapDisposition(ctx, d));
    },

    caseFields: async (_p: unknown, a: { queryUrn?: string }, ctx: GraphQLContext) => {
      const rows = await ctx.clients.case.caseFields(a.queryUrn);
      return rows.map((d) => mapCaseField(ctx, d));
    },

    proposalsInbox: async (
      _p: unknown,
      a: ConnectionArgs & { status?: string; agentKey?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.agent.proposals({
        status: lower(a.status), agentKey: a.agentKey, limit, cursor,
      });
      return toConnection(page, (d) => mapProposal(ctx, d));
    },

    proposal: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.agent.proposal(a.id).then((d) => mapProposal(ctx, d))),

    agentRun: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.agent.run(a.id).then((d) => mapAgentRun(ctx, d))),

    // Correction->retrain loop stats (agent-runtime M1 transcripts + M2 SFT
    // datasets). Counts are honest page counts: the service caps list pages
    // at 200, so `capped` tells the UI to render "200+" instead of a lie.
    learningLoop: async (_p: unknown, _a: unknown, ctx: GraphQLContext) => {
      const CAP = 200;
      const [all, decided, datasets] = await Promise.all([
        ctx.clients.agent.transcripts({ limit: CAP }),
        ctx.clients.agent.transcripts({ decided: true, limit: CAP }),
        ctx.clients.agent.sftDatasets({ limit: 50 }),
      ]);
      const ds = (datasets.data ?? []).slice().sort((a, b) =>
        (b.created_at ?? "").localeCompare(a.created_at ?? ""));
      const latest = ds[0];
      return {
        transcriptsCaptured: (all.data ?? []).length,
        correctionsCaptured: (decided.data ?? []).length,
        datasetCount: ds.length,
        latestDatasetAgentKey: latest?.agent_key ?? null,
        latestDatasetVersion: latest?.version ?? null,
        latestDatasetExamples: latest?.row_count ?? null,
        latestDatasetAt: latest?.created_at ?? null,
        capped: (all.data ?? []).length >= CAP,
      };
    },

    agentKillSwitches: async (_p: unknown, _a: unknown, ctx: GraphQLContext) => {
      const rows = await ctx.clients.agent.killSwitches();
      return rows.map((d) => mapAgentKillSwitch(ctx, d));
    },

    toolKillSwitches: async (_p: unknown, _a: unknown, ctx: GraphQLContext) => {
      const rows = await ctx.clients.toolPlane.killSwitches();
      return rows.map((d) => mapToolKillSwitch(ctx, d));
    },

    memories: async (
      _p: unknown,
      a: { scope?: string; scopeRef?: string; status?: string; tags?: string[] } & ConnectionArgs,
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.memory.browse({
        scope: a.scope, scopeRef: a.scopeRef, status: a.status, tags: a.tags, limit, cursor,
      });
      return toConnection(page, (d) => mapMemoryRecord(ctx, d));
    },

    memory: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.memory.memory(a.id).then((d) => mapMemoryRecord(ctx, d))),

    erasure: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.memory.erasure(a.id).then((d) => mapErasureRequest(ctx, d))),

    memoryStats: (_p: unknown, _a: unknown, ctx: GraphQLContext) => ctx.clients.memory.stats(),

    experiments: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.experiment.experiments(limit, cursor);
      return toConnection(page, (d) => mapExperiment(ctx, d));
    },

    experiment: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.experiment.experiment(a.id).then((d) => mapExperiment(ctx, d))),

    archivedExperiments: async (
      _p: unknown,
      a: ConnectionArgs & { workspaceId?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.experiment.archivedExperiments(limit, cursor, a.workspaceId);
      return toConnection(page, (d) => mapExperiment(ctx, d));
    },

    run: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.experiment.run(a.id).then((d) => mapRun(ctx, d))),

    // ---- ml: model registry (list + detail with versions/stages) -------------
    models: async (
      _p: unknown,
      a: ConnectionArgs & { stage?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      // The list path serves model headers only (no versions) — versions resolve
      // to [] here; the model(id) detail path hydrates them (no per-row N+1).
      const page = await ctx.clients.experiment.models({ stage: a.stage, limit, cursor });
      return toConnection(page, (d) => mapRegistryModel(ctx, d));
    },

    model: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(
        ctx.clients.experiment
          .modelDetail(a.id)
          .then((d) => mapRegistryModel(ctx, d.model, d.versions ?? [])),
      ),

    promotions: async (
      _p: unknown,
      a: { modelId: string; version: number } & ConnectionArgs,
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.experiment.promotions(a.modelId, a.version, limit, cursor);
      return toConnection(page, (d) => mapPromotion(ctx, d));
    },

    // ---- ml: batch inference jobs (inference-service) ------------------------
    inferenceJobs: async (
      _p: unknown,
      a: ConnectionArgs & { status?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.inference.jobs({ status: lower(a.status), limit, cursor });
      return toConnection(page, (d) => mapInferenceJob(ctx, d));
    },

    inferenceJob: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.inference.job(a.id).then((d) => mapInferenceJob(ctx, d))),

    // ---- Tier 4b: ml ops (experiment-service run tooling) ---------------------
    // best_run 404s BOTH for a missing experiment and when no run carries the
    // metric — either resolves to null (nullable single-entity convention).
    // The payload is _run_payload + {metric: float} metrics, which mapRun's
    // plain-number metrics path folds straight into Run.metrics.
    bestRun: (
      _p: unknown,
      a: { experimentId: string; metric: string; direction?: string; status?: string },
      ctx: GraphQLContext,
    ) =>
      nullOn404(
        ctx.clients.experiment
          .bestRun(a.experimentId, a.metric, a.direction ?? undefined, a.status ?? undefined)
          .then((d) => mapRun(ctx, d)),
      ),

    compareRuns: async (
      _p: unknown,
      a: { runIds: string[]; metrics?: string[]; params?: string[]; includeAll?: boolean },
      ctx: GraphQLContext,
    ) => {
      const r = await ctx.clients.experiment.compareRuns({
        run_ids: a.runIds,
        metrics: a.metrics,
        params: a.params,
        include_all: a.includeAll ?? false,
      });
      // metrics/params rows pass through verbatim (compare.py build_comparison):
      // [{key, values:{runId: value|null}, best_run_id, direction}] / [{key, values, differs}].
      return {
        runIds: r.data?.runs ?? a.runIds,
        metrics: r.data?.metrics ?? [],
        params: r.data?.params ?? [],
      };
    },

    runNote: (_p: unknown, a: { runId: string }, ctx: GraphQLContext) =>
      // GET /runs/{id}/note 404s when the run has no note — that is a real
      // "no note" answer, not an error: resolve null.
      nullOn404(ctx.clients.experiment.runNote(a.runId).then((d) => mapRunNote(d))),

    runMetricHistory: async (
      _p: unknown,
      a: { runId: string; keys?: string[] },
      ctx: GraphQLContext,
    ) => {
      const page = await ctx.clients.experiment.metricHistory(a.runId, a.keys);
      return page.data ?? [];
    },

    runArtifacts: async (_p: unknown, a: { runId: string }, ctx: GraphQLContext) => {
      const arts = await ctx.clients.experiment.runArtifacts(a.runId);
      return arts.map((d) => mapRunArtifact(d));
    },

    runArtifactUrl: async (
      _p: unknown,
      a: { runId: string; path: string },
      ctx: GraphQLContext,
    ) => {
      // Non-null by design: a missing artifact is a real downstream 404 error
      // (never a fabricated link) — surfaced verbatim.
      const d = await ctx.clients.experiment.runArtifactUrl(a.runId, a.path);
      return d.url;
    },

    modelCard: (_p: unknown, a: { modelId: string; version: number }, ctx: GraphQLContext) =>
      // Merged card verbatim as JSON; 404 (model/version/card missing) → null.
      nullOn404(ctx.clients.experiment.modelCard(a.modelId, a.version)),

    // ---- Tier 4b: ml ops (inference-service schedules) ------------------------
    inferenceSchedules: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.inference.schedules(limit, cursor);
      return toConnection(page, (d) => mapInferenceSchedule(ctx, d));
    },

    inferenceSchedule: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.inference.schedule(a.id).then((d) => mapInferenceSchedule(ctx, d))),

    inferenceScheduleFires: async (
      _p: unknown,
      a: { scheduleId: string } & ConnectionArgs,
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.inference.scheduleFires(a.scheduleId, limit, cursor);
      return toConnection(page, (d) => mapInferenceJob(ctx, d));
    },

    workspaceCostPanel: async (
      _p: unknown,
      a: { workspaceId: string; from: string; to: string },
      ctx: GraphQLContext,
    ) => {
      const [report, states] = await Promise.all([
        ctx.clients.usage.usageReport({
          groupBy: ["meter"], from: a.from, to: a.to, workspaceId: a.workspaceId, limit: 200,
        }),
        ctx.clients.usage.budgetStates(`workspace/${a.workspaceId}`),
      ]);
      const rows = (report.data ?? report.rows ?? []).map((r) => ({
        dimensions: r,
        meterKey: (r as any).meter_key ?? null,
        quantity: (r as any).quantity ?? null,
        // usage-service RollupRow serializes the dollar figure as `usd` today;
        // `cost_usd` is the incoming contract — accept both.
        costUsd: (r as any).cost_usd ?? (r as any).usd ?? null,
      }));
      return {
        rows,
        budgetStates: states.map((s) => ({
          // budgetStateView carries no scope today (incoming contract adds it);
          // scope may also arrive as the budget's nested object — stringify keys.
          scope: budgetScopeString(s.scope) ?? null,
          consumed: s.consumed ?? null,
          // state view serializes `limit`; the budget view calls it `limit_value`.
          limit: s.limit ?? s.limit_value ?? null,
          lastThreshold: s.last_threshold ?? null,
          exhaustedAt: s.exhausted_at ?? null,
        })),
      };
    },

    budgets: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.usage.budgets(limit, cursor);
      return toConnection(page, (d) => mapBudget(ctx, d));
    },

    budget: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.usage.budget(a.id).then((d) => mapBudget(ctx, d))),

    rateCards: async (_p: unknown, _a: ConnectionArgs, ctx: GraphQLContext) => {
      const page = await ctx.clients.usage.rateCards();
      return toConnection(page, (d) => mapRateCard(ctx, d));
    },

    anomalies: async (_p: unknown, a: { status?: string }, ctx: GraphQLContext) => {
      const page = await ctx.clients.usage.anomalies(a.status);
      return (page.data ?? []).map((d) => mapAnomaly(ctx, d));
    },

    // ---- pipelines: no-code builder catalog + templates + runs (JWT passthrough)
    pipelineStepTypes: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.pipelines.components().then((cs) => cs.map(mapPipelineStepType)),

    algorithmTemplates: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.pipelines.algorithmTemplates().then((as) => as.map(mapAlgorithmTemplate)),

    pipelineTemplates: async (
      _p: unknown,
      a: ConnectionArgs & { q?: string; pipelineType?: string; includeArchived?: boolean },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.pipelines.pipelines({
        name: a.q, pipelineType: a.pipelineType, includeArchived: a.includeArchived ?? undefined, limit, cursor,
      });
      return toConnection(page, (d) => mapPipelineTemplate(ctx, d));
    },

    pipelineTemplate: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.pipelines.pipeline(a.id).then((d) => mapPipelineTemplate(ctx, d))),

    pipelineRuns: async (
      _p: unknown,
      a: ConnectionArgs & { templateId?: string; status?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.pipelines.runs({
        status: a.status, templateId: a.templateId, limit, cursor,
      });
      return toConnection(page, (d) => mapPipelineRun(ctx, d));
    },

    // ---- Tier 4a: pipeline run detail/manifest + template versions ------------
    pipelineRun: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.pipelines.runById(a.id).then((d) => mapPipelineRun(ctx, d))),

    pipelineRunManifest: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapPipelineRunManifest(await ctx.clients.pipelines.runManifest(a.id)),

    pipelineTemplateVersions: async (
      _p: unknown,
      a: ConnectionArgs & { templateId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.pipelines.templateVersions(a.templateId, limit, cursor);
      return toConnection(page, mapPipelineTemplateVersion);
    },

    pipelineSchedules: async (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      (await ctx.clients.pipelines.pipelineSchedules()).map((d) => mapPipelineSchedule(ctx, d)),

    // ==== Tier 2a: eval (eval-service) =======================================
    evalSuite: (_p: unknown, a: { suiteId: string; version?: number }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.eval.suite(a.suiteId, a.version).then((d) => mapEvalSuite(ctx, d))),

    evalRuns: async (
      _p: unknown,
      a: ConnectionArgs & { agentKey?: string; trigger?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.eval.runs({ agentKey: a.agentKey, trigger: a.trigger, limit, cursor });
      return toConnection(page, (d) => mapEvalRun(ctx, d));
    },

    evalRun: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.eval.run(a.id).then((d) => mapEvalRun(ctx, d))),

    evalDatasets: async (_p: unknown, a: ConnectionArgs & { agentKey?: string }, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.eval.datasets({ agentKey: a.agentKey, limit, cursor });
      return toConnection(page, (d) => mapEvalDataset(ctx, d));
    },

    evalDataset: (_p: unknown, a: { datasetKey: string; version: number }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.eval.dataset(a.datasetKey, a.version).then((d) => mapEvalDataset(ctx, d))),

    evalCases: async (
      _p: unknown,
      a: ConnectionArgs & { datasetKey?: string; datasetVersion?: number; status?: string; source?: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.eval.cases({
        status: a.status, datasetKey: a.datasetKey, datasetVersion: a.datasetVersion, source: a.source,
        limit, cursor,
      });
      return toConnection(page, (d) => mapEvalCase(ctx, d));
    },

    evalCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.eval.case_(a.id).then((d) => mapEvalCase(ctx, d))),

    evalScorers: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.eval.scorers({ limit, cursor });
      return toConnection(page, (d) => mapEvalScorer(ctx, d));
    },

    evalGate: (_p: unknown, a: { gateRunId: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.eval.gate(a.gateRunId).then((d) => mapEvalGateResult(ctx, d))),

    evalGatesByDigest: (_p: unknown, a: { agentKey: string; contentDigest: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.gatesByDigest(a.agentKey, a.contentDigest).then((rows) => rows.map((d) => mapEvalGateResult(ctx, d))),

    evalCanary: (_p: unknown, a: { comparisonId: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.eval.canary(a.comparisonId).then((d) => mapEvalCanary(ctx, d))),

    evalTrends: (_p: unknown, a: { agentKey: string; scorer?: string; window?: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.trends(a.agentKey, a.scorer, a.window).then((rows) => rows.map(mapEvalTrendPoint)),

    evalSlos: (_p: unknown, a: { agentKey: string; window?: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.slos(a.agentKey, a.window).then((rows) => rows.map(mapEvalSloRow)),

    // ==== Tier 2a: ai-gateway admin ==========================================
    aiProviders: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.aiGateway.providers(limit, cursor);
      return toConnection(page, (d) => mapAiProvider(ctx, d));
    },

    aiLadder: (_p: unknown, a: { requestClass: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.aiGateway.ladder(a.requestClass).then(mapAiLadder)),

    aiBudgets: async (_p: unknown, a: ConnectionArgs & { scopeType?: string }, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.aiGateway.budgets(limit, cursor, a.scopeType);
      return toConnection(page, (d) => mapAiBudget(ctx, d));
    },

    aiBudget: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.aiGateway.budget(a.id).then((d) => mapAiBudget(ctx, d))),

    aiSpend: (_p: unknown, a: { scopeType: string; scopeRef: string; window?: string }, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.spend(a.scopeType, a.scopeRef, a.window).then((rows) => rows.map(mapAiSpendRow)),

    // ADDED (provider-agnostic + cost-detail): real per-provider/model breakdown.
    aiCostBreakdown: (_p: unknown, a: { windowHours?: number }, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.costBreakdown(a.windowHours ?? undefined).then(mapAiCostBreakdown),

    aiKeys: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.aiGateway.keys(limit, cursor);
      return toConnection(page, (d) => mapAiVirtualKey(ctx, d));
    },

    aiGuardrailPolicy: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.guardrails().then(mapAiGuardrailPolicy),

    // ==== Tier 2b: notification-service ======================================
    notifications: async (_p: unknown, a: ConnectionArgs & { unread?: boolean }, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.notification.notifications(a.unread ?? undefined, limit, cursor);
      return toConnection(page, (d) => mapNotification(ctx, d));
    },

    notificationUnreadCount: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.notification.unreadCount(),

    notificationPreferences: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.notification.preferences().then(mapNotificationPreferences),

    notificationRules: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.notification.rules(limit, cursor);
      return toConnection(page, mapNotificationRule);
    },

    notificationWebhooks: async (_p: unknown, a: ConnectionArgs, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.notification.webhooks(limit, cursor);
      return toConnection(page, mapWebhookEndpoint);
    },

    notificationWebhookDeliveries: async (
      _p: unknown,
      a: ConnectionArgs & { webhookId: string },
      ctx: GraphQLContext,
    ) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.notification.webhookDeliveries(a.webhookId, limit, cursor);
      return toConnection(page, mapWebhookDelivery);
    },

    notificationTemplates: (_p: unknown, a: { key: string }, ctx: GraphQLContext) =>
      ctx.clients.notification.templates(a.key).then((rows) => rows.map(mapNotificationTemplate)),

    notificationDeliveryStats: (_p: unknown, a: { window?: string }, ctx: GraphQLContext) =>
      ctx.clients.notification.deliveryStats(a.window).then((d) => ({
        window: d.window,
        byChannel: d.by_channel ?? {},
      })),

    emailSuppressions: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.notification.suppressions().then((rows) => rows.map(mapEmailSuppression)),

    // ==== Tier 2b: tool-plane registry admin =================================
    tools: async (_p: unknown, a: ConnectionArgs & { ownerService?: string }, ctx: GraphQLContext) => {
      const { limit, cursor } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.toolPlane.tools(limit, cursor, a.ownerService);
      return toConnection(page, mapTool);
    },

    toolHealth: (_p: unknown, a: { toolId: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.toolPlane.toolHealth(a.toolId).then(mapToolHealth)),

    toolSchema: (_p: unknown, a: { toolId: string; version?: string }, ctx: GraphQLContext) =>
      nullOn404(
        ctx.clients.toolPlane.toolSchema(a.toolId, a.version).then((d) => ({
          toolId: d.tool_id,
          version: d.version,
          inputSchema: d.input_schema ?? null,
          outputSchema: d.output_schema ?? null,
        })),
      ),

    byoSubmissions: (_p: unknown, a: { status?: string }, ctx: GraphQLContext) =>
      ctx.clients.toolPlane.byoSubmissions(a.status).then((rows) => rows.map(mapByoSubmission)),

    // ==== Tier 2b: agent-runtime catalog/registry ============================
    agentDefinitions: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.agent.agentDefinitions().then((rows) => rows.map(mapAgentDefinition)),

    agentVersions: (_p: unknown, a: { agentKey: string }, ctx: GraphQLContext) =>
      ctx.clients.agent.agentVersions(a.agentKey).then((rows) => rows.map(mapAgentVersionInfo)),

    tenantAgentConfig: (_p: unknown, a: { agentKey: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.agent.tenantAgentConfig(a.agentKey).then(mapTenantAgentConfig)),

    agentCeilings: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.agent.agentCeilings().then((d) => ({
        maxBudgetTokens: d.max_budget_tokens,
        maxTier: d.max_tier,
        updatedAt: d.updated_at ?? null,
        updatedBy: d.updated_by ?? null,
      })),

    agentRuns: async (_p: unknown, a: ConnectionArgs & { agentKey?: string }, ctx: GraphQLContext) => {
      const { limit } = toLimitCursor(a, ctx.config.limits);
      const page = await ctx.clients.agent.agentRuns({ agentKey: a.agentKey, limit });
      return toConnection(page, (d) => mapAgentRunListItem(ctx, d));
    },

    // ---- BRD 54 inc2: governed decision tables ------------------------------
    decisionModels: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.agent.decisionModels().then((rows) => rows.map(mapDecisionModel)),

    decisionModel: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.agent.decisionModel(a.id).then(mapDecisionModel)),

    decisionModelVersions: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.agent.decisionModelVersions(a.id).then((rows) => rows.map(mapDecisionModel)),

    // ---- BRD 56: entity resolution (steward surface) ------------------------
    resolutionRuns: (_p: unknown, a: { datasetId: string; limit?: number }, ctx: GraphQLContext) =>
      ctx.clients.dataset.resolutionRuns(a.datasetId, a.limit ?? 50).then((rows) => rows.map(mapResolutionRun)),

    resolutionRun: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.dataset.resolutionRun(a.id).then(mapResolutionRunDetail)),

    mergeCandidates: (_p: unknown, a: { runId: string; status?: string }, ctx: GraphQLContext) =>
      ctx.clients.dataset.mergeCandidates(a.runId, a.status).then((rows) => rows.map(mapMergeCandidate)),

    // ---- inc11: domain ontology (governed entity-TYPE registry) -------------
    ontologyEntities: (_p: unknown, a: { workspaceId?: string }, ctx: GraphQLContext) =>
      ctx.clients.dataset
        .ontologyEntities(a.workspaceId ?? undefined)
        .then((rows) => rows.map(mapOntologyEntity)),

    // ---- BRD 23: capability packs -------------------------------------------
    packs: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.pack.packs().then((rows) => rows.map(mapPack)),

    pack: (_p: unknown, a: { name: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.pack.pack(a.name).then(mapPack)),

    packInstalls: (_p: unknown, a: { workspaceId?: string }, ctx: GraphQLContext) =>
      ctx.clients.pack.installs(a.workspaceId).then((rows) => rows.map(mapPackInstall)),

    packInstall: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.pack.installDetail(a.id).then(mapPackInstall)),
  },

  Mutation: {
    // ---- inc11: domain ontology writes --------------------------------------
    createOntologyEntity: async (
      _p: unknown,
      a: {
        input: {
          workspaceId: string; entityKey: string; name: string; description?: string;
          attributes?: { name: string; dataType?: string }[];
          relationships?: { name: string; target: string; cardinality?: string }[];
        };
      },
      ctx: GraphQLContext,
    ) => {
      const e = await ctx.clients.dataset.createOntologyEntity({
        workspace_id: a.input.workspaceId,
        entity_key: a.input.entityKey,
        name: a.input.name,
        description: a.input.description,
        attributes: (a.input.attributes ?? []).map((x) => ({ name: x.name, data_type: x.dataType })),
        relationships: (a.input.relationships ?? []).map((x) => ({
          name: x.name, target: x.target, cardinality: x.cardinality,
        })),
      });
      return mapOntologyEntity(e);
    },

    deleteOntologyEntity: (
      _p: unknown, a: { entityKey: string; workspaceId: string }, ctx: GraphQLContext,
    ) => ctx.clients.dataset.deleteOntologyEntity(a.entityKey, a.workspaceId),

    // ---- admin: identity invite + rbac workspace/group writes ----------------
    inviteUser: async (
      _p: unknown,
      a: { input: { email: string; fullName?: string; groups?: string[] }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // The Keycloak admin path is not yet verified against a live KC: if it
      // errors, DownstreamError bubbles to the formatter (real error surfaced) —
      // we never fake a success (END STATE honesty).
      const d = await ctx.clients.identity.inviteUser(
        { email: a.input.email, full_name: a.input.fullName, groups: a.input.groups },
        a.idempotencyKey,
      );
      return mapUser(ctx, d);
    },

    createWorkspace: async (
      _p: unknown,
      a: { input: { name: string; description?: string; public?: boolean }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.createWorkspace(
        { name: a.input.name, description: a.input.description, public: a.input.public },
        a.idempotencyKey,
      );
      return mapWorkspace(ctx, d);
    },

    setEmbedConfig: async (
      _p: unknown,
      a: { tenantId: string; allowedOrigins: string[]; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.identity.setEmbedConfig(a.tenantId, a.allowedOrigins, a.idempotencyKey);
      return { __typename: "SetEmbedConfigResult" as const, embedSecret: d.embed_secret, allowedOrigins: d.allowed_origins };
    },

    setTenantIdp: async (
      _p: unknown,
      a: { input: { issuer: string; clientId?: string; discoveryUrl?: string; enabled?: boolean }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.identity.setTenantIdp(
        { issuer: a.input.issuer, client_id: a.input.clientId, discovery_url: a.input.discoveryUrl, enabled: a.input.enabled },
        a.idempotencyKey,
      );
      return { __typename: "TenantIdpConfig" as const, configured: true, issuer: d.issuer, clientId: d.client_id, discoveryUrl: d.discovery_url, enabled: d.enabled, updatedAt: d.updated_at ?? null };
    },

    deleteTenantIdp: async (_p: unknown, _a: unknown, ctx: GraphQLContext) => {
      await ctx.clients.identity.deleteTenantIdp();
      return true;
    },

    addGroupMember: async (
      _p: unknown,
      a: { groupId: string; userId: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.rbac.addGroupMember(a.groupId, a.userId, a.idempotencyKey);
      return true;
    },

    removeGroupMember: async (
      _p: unknown,
      a: { groupId: string; userId: string },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.rbac.removeGroupMember(a.groupId, a.userId);
      return true;
    },

    createTeam: async (
      _p: unknown,
      a: { input: { name: string; description?: string }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // A "Team" is specifically a permission-type group (per-workspace user
      // groupings for role assignment) — group_type is fixed here, never client-set.
      const d = await ctx.clients.rbac.createGroup(
        { name: a.input.name, description: a.input.description, group_type: "permission" },
        a.idempotencyKey,
      );
      return mapGroup(ctx, d);
    },

    updateTeam: async (
      _p: unknown,
      a: { id: string; input: { name?: string; description?: string }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.updateGroup(
        a.id,
        { name: a.input.name, description: a.input.description },
        a.idempotencyKey,
      );
      return mapGroup(ctx, d);
    },

    deleteTeam: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.rbac.deleteGroup(a.id);
      return true;
    },

    assignTeamRole: async (
      _p: unknown,
      a: { groupId: string; roleId: string },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.rbac.bindGroupRole(a.groupId, a.roleId);
      return true;
    },

    unassignTeamRole: async (
      _p: unknown,
      a: { groupId: string; roleId: string },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.rbac.unbindGroupRole(a.groupId, a.roleId);
      return true;
    },

    // ---- Tier 4b: identity/rbac admin — user lifecycle ------------------------
    updateUser: async (
      _p: unknown,
      a: { id: string; fullName: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.identity.patchUser(a.id, a.fullName, a.idempotencyKey);
      return mapUser(ctx, d);
    },

    deactivateUser: async (
      _p: unknown,
      a: { id: string; overrideLastAdmin?: boolean; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // The last-admin guard's 409 (BR-9) surfaces verbatim unless the caller
      // explicitly overrides (super-admin only, enforced downstream).
      const d = await ctx.clients.identity.deactivateUser(a.id, a.overrideLastAdmin, a.idempotencyKey);
      return mapUser(ctx, d);
    },

    resendUserInvite: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.identity.resendInvite(a.id, a.idempotencyKey);
      return mapUser(ctx, d);
    },

    deleteUser: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.identity.deleteUser(a.id);
      return true;
    },

    // ---- Tier 4b: identity/rbac admin — service-account lifecycle -------------
    createServiceAccount: async (
      _p: unknown,
      a: { input: { name: string; scopes?: string[]; expiresAt?: string }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.identity.createServiceAccount(
        { name: a.input.name, scopes: a.input.scopes, expires_at: a.input.expiresAt },
        a.idempotencyKey,
      );
      // api_key passes through verbatim — shown exactly once, never persisted.
      return mapCreatedServiceAccount(ctx, d);
    },

    rotateServiceAccount: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.identity.rotateServiceAccount(a.id, a.idempotencyKey);
      return mapCreatedServiceAccount(ctx, d);
    },

    revokeServiceAccount: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.identity.revokeServiceAccount(a.id);
      return true;
    },

    // ---- Tier 4b: identity/rbac admin — workspace lifecycle + content groups --
    updateWorkspace: async (
      _p: unknown,
      a: { id: string; input: { name?: string; description?: string; public?: boolean }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.updateWorkspace(
        a.id,
        { name: a.input.name, description: a.input.description, public: a.input.public },
        a.idempotencyKey,
      );
      return mapWorkspace(ctx, d);
    },

    archiveWorkspace: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.archiveWorkspace(a.id, a.idempotencyKey);
      return mapWorkspace(ctx, d);
    },

    restoreWorkspace: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.restoreWorkspace(a.id, a.idempotencyKey);
      return mapWorkspace(ctx, d);
    },

    linkWorkspaceContentGroup: async (
      _p: unknown,
      a: { workspaceId: string; groupId: string },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.rbac.linkContentGroup(a.workspaceId, a.groupId);
      return true;
    },

    unlinkWorkspaceContentGroup: async (
      _p: unknown,
      a: { workspaceId: string; groupId: string },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.rbac.unlinkContentGroup(a.workspaceId, a.groupId);
      return true;
    },

    createGroup: async (
      _p: unknown,
      a: { input: { name: string; description?: string; groupType: string }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // The GraphQL enum (PERMISSION|CONTENT) lowercases to rbac's wire value.
      const d = await ctx.clients.rbac.createGroup(
        { name: a.input.name, description: a.input.description, group_type: a.input.groupType.toLowerCase() },
        a.idempotencyKey,
      );
      return mapGroup(ctx, d);
    },

    updateGroup: async (
      _p: unknown,
      a: { input: { id: string; name?: string; description?: string }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // PATCH only the provided fields — the rbac handler decodes name/description
      // as pointers, so an omitted field is left untouched (groupType is fixed).
      const body: { name?: string; description?: string } = {};
      if (a.input.name !== undefined) body.name = a.input.name;
      if (a.input.description !== undefined) body.description = a.input.description;
      const d = await ctx.clients.rbac.updateGroup(a.input.id, body, a.idempotencyKey);
      return mapGroup(ctx, d);
    },

    bulkGroupMembership: async (
      _p: unknown,
      a: { groupId: string; operations: { op: string; userId: string }[]; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.bulkGroupMembers(
        a.groupId,
        a.operations.map((o) => ({ op: o.op.toLowerCase() as "add" | "remove", user_id: o.userId })),
        a.idempotencyKey,
      );
      return mapBulkGroupMembershipResult(d);
    },

    // ---- Tier 4b: identity/rbac admin — custom-role CRUD ----------------------
    createRole: async (
      _p: unknown,
      a: { input: { name: string; actions: string[] }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.createRole(a.input.name, a.input.actions, a.idempotencyKey);
      return mapRole(d);
    },

    renameRole: async (
      _p: unknown,
      a: { id: string; name: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // System roles answer 409 SYSTEM_IMMUTABLE — surfaced verbatim (the UI
      // hides mutation controls for system roles; the service still enforces).
      const d = await ctx.clients.rbac.renameRole(a.id, a.name, a.idempotencyKey);
      return mapRole(d);
    },

    setRoleActions: async (
      _p: unknown,
      a: { id: string; actions: string[]; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.setRoleActions(a.id, a.actions, a.idempotencyKey);
      return mapRole(d);
    },

    updateRole: async (
      _p: unknown,
      a: { id: string; input: { name?: string; actions?: string[] }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // System roles answer 409 SYSTEM_IMMUTABLE — surfaced verbatim (the UI
      // hides mutation controls for system roles; the service still enforces).
      const d = await ctx.clients.rbac.updateRole(a.id, a.input, a.idempotencyKey);
      return mapRole(d);
    },

    deleteRole: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.rbac.deleteRole(a.id);
      return true;
    },

    // ---- Tier 4b: identity/rbac admin — content grants ------------------------
    createContentGrant: async (
      _p: unknown,
      a: {
        input: { workspaceId: string; resourceUrn: string; subjectType: string; subjectId: string; level: string };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.rbac.createGrant(
        {
          workspace_id: a.input.workspaceId,
          resource_urn: a.input.resourceUrn,
          // rbac's createGrantRequest nests the subject object on the wire.
          subject: { type: a.input.subjectType, id: a.input.subjectId },
          level: a.input.level,
        },
        a.idempotencyKey,
      );
      return mapContentGrant(d);
    },

    deleteContentGrant: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.rbac.deleteGrant(a.id);
      return true;
    },

    createBudget: async (
      _p: unknown,
      a: {
        input: {
          workspaceId?: string; userId?: string; agentId?: string;
          meterKey: string; window: string; limitUsd: number; actionAt100?: string;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.usage.createBudget(
        {
          scope: { workspace_id: a.input.workspaceId, user_id: a.input.userId, agent_id: a.input.agentId },
          meter_key: a.input.meterKey,
          window: a.input.window,
          limit_value: a.input.limitUsd,
          action_at_100: a.input.actionAt100,
        },
        a.idempotencyKey,
      );
      return mapBudget(ctx, d);
    },

    updateBudget: async (
      _p: unknown,
      a: { id: string; input: { limitUsd?: number; actionAt100?: string }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.usage.updateBudget(
        a.id,
        { limit_value: a.input.limitUsd, action_at_100: a.input.actionAt100 },
        a.idempotencyKey,
      );
      return mapBudget(ctx, d);
    },

    deleteBudget: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.usage.deleteBudget(a.id);
      return true;
    },

    createRateCard: async (
      _p: unknown,
      a: { input: { version: number; effectiveFrom: string; items: Record<string, number> }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.usage.createRateCard(
        { version: a.input.version, effective_from: a.input.effectiveFrom, items: a.input.items },
        a.idempotencyKey,
      );
      return mapRateCard(ctx, d);
    },

    activateRateCard: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.usage.activateRateCard(a.id);
      return mapRateCard(ctx, d);
    },

    dismissAnomaly: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const dismissed = await ctx.clients.usage.dismissAnomaly(a.id);
      // dismiss's own response is thin ({id, status}); re-read the list (no
      // status filter -> includes dismissed rows too) for the full row rather
      // than fabricate the numeric fields the route didn't echo.
      const page = await ctx.clients.usage.anomalies();
      const found = (page.data ?? []).find((r) => r.id === dismissed.id);
      if (!found) {
        throw new Error(`anomaly ${dismissed.id} was dismissed but no longer appears in the list`);
      }
      return mapAnomaly(ctx, found);
    },

    // ---- audit: chain-integrity verify + compliance packs -------------------
    verifyChainIntegrity: async (
      _p: unknown,
      a: { date: string; tenantId?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.audit.verifyChain(a.date, a.tenantId);
      return mapChainVerifyResult(d);
    },

    generateSoc2Pack: async (_p: unknown, a: { from: string; to: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.audit.generateSoc2Pack(a.from, a.to);
      return mapComplianceJob(d);
    },

    generateAiDecisionLog: async (
      _p: unknown,
      a: { from: string; to: string; agentId?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.audit.generateAiDecisionLog(a.from, a.to, a.agentId);
      return mapComplianceJob(d);
    },

    updateCase: async (
      _p: unknown,
      a: { id: string; patch: { description?: string; dueDate?: string; severity?: string; customFields?: Record<string, unknown> }; idempotencyKey: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.case.update(
        a.id,
        {
          description: a.patch.description,
          due_date: a.patch.dueDate,
          severity: lower(a.patch.severity),
          custom_fields: a.patch.customFields,
        },
        a.idempotencyKey,
      );
      return mapCase(ctx, d);
    },

    createCases: async (
      _p: unknown,
      a: {
        input: {
          datasetUrn: string;
          datasetVersion?: string;
          queryUrn?: string;
          dashboardUrn?: string;
          dueDate: string;
          severity?: string;
          assignedToId?: string;
          description?: string;
          rows: { rowPk: string; displayProjection: { key: string; value: string }[] }[];
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const i = a.input;
      const d = await ctx.clients.case.createCases(
        {
          dataset_urn: i.datasetUrn,
          dataset_version: i.datasetVersion,
          query_urn: i.queryUrn,
          dashboard_urn: i.dashboardUrn,
          due_date: i.dueDate,
          severity: i.severity,
          assigned_to_id: i.assignedToId,
          description: i.description,
          rows: i.rows.map((r) => ({
            row_pk: r.rowPk,
            display_projection: Object.fromEntries(
              r.displayProjection.map((kv) => [kv.key, kv.value]),
            ),
          })),
        },
        a.idempotencyKey,
      );
      return {
        created: (d.created ?? []).map((c) => ({
          id: c.id,
          caseNumber: c.case_number ?? null,
          status: c.status ?? null,
          dedupKey: c.dedup_key ?? null,
          recurrenceOf: c.recurrence_of ?? null,
        })),
        deduplicated: (d.deduplicated ?? []).map((r) => ({
          id: r.id,
          caseNumber: r.case_number ?? null,
          rowPk: r.row_pk ?? null,
        })),
      };
    },

    bulkAssignCases: async (
      _p: unknown,
      a: { caseIds: string[]; assigneeId: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.case.bulk(
        a.caseIds,
        "assign",
        { assignee_id: a.assigneeId },
        a.idempotencyKey,
      );
      return {
        succeededIds: d.succeeded ?? [],
        failed: (d.failed ?? []).map((f) => ({ caseId: f.id, code: f.code, message: f.message })),
      };
    },

    // ---- Tier 4b: case ops (case-service lifecycle/comments/export/catalog) --
    assignCase: (
      _p: unknown,
      a: { id: string; assigneeId: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => ctx.clients.case.assign(a.id, a.assigneeId, a.idempotencyKey).then((d) => mapCase(ctx, d)),

    unassignCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.case.unassign(a.id).then((d) => mapCase(ctx, d)),

    startCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.case.start(a.id).then((d) => mapCase(ctx, d)),

    resolveCase: (
      _p: unknown,
      a: { id: string; dispositionId: string; resolutionNote?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.case
        .resolve(
          a.id,
          { disposition_id: a.dispositionId, resolution_note: a.resolutionNote },
          a.idempotencyKey,
        )
        .then((d) => mapCase(ctx, d)),

    reopenCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.case.reopen(a.id).then((d) => mapCase(ctx, d)),

    closeCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.case.close(a.id).then((d) => mapCase(ctx, d)),

    escalateCase: (
      _p: unknown,
      a: { id: string; to?: string; reason?: string },
      ctx: GraphQLContext,
    ) => ctx.clients.case.escalate(a.id, { to: a.to, reason: a.reason }).then((d) => mapCase(ctx, d)),

    addCaseComment: (
      _p: unknown,
      a: { caseId: string; body: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => ctx.clients.case.addComment(a.caseId, a.body, a.idempotencyKey).then((d) => mapCaseComment(ctx, d)),

    updateCaseComment: (_p: unknown, a: { id: string; body: string }, ctx: GraphQLContext) =>
      // PATCH /comments/{cid} echoes ONLY {id, body} — the mapper leaves every
      // other CaseComment field null rather than fabricate values the route
      // didn't return (the SDL documents this contract).
      ctx.clients.case.editComment(a.id, a.body).then((d) => mapCaseComment(ctx, d)),

    deleteCaseComment: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.case.deleteComment(a.id).then(() => true),

    exportCases: async (
      _p: unknown,
      a: { filter?: Record<string, string>; format?: string },
      ctx: GraphQLContext,
    ) => {
      // 202 carries only {operation_id}; immediately re-read the operation so
      // the caller gets its REAL current state (a fast worker may already have
      // succeeded/failed) — never a fabricated "running" placeholder.
      const { operation_id } = await ctx.clients.case.exportCases(a.filter ?? {}, a.format ?? "csv");
      const op = await ctx.clients.case.operation(operation_id);
      return mapCaseOperation(op);
    },

    createDisposition: (
      _p: unknown,
      a: {
        input: { code: string; label: string; category: string; requiresNote?: boolean; active?: boolean };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.case
        .createDisposition(
          {
            code: a.input.code,
            label: a.input.label,
            category: a.input.category,
            requires_note: a.input.requiresNote,
            active: a.input.active,
          },
          a.idempotencyKey,
        )
        .then((d) => mapDisposition(ctx, d)),

    updateDisposition: (
      _p: unknown,
      a: { id: string; input: { label?: string; category?: string; requiresNote?: boolean; active?: boolean } },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.case
        .updateDisposition(a.id, {
          label: a.input.label,
          category: a.input.category,
          requires_note: a.input.requiresNote,
          active: a.input.active,
        })
        .then((d) => mapDisposition(ctx, d)),

    createCaseField: (
      _p: unknown,
      a: {
        input: {
          queryUrn?: string;
          name: string;
          dataType: string;
          purpose?: string;
          fieldMeta?: Record<string, unknown>;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.case
        .createCaseField(
          {
            query_urn: a.input.queryUrn,
            name: a.input.name,
            data_type: a.input.dataType,
            purpose: a.input.purpose,
            field_meta: a.input.fieldMeta,
          },
          a.idempotencyKey,
        )
        .then((d) => mapCaseField(ctx, d)),

    updateCaseField: (
      _p: unknown,
      a: { input: { id: string; purpose?: string; fieldMeta?: Record<string, unknown> } },
      ctx: GraphQLContext,
    ) => {
      // Only forward explicitly-provided fields so a partial edit never clobbers
      // the field's purpose/field_meta with a null the caller didn't set.
      const body: { purpose?: string; field_meta?: Record<string, unknown> } = {};
      if (a.input.purpose !== undefined) body.purpose = a.input.purpose;
      if (a.input.fieldMeta !== undefined) body.field_meta = a.input.fieldMeta;
      return ctx.clients.case.updateCaseField(a.input.id, body).then((d) => mapCaseField(ctx, d));
    },

    deleteCaseField: (_p: unknown, a: { id: string; orphan?: boolean }, ctx: GraphQLContext) =>
      ctx.clients.case.deleteCaseField(a.id, a.orphan).then(() => true),

    putCaseSlaPolicy: (
      _p: unknown,
      a: { input: { warnBeforeSeconds?: number; onBreach?: string; escalateTo?: string; maxReassignCount?: number } },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.case
        .putSlaPolicy({
          warn_before_seconds: a.input.warnBeforeSeconds,
          on_breach: a.input.onBreach,
          escalate_to: a.input.escalateTo,
          max_reassign_count: a.input.maxReassignCount,
        })
        .then(mapCaseSlaPolicy),

    decideProposal: async (
      _p: unknown,
      a: { id: string; decision: { kind: string; reason?: string; editedArgs?: Record<string, unknown>; responseText?: string }; idempotencyKey: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.agent.decide(
        a.id,
        {
          action: decisionAction(a.decision.kind),
          message: a.decision.reason ?? a.decision.responseText,
          edited_args: a.decision.editedArgs,
        },
        a.idempotencyKey,
      );
      return mapProposal(ctx, d);
    },

    // ---- kill switches (agent-runtime + tool-plane, emergency stop) ---------
    createAgentKillSwitch: async (
      _p: unknown,
      a: { agentKey: string; scope?: string; version?: number; tenantId?: string; reason: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const created = await ctx.clients.agent.createKillSwitch(
        { agent_key: a.agentKey, scope: a.scope, version: a.version, tenant_id: a.tenantId, reason: a.reason },
        a.idempotencyKey,
      );
      // create's own response is thin ({kill_id, active}); re-read the list (the
      // caller's own tenant/operator view already covers the new row, since they
      // just successfully created it) for the full KillSwitch shape rather than
      // invent fields the route didn't echo. Falls back to echoing exactly the
      // (now server-accepted) input the caller sent — real values, not fabricated
      // — only if the row races out of the list read.
      const rows = await ctx.clients.agent.killSwitches();
      const found = rows.find((k) => k.kill_id === created.kill_id);
      return mapAgentKillSwitch(ctx, found ?? {
        kill_id: created.kill_id, scope: a.scope ?? "agent_version_tenant", agent_key: a.agentKey,
        version: a.version, tenant_id: a.tenantId, active: created.active, reason: a.reason,
        set_by: String(ctx.identity.claims.sub ?? ""),
      });
    },

    deleteAgentKillSwitch: (_p: unknown, a: { killId: string }, ctx: GraphQLContext) =>
      ctx.clients.agent.deleteKillSwitch(a.killId).then((d) => ({ id: d.kill_id, active: d.active })),

    createToolKillSwitch: async (
      _p: unknown,
      a: { toolId: string; scope: string; version?: string; tenantId?: string; reason: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const created = await ctx.clients.toolPlane.createKillSwitch(
        { tool_id: a.toolId, scope: a.scope, version: a.version, tenant_id: a.tenantId, reason: a.reason },
        a.idempotencyKey,
      );
      // create's response is thin ({id, active, set_by}) and does NOT echo the
      // server-resolved tenant_id — for a tool_tenant-scoped kill with no
      // explicit tenantId, tool-plane defaults it to the CALLER's own tenant
      // (handleCreateKill), which the client-side echo can't know. Re-read the
      // list (the caller's own create just landed there) for the real row.
      const rows = await ctx.clients.toolPlane.killSwitches();
      const found = rows.find((k) => k.id === created.id);
      return mapToolKillSwitch(ctx, found ?? created);
    },

    deleteToolKillSwitch: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.toolPlane.deleteKillSwitch(a.id).then((d) => ({ id: d.id, active: d.active })),

    // ---- memory erasure (right-to-be-forgotten, compliance-sensitive) -------
    requestMemoryErasure: async (
      _p: unknown,
      a: { subjectId: string; subjectType?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.memory.startErasure(a.subjectId, a.subjectType ?? "user");
      return mapErasureRequest(ctx, d);
    },

    // ==== Tier 2a: eval (eval-service) =======================================
    createEvalSuite: async (
      _p: unknown,
      a: {
        input: {
          suiteId: string; agentKey: string; datasets: Record<string, unknown>[];
          scorers: Record<string, unknown>[]; gateRule: string; baselineVersion?: string;
          judgeLadderPin?: Record<string, unknown>; minCases?: number;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.eval.createSuite({
        suite_id: a.input.suiteId, agent_key: a.input.agentKey, datasets: a.input.datasets,
        scorers: a.input.scorers, gate_rule: a.input.gateRule, baseline_version: a.input.baselineVersion,
        judge_ladder_pin: a.input.judgeLadderPin, min_cases: a.input.minCases,
      });
      return mapEvalSuite(ctx, d);
    },

    updateEvalSuite: async (
      _p: unknown,
      a: {
        input: {
          suiteId: string; version?: number; datasets?: Record<string, unknown>[];
          scorers?: Record<string, unknown>[]; gateRule?: string; baselineVersion?: string;
          judgeLadderPin?: Record<string, unknown>; minCases?: number;
        };
      },
      ctx: GraphQLContext,
    ) => {
      // Send only provided fields so a partial edit leaves the rest untouched.
      const patch: {
        datasets?: Record<string, unknown>[]; scorers?: Record<string, unknown>[];
        gate_rule?: string; baseline_version?: string;
        judge_ladder_pin?: Record<string, unknown>; min_cases?: number;
      } = {};
      if (a.input.datasets !== undefined) patch.datasets = a.input.datasets;
      if (a.input.scorers !== undefined) patch.scorers = a.input.scorers;
      if (a.input.gateRule !== undefined) patch.gate_rule = a.input.gateRule;
      if (a.input.baselineVersion !== undefined) patch.baseline_version = a.input.baselineVersion;
      if (a.input.judgeLadderPin !== undefined) patch.judge_ladder_pin = a.input.judgeLadderPin;
      if (a.input.minCases !== undefined) patch.min_cases = a.input.minCases;
      const d = await ctx.clients.eval.updateSuite(a.input.suiteId, patch, a.input.version);
      return mapEvalSuite(ctx, d);
    },

    createEvalRun: async (
      _p: unknown,
      a: {
        input: {
          trigger?: string; agentKey: string; candidate: Record<string, unknown>; suiteId: string;
          suiteVersion?: number; candidateOutputs?: Record<string, Record<string, unknown>>;
          baseline?: Record<string, unknown>; memorySnapshotVer?: string; costCapUsd?: number;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.eval.createRun({
        trigger: a.input.trigger, agent_key: a.input.agentKey, candidate: a.input.candidate,
        suite_id: a.input.suiteId, suite_version: a.input.suiteVersion,
        candidate_outputs: a.input.candidateOutputs, baseline: a.input.baseline,
        memory_snapshot_ver: a.input.memorySnapshotVer, cost_cap_usd: a.input.costCapUsd,
      });
      return mapEvalRun(ctx, d);
    },

    cancelEvalRun: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.cancelRun(a.id).then((d) => mapEvalRun(ctx, d)),

    createEvalDataset: async (
      _p: unknown,
      a: { input: { datasetKey: string; agentKey: string; description?: string; provenanceSummary?: Record<string, unknown> } },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.eval.createDataset({
        dataset_key: a.input.datasetKey, agent_key: a.input.agentKey,
        description: a.input.description, provenance_summary: a.input.provenanceSummary,
      });
      return mapEvalDataset(ctx, d);
    },

    freezeEvalDataset: (_p: unknown, a: { datasetKey: string; version: number }, ctx: GraphQLContext) =>
      ctx.clients.eval.freezeDataset(a.datasetKey, a.version).then((d) => mapEvalDataset(ctx, d)),

    createEvalCase: async (
      _p: unknown,
      a: {
        input: {
          datasetKey: string; agentKey?: string; input: Record<string, unknown>; expected: Record<string, unknown>;
          source?: string; sourceRef?: string; tags?: string[]; weight?: number; status?: string;
          anonymizationAttestedBy?: string;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.eval.createCase({
        dataset_key: a.input.datasetKey, agent_key: a.input.agentKey, input: a.input.input,
        expected: a.input.expected, source: a.input.source, source_ref: a.input.sourceRef,
        tags: a.input.tags, weight: a.input.weight, status: a.input.status,
        anonymization_attested_by: a.input.anonymizationAttestedBy,
      });
      return mapEvalCase(ctx, d);
    },

    promoteEvalCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.promoteCase(a.id).then((d) => mapEvalCase(ctx, d)),

    attestEvalCase: (_p: unknown, a: { id: string; attestedBy: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.attestCase(a.id, a.attestedBy).then((d) => mapEvalCase(ctx, d)),

    rejectEvalCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.rejectCase(a.id).then((d) => mapEvalCase(ctx, d)),

    retireEvalCase: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.retireCase(a.id).then((d) => mapEvalCase(ctx, d)),

    updateEvalCase: (
      _p: unknown,
      a: {
        id: string;
        patch: {
          input?: Record<string, unknown>; expected?: Record<string, unknown>; tags?: string[];
          weight?: number; anonymizationAttestedBy?: string;
        };
      },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.eval
        .patchCase(a.id, {
          input: a.patch.input, expected: a.patch.expected, tags: a.patch.tags,
          weight: a.patch.weight, anonymization_attested_by: a.patch.anonymizationAttestedBy,
        })
        .then((d) => mapEvalCase(ctx, d)),

    createEvalScorer: async (
      _p: unknown,
      a: {
        input: {
          scorerKey: string; version: number; kind: string; gateEligible?: boolean;
          configSchema?: Record<string, unknown>; applicableExpectedKinds?: string[]; imageRef?: string;
          judgePromptRef?: string; judgePromptVer?: string; judgeAgreement?: number; status?: string;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.eval.createScorer({
        scorer_key: a.input.scorerKey, version: a.input.version, kind: a.input.kind,
        gate_eligible: a.input.gateEligible, config_schema: a.input.configSchema,
        applicable_expected_kinds: a.input.applicableExpectedKinds, image_ref: a.input.imageRef,
        judge_prompt_ref: a.input.judgePromptRef, judge_prompt_ver: a.input.judgePromptVer,
        judge_agreement: a.input.judgeAgreement, status: a.input.status,
      });
      return mapEvalScorer(ctx, d);
    },

    updateEvalScorer: async (
      _p: unknown,
      a: {
        input: {
          scorerKey: string; version?: number; gateEligible?: boolean;
          configSchema?: Record<string, unknown>; applicableExpectedKinds?: string[]; imageRef?: string;
          judgePromptRef?: string; judgePromptVer?: string; judgeAgreement?: number; status?: string;
        };
      },
      ctx: GraphQLContext,
    ) => {
      // Send only provided fields so a partial edit leaves the rest untouched.
      const patch: {
        gate_eligible?: boolean; config_schema?: Record<string, unknown>;
        applicable_expected_kinds?: string[]; image_ref?: string; judge_prompt_ref?: string;
        judge_prompt_ver?: string; judge_agreement?: number; status?: string;
      } = {};
      if (a.input.gateEligible !== undefined) patch.gate_eligible = a.input.gateEligible;
      if (a.input.configSchema !== undefined) patch.config_schema = a.input.configSchema;
      if (a.input.applicableExpectedKinds !== undefined) patch.applicable_expected_kinds = a.input.applicableExpectedKinds;
      if (a.input.imageRef !== undefined) patch.image_ref = a.input.imageRef;
      if (a.input.judgePromptRef !== undefined) patch.judge_prompt_ref = a.input.judgePromptRef;
      if (a.input.judgePromptVer !== undefined) patch.judge_prompt_ver = a.input.judgePromptVer;
      if (a.input.judgeAgreement !== undefined) patch.judge_agreement = a.input.judgeAgreement;
      if (a.input.status !== undefined) patch.status = a.input.status;
      const d = await ctx.clients.eval.updateScorer(a.input.scorerKey, patch, a.input.version);
      return mapEvalScorer(ctx, d);
    },

    activateEvalScorer: (_p: unknown, a: { scorerKey: string; version: number }, ctx: GraphQLContext) =>
      ctx.clients.eval.activateScorer(a.scorerKey, a.version).then((d) => mapEvalScorer(ctx, d)),

    createEvalCanary: async (
      _p: unknown,
      a: {
        input: {
          agentKey: string; candidateVersion: string; baselineVersion: string; mode?: string;
          sampleSpec?: Record<string, unknown>; thresholds?: Record<string, unknown>; mustScorers?: string[];
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.eval.createCanary({
        agent_key: a.input.agentKey, candidate_version: a.input.candidateVersion,
        baseline_version: a.input.baselineVersion, mode: a.input.mode, sample_spec: a.input.sampleSpec,
        thresholds: a.input.thresholds, must_scorers: a.input.mustScorers,
      });
      return mapEvalCanary(ctx, d);
    },

    ingestEvalCanarySamples: (
      _p: unknown,
      a: { comparisonId: string; pairedScores: Record<string, [number, number][]> },
      ctx: GraphQLContext,
    ) => ctx.clients.eval.ingestCanarySamples(a.comparisonId, a.pairedScores).then((d) => mapEvalCanary(ctx, d)),

    stopEvalCanary: (_p: unknown, a: { comparisonId: string }, ctx: GraphQLContext) =>
      ctx.clients.eval.stopCanary(a.comparisonId).then((d) => mapEvalCanary(ctx, d)),

    setEvalSloTargets: async (
      _p: unknown,
      a: { agentKey: string; agentVersion?: string; targets: Record<string, unknown> },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.eval.setSloTargets(a.agentKey, a.agentVersion, a.targets);
      return true;
    },

    // ==== Tier 2a: ai-gateway admin ==========================================
    createAiProvider: async (
      _p: unknown,
      a: {
        input: {
          provider: string; modelFamily: string; deploymentName: string; region: string; cloud: string;
          endpointVaultRef: string; tpmLimit?: number; rpmLimit?: number; priority?: number;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.aiGateway.createProvider(
        {
          provider: a.input.provider, model_family: a.input.modelFamily, deployment_name: a.input.deploymentName,
          region: a.input.region, cloud: a.input.cloud, endpoint_vault_ref: a.input.endpointVaultRef,
          tpm_limit: a.input.tpmLimit, rpm_limit: a.input.rpmLimit, priority: a.input.priority,
        },
        a.idempotencyKey,
      );
      return mapAiProvider(ctx, d);
    },

    patchAiProvider: (
      _p: unknown,
      a: {
        deploymentId: string;
        input: {
          status?: string; priority?: number; tpmLimit?: number; rpmLimit?: number;
          endpointVaultRef?: string; reason?: string;
        };
        force?: boolean;
      },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.aiGateway
        .patchProvider(
          a.deploymentId,
          {
            status: a.input.status, priority: a.input.priority, tpm_limit: a.input.tpmLimit,
            rpm_limit: a.input.rpmLimit, endpoint_vault_ref: a.input.endpointVaultRef, reason: a.input.reason,
          },
          a.force ?? false,
        )
        .then((d) => mapAiProvider(ctx, d)),

    drainAiProvider: (_p: unknown, a: { deploymentId: string; force?: boolean }, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.drainProvider(a.deploymentId, a.force ?? false).then((d) => mapAiProvider(ctx, d)),

    putAiLadder: (
      _p: unknown,
      a: { requestClass: string; rungs: Record<string, unknown>[]; maxRung?: number; scope?: string },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.aiGateway
        .putLadder(a.requestClass, { rungs: a.rungs, max_rung: a.maxRung, scope: a.scope })
        .then(mapAiLadder),

    createAiBudget: async (
      _p: unknown,
      a: {
        input: { scopeType: string; scopeRef: string; window: string; limitUsd: number; degradePct?: number };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.aiGateway.createBudget(
        {
          scope_type: a.input.scopeType, scope_ref: a.input.scopeRef, window: a.input.window,
          limit_usd: a.input.limitUsd, degrade_pct: a.input.degradePct,
        },
        a.idempotencyKey,
      );
      return mapAiBudget(ctx, d);
    },

    updateAiBudget: (
      _p: unknown,
      a: { id: string; input: { limitUsd?: number; degradePct?: number; status?: string } },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.aiGateway
        .patchBudget(a.id, { limit_usd: a.input.limitUsd, degrade_pct: a.input.degradePct, status: a.input.status })
        .then((d) => mapAiBudget(ctx, d)),

    deleteAiBudget: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.deleteBudget(a.id).then((d) => mapAiBudget(ctx, d)),

    createAiVirtualKey: async (
      _p: unknown,
      a: {
        input: {
          principalType: string; principalId: string; allowedRequestClasses?: string[]; maxRung?: number;
          ttlSeconds?: number;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.aiGateway.createKey(
        {
          principal_type: a.input.principalType, principal_id: a.input.principalId,
          allowed_request_classes: a.input.allowedRequestClasses, max_rung: a.input.maxRung,
          ttl_seconds: a.input.ttlSeconds,
        },
        a.idempotencyKey,
      );
      return mapAiVirtualKey(ctx, d);
    },

    revokeAiVirtualKey: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.revokeKey(a.id).then((d) => mapAiVirtualKey(ctx, d)),

    rotateAiVirtualKey: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.rotateKey(a.id).then((d) => mapAiVirtualKey(ctx, d)),

    putAiGuardrailPolicy: (_p: unknown, a: { policy: Record<string, unknown> }, ctx: GraphQLContext) =>
      ctx.clients.aiGateway.putGuardrails(a.policy).then(mapAiGuardrailPolicy),

    // ---- ingestion: create / test / delete a connection (JWT passthrough) ----
    createConnection: async (
      _p: unknown,
      a: {
        input: {
          name: string; type: string; config: Record<string, unknown>;
          secrets?: Record<string, unknown>; trafficDirection?: string;
          tags?: string[]; workspaceId?: string; skipTest?: boolean;
        };
        idempotencyKey: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.ingestion.createConnection(
        {
          name: a.input.name,
          connector_type: a.input.type,
          config: a.input.config,
          secrets: a.input.secrets,
          traffic_direction: a.input.trafficDirection,
          tags: a.input.tags,
          workspace_id: a.input.workspaceId,
          skip_test: a.input.skipTest,
        },
        a.idempotencyKey,
      );
      return mapConnection(ctx, d);
    },

    testConnection: async (
      _p: unknown,
      a: { id?: string; type?: string; config?: Record<string, unknown>; secrets?: Record<string, unknown> },
      ctx: GraphQLContext,
    ) => {
      if (a.id) {
        return mapConnectionTest(await ctx.clients.ingestion.testSaved(a.id));
      }
      if (!a.type || !a.config) {
        // Local input error — the BFF invents no code, this mirrors the REST 422.
        throw gqlError(ErrorCode.VALIDATION_FAILED, "testConnection requires either id or type+config", {
          details: { field: a.type ? "config" : "type" },
        });
      }
      return mapConnectionTest(
        await ctx.clients.ingestion.testAdhoc({ connector_type: a.type, config: a.config, secrets: a.secrets }),
      );
    },

    // ---- decision write-back / SoR sync (ingestion-service, JWT passthrough) --
    createWriteback: async (
      _p: unknown,
      a: {
        input: {
          connectionId: string; decisionKind: string; decisionRef: string;
          target?: Record<string, unknown>; payload?: Record<string, unknown>; workspaceId?: string;
        };
        idempotencyKey: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.ingestion.createWriteback(
        {
          connection_id: a.input.connectionId,
          decision_kind: a.input.decisionKind,
          decision_ref: a.input.decisionRef,
          target: a.input.target,
          payload: a.input.payload,
          workspace_id: a.input.workspaceId,
          idempotency_key: a.idempotencyKey,
        },
        a.idempotencyKey,
      );
      return mapWriteback(ctx, d);
    },

    approveWriteback: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.ingestion.approveWriteback(a.id).then((d) => mapWriteback(ctx, d)),

    rejectWriteback: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.ingestion.rejectWriteback(a.id).then((d) => mapWriteback(ctx, d)),

    retryWriteback: (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      ctx.clients.ingestion.retryWriteback(a.id).then((d) => mapWriteback(ctx, d)),

    deleteConnection: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.ingestion.deleteConnection(a.id);
      return true;
    },

    // ---- Tier 4a: connection edit (secrets merge write-only downstream) -------
    updateConnection: async (
      _p: unknown,
      a: {
        id: string;
        input: {
          name?: string; config?: Record<string, unknown>; secrets?: Record<string, unknown>;
          trafficDirection?: string; tags?: string[]; skipTest?: boolean;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.ingestion.updateConnection(a.id, {
        name: a.input.name,
        config: a.input.config,
        secrets: a.input.secrets,
        traffic_direction: a.input.trafficDirection as "incoming" | "outgoing" | "both" | undefined,
        tags: a.input.tags,
        skip_test: a.input.skipTest,
      });
      return mapConnection(ctx, d);
    },

    // ---- ingestion run (ingestion-service POST /ingestions, JWT passthrough) --
    createIngestion: async (
      _p: unknown,
      a: {
        input: {
          mode: "file_upload" | "query" | "scheduled_run" | "webhook_batch";
          connectionId?: string; statement?: string; fileFormat?: string;
          datasetUrn?: string; newDatasetName?: string; newDatasetDescription?: string;
          skipProfiling?: boolean; allowEmpty?: boolean;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const i = a.input;
      const d = await ctx.clients.ingestion.createIngestion(
        {
          ingestion_mode: i.mode,
          connection_id: i.connectionId,
          statement: i.statement,
          file_format: i.fileFormat,
          dataset_urn: i.datasetUrn,
          new_dataset: i.newDatasetName
            ? { name: i.newDatasetName, description: i.newDatasetDescription }
            : undefined,
          skip_profiling: i.skipProfiling,
          allow_empty: i.allowEmpty,
        },
        a.idempotencyKey,
      );
      return mapIngestion(ctx, d);
    },

    // ---- Tier 4a: ingestion lifecycle (cancel/retry/reingest) ------------------
    cancelIngestion: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapIngestion(ctx, await ctx.clients.ingestion.cancelIngestion(a.id)),

    retryIngestion: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapIngestion(ctx, await ctx.clients.ingestion.retryIngestion(a.id)),

    reingestIngestion: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapIngestion(ctx, await ctx.clients.ingestion.reingestIngestion(a.id)),

    // ---- Tier 4a: recurring ingestion schedules -------------------------------
    createIngestionSchedule: async (
      _p: unknown,
      a: {
        input: ScheduleTimingInputGQL & {
          connectionId: string;
          ingestionTemplate: Record<string, unknown>;
          watermark?: { column: string; operator?: string; valueType?: string; initialValue: string };
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const i = a.input;
      const d = await ctx.clients.ingestion.createSchedule(
        {
          connection_id: i.connectionId,
          ingestion_template: i.ingestionTemplate,
          cron: i.cron,
          interval_seconds: i.intervalSeconds,
          timezone: i.timezone,
          watermark: i.watermark
            ? {
                column: i.watermark.column,
                operator: i.watermark.operator,
                value_type: i.watermark.valueType,
                initial_value: i.watermark.initialValue,
              }
            : undefined,
          overlap_policy: i.overlapPolicy as "skip" | "buffer_one" | undefined,
          enabled: i.enabled,
        },
        a.idempotencyKey,
      );
      return mapIngestionSchedule(ctx, d);
    },

    updateIngestionSchedule: async (
      _p: unknown,
      a: { id: string; input: ScheduleTimingInputGQL },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.ingestion.updateSchedule(a.id, scheduleUpdateBodyOf(a.input));
      return mapIngestionSchedule(ctx, d);
    },

    deleteIngestionSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.ingestion.deleteSchedule(a.id);
      return true;
    },

    pauseIngestionSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapIngestionSchedule(ctx, await ctx.clients.ingestion.pauseSchedule(a.id)),

    resumeIngestionSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapIngestionSchedule(ctx, await ctx.clients.ingestion.resumeSchedule(a.id)),

    runIngestionScheduleNow: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapScheduleRunNow(await ctx.clients.ingestion.runScheduleNow(a.id)),

    // ---- resumable uploads (ingestion-service, JWT passthrough) --------------
    // Session lifecycle only — chunk bodies are binary and never touch GraphQL;
    // see the Upload type doc comment and services/ui-web/src/app/api/uploads.
    createUpload: async (
      _p: unknown,
      a: { input: { ingestionId: string; partSize?: number; bytesTotal?: number }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.ingestion.createUpload(
        { ingestion_id: a.input.ingestionId, part_size: a.input.partSize, bytes_total: a.input.bytesTotal },
        a.idempotencyKey,
      );
      // POST /uploads only echoes {upload_id, part_size, expires_at} — it does
      // NOT echo ingestion_id/bytes_total back (unlike GET /uploads/{id}, which
      // returns the full session). Both are known from what the caller just
      // sent, so backfill them here rather than leave a real, non-nullable
      // field null (verified against the live response, not assumed).
      return mapUpload({
        ...d,
        ingestion_id: d.ingestion_id ?? a.input.ingestionId,
        bytes_total: d.bytes_total ?? a.input.bytesTotal ?? null,
      });
    },

    completeUpload: async (
      _p: unknown,
      a: { uploadId: string; input: { parts: { n: number; etag: string; size: number }[]; sha256?: string } },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.ingestion.completeUpload(a.uploadId, {
        parts: a.input.parts.map((p) => ({ n: p.n, etag: p.etag, size: p.size })),
        sha256: a.input.sha256,
      });
      return mapIngestion(ctx, d);
    },

    // ---- ad-hoc / saved query execution (query-service, JWT passthrough) -----
    runSql: async (
      _p: unknown,
      a: { input: { sql: string; limit?: number; engineHint?: string } },
      ctx: GraphQLContext,
    ) => {
      const limit = a.input.limit ?? 1000;
      const exec = await ctx.clients.query.runSQL({
        sql: a.input.sql, mode: "sync", limit, engine_hint: a.input.engineHint,
      });
      return hydrateResult(ctx, exec, limit);
    },

    runSavedQuery: async (
      _p: unknown,
      a: { id: string; limit?: number },
      ctx: GraphQLContext,
    ) => {
      const limit = a.limit ?? 1000;
      const exec = await ctx.clients.query.runSaved(a.id, { mode: "sync", limit });
      return hydrateResult(ctx, exec, limit);
    },

    // ---- Tier 4a: saved-query authoring (query-service, JWT passthrough) ------
    createSavedQuery: async (
      _p: unknown,
      a: { input: SavedQueryInputGQL; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // Saved queries are optionally workspace-scoped; thread the caller's
      // workspace claim when present (query-service accepts a missing one).
      const workspaceId = String(ctx.identity.claims.workspace_id ?? "").trim();
      const d = await ctx.clients.query.createQuery(
        { ...savedQueryBodyOf(a.input), ...(workspaceId ? { workspace_id: workspaceId } : {}) },
        a.idempotencyKey,
      );
      return mapSavedQuery(ctx, d);
    },

    updateSavedQuery: async (
      _p: unknown,
      a: { id: string; input: SavedQueryInputGQL },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.query.patchQuery(a.id, savedQueryBodyOf(a.input));
      return mapSavedQuery(ctx, d);
    },

    deleteSavedQuery: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.query.deleteQuery(a.id);
      return true;
    },

    cancelQueryExecution: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapQueryExecution(ctx, await ctx.clients.query.cancelExecution(a.id)),

    // ---- dataset archive/restore (JWT passthrough) ---------------------------
    archiveDataset: async (
      _p: unknown,
      a: { id: string; force?: boolean },
      ctx: GraphQLContext,
    ) => {
      const r = await ctx.clients.dataset.archive(a.id, a.force ?? undefined);
      return r.deleted;
    },

    restoreDataset: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.dataset.restore(a.id);
      return mapDataset(ctx, d);
    },

    updateDataset: async (
      _p: unknown,
      a: { id: string; input: { name?: string; description?: string } },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.dataset.update(a.id, {
        name: a.input.name ?? undefined,
        description: a.input.description ?? undefined,
      });
      return mapDataset(ctx, d);
    },

    // ---- Tier 4a: manual re-profile trigger (dataset-service, 202 async) ------
    reprofileDataset: async (
      _p: unknown,
      a: { id: string; versionNo?: number; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      let versionNo = a.versionNo;
      if (versionNo == null) {
        // Default to the dataset's current version (the payload nests it).
        const ds = await ctx.clients.dataset.dataset(a.id);
        versionNo = ds.current_version?.version_no ?? undefined;
        if (versionNo == null) {
          throw gqlError(
            ErrorCode.VALIDATION_FAILED,
            "reprofileDataset: dataset has no current version to profile",
            { field: "versionNo" },
          );
        }
      }
      return mapReprofile(await ctx.clients.dataset.reprofile(a.id, versionNo, a.idempotencyKey));
    },

    // ---- charts: dashboard + chart authoring (JWT passthrough) ---------------
    createDashboard: async (
      _p: unknown,
      a: {
        input: { name: string; module?: string; description?: string; layout?: unknown; meta?: unknown; tags?: string[] };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      // The backend requires a workspace; the SDL input omits it, so source it from
      // the caller's verified JWT claim (the backend takes tenant from the same token).
      // Fail closed rather than silently misfile the dashboard under an empty workspace.
      const workspaceId = String(ctx.identity.claims.workspace_id ?? "").trim();
      if (!workspaceId) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, "createDashboard: no workspace in caller token", {
          field: "workspace_id",
        });
      }
      const d = await ctx.clients.chart.createDashboard(
        {
          workspace_id: workspaceId,
          name: a.input.name,
          module: a.input.module,
          description: a.input.description,
          layout: a.input.layout,
          meta: a.input.meta,
          tags: a.input.tags,
        },
        a.idempotencyKey,
      );
      return mapDashboard(ctx, d);
    },

    updateDashboard: async (
      _p: unknown,
      a: {
        id: string;
        input: { name?: string; description?: string; layout?: unknown; meta?: unknown; tags?: string[] };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.chart.updateDashboard(
        a.id,
        {
          name: a.input.name,
          description: a.input.description,
          layout: a.input.layout,
          meta: a.input.meta,
          tags: a.input.tags,
        },
        a.idempotencyKey,
      );
      return mapDashboard(ctx, d);
    },

    deleteDashboard: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.chart.deleteDashboard(a.id);
      return true;
    },

    archiveDashboard: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.chart.archiveDashboard(a.id);
      return mapDashboard(ctx, d);
    },

    restoreDashboard: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.chart.restoreDashboard(a.id);
      return mapDashboard(ctx, d);
    },

    // ---- scheduled dashboard report subscriptions (notification-service) -----
    createReportSubscription: async (
      _p: unknown,
      a: {
        input: {
          dashboardId: string; name: string; recipients: string[]; cadence: string;
          sendHour?: number; sendWeekday?: number; timezone?: string; format?: string; enabled?: boolean;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      // The subscription's workspace is the TARGET DASHBOARD's own workspace
      // (not necessarily the caller's default workspace claim) — fetching it
      // also doubles as a real chart.dashboard.read existence/permission check
      // before notification-service ever sees the request.
      const dashboard = await ctx.clients.chart.dashboard(a.input.dashboardId);
      const workspaceId = dashboard.workspace_id;
      if (!workspaceId) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, "createReportSubscription: dashboard has no workspace_id", {
          field: "dashboardId",
        });
      }
      const d = await ctx.clients.notification.createReportSubscription(
        {
          dashboard_id: a.input.dashboardId,
          workspace_id: workspaceId,
          name: a.input.name,
          recipients: a.input.recipients,
          cadence: a.input.cadence,
          send_hour: a.input.sendHour,
          send_weekday: a.input.sendWeekday,
          timezone: a.input.timezone,
          format: a.input.format,
          enabled: a.input.enabled,
        },
        a.idempotencyKey,
      );
      return mapReportSubscription(ctx, d);
    },

    updateReportSubscription: async (
      _p: unknown,
      a: {
        id: string;
        input: {
          name?: string; recipients?: string[]; cadence?: string; sendHour?: number;
          sendWeekday?: number; timezone?: string; format?: string; enabled?: boolean;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.updateReportSubscription(
        a.id,
        {
          name: a.input.name,
          recipients: a.input.recipients,
          cadence: a.input.cadence,
          send_hour: a.input.sendHour,
          send_weekday: a.input.sendWeekday,
          timezone: a.input.timezone,
          format: a.input.format,
          enabled: a.input.enabled,
        },
        a.idempotencyKey,
      );
      return mapReportSubscription(ctx, d);
    },

    deleteReportSubscription: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.notification.deleteReportSubscription(a.id);
      return true;
    },

    pauseReportSubscription: async (_p: unknown, a: { id: string; paused: boolean }, ctx: GraphQLContext) => {
      const d = await ctx.clients.notification.updateReportSubscription(a.id, { enabled: !a.paused });
      return mapReportSubscription(ctx, d);
    },

    triggerReportSubscription: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.notification.triggerReportSubscription(a.id);
      return true;
    },

    createChart: async (
      _p: unknown,
      a: {
        input: {
          dashboardId: string; name: string; chartType: string; description?: string;
          config: Record<string, unknown>; displayMeta?: Record<string, unknown>; sources?: ChartSourceInputGQL[];
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.chart.createChart(
        a.input.dashboardId,
        {
          name: a.input.name,
          chart_type: a.input.chartType,
          description: a.input.description,
          config: a.input.config,
          display_meta: a.input.displayMeta,
          sources: mapSourcesInput(a.input.sources),
        },
        a.idempotencyKey,
      );
      return mapChart(ctx, d);
    },

    updateChart: async (
      _p: unknown,
      a: {
        id: string;
        input: {
          name?: string; chartType?: string; config?: Record<string, unknown>;
          displayMeta?: Record<string, unknown>; sources?: ChartSourceInputGQL[];
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.chart.updateChart(
        a.id,
        {
          name: a.input.name,
          chart_type: a.input.chartType,
          config: a.input.config,
          display_meta: a.input.displayMeta,
          sources: mapSourcesInput(a.input.sources),
        },
        a.idempotencyKey,
      );
      return mapChart(ctx, d);
    },

    deleteChart: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.chart.deleteChart(a.id);
      return true;
    },

    // ---- pipelines: create / validate / run (JWT passthrough) ----------------
    createPipeline: async (
      _p: unknown,
      a: {
        input: { name: string; pipelineType: string; definition: Record<string, unknown> };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      // The backend requires a workspace; the SDL input omits it, so source it from
      // the caller's verified JWT claim (the backend takes tenant from the same token).
      // Fail closed rather than silently misfile the pipeline under an empty workspace
      // if a token ever lacks the claim (the backend accepts "" without complaint).
      const workspaceId = String(ctx.identity.claims.workspace_id ?? "").trim();
      if (!workspaceId) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, "createPipeline: no workspace in caller token", {
          field: "workspace_id",
        });
      }
      const d = await ctx.clients.pipelines.createPipeline(
        {
          workspace_id: workspaceId,
          name: a.input.name,
          pipeline_type: a.input.pipelineType,
          definition: a.input.definition,
        },
        a.idempotencyKey,
      );
      return mapPipelineTemplate(ctx, d);
    },

    updatePipeline: async (
      _p: unknown,
      a: {
        id: string;
        input: { name: string; definition: Record<string, unknown> };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      // The type is immutable on update (the backend keeps it from the template) and
      // the workspace/tenant come from the token, so only name + definition are sent.
      const d = await ctx.clients.pipelines.updatePipeline(
        a.id,
        { name: a.input.name, definition: a.input.definition },
        a.idempotencyKey,
      );
      return mapPipelineTemplate(ctx, d);
    },

    validatePipeline: async (
      _p: unknown,
      a: { definition: Record<string, unknown>; pipelineType: string },
      ctx: GraphQLContext,
    ) => {
      const report = await ctx.clients.pipelines.validate({
        pipeline_type: a.pipelineType, definition: a.definition,
      });
      return mapValidationReport(report);
    },

    runPipeline: async (
      _p: unknown,
      a: { id: string; input?: { parameters?: Record<string, unknown> }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.pipelines.run(a.id, a.input?.parameters ?? {}, a.idempotencyKey);
      return mapPipelineRun(ctx, d);
    },

    // ---- Tier 4a: pipeline run lifecycle (terminate/retry) --------------------
    terminatePipelineRun: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapPipelineRun(ctx, await ctx.clients.pipelines.terminateRun(a.id)),

    retryPipelineRun: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => mapPipelineRun(ctx, await ctx.clients.pipelines.retryRun(a.id, a.idempotencyKey)),

    // ---- Tier 4a: pipeline template lifecycle ---------------------------------
    clonePipelineTemplate: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => mapPipelineTemplate(ctx, await ctx.clients.pipelines.clonePipeline(a.id, a.idempotencyKey)),

    activatePipelineTemplateVersion: async (
      _p: unknown,
      a: { templateId: string; versionId: string },
      ctx: GraphQLContext,
    ) => mapPipelineTemplate(ctx, await ctx.clients.pipelines.activateVersion(a.templateId, a.versionId)),

    compilePipelineTemplate: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapCompiledPipelineManifest(await ctx.clients.pipelines.compilePipeline(a.id)),

    deletePipelineTemplate: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapPipelineTemplate(ctx, await ctx.clients.pipelines.deletePipeline(a.id)),

    restorePipelineTemplate: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapPipelineTemplate(ctx, await ctx.clients.pipelines.restorePipeline(a.id)),

    // ---- Tier 4a: recurring pipeline schedules (PIPE-FR-050) ------------------
    createPipelineSchedule: async (
      _p: unknown,
      a: {
        input: {
          templateId: string;
          name?: string;
          cron: string;
          timezone?: string;
          runParameters?: Record<string, unknown>;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const i = a.input;
      const d = await ctx.clients.pipelines.createPipelineSchedule(
        {
          template_id: i.templateId,
          name: i.name,
          cron: i.cron,
          timezone: i.timezone,
          run_parameters: i.runParameters,
        },
        a.idempotencyKey,
      );
      return mapPipelineSchedule(ctx, d);
    },

    pausePipelineSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapPipelineSchedule(ctx, await ctx.clients.pipelines.pausePipelineSchedule(a.id)),

    resumePipelineSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapPipelineSchedule(ctx, await ctx.clients.pipelines.resumePipelineSchedule(a.id)),

    runNowPipelineSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const { run } = await ctx.clients.pipelines.runNowPipelineSchedule(a.id);
      if (run == null) {
        throw gqlError(ErrorCode.INTERNAL, "runNowPipelineSchedule: schedule fire produced no run", {
          scheduleId: a.id,
        });
      }
      return mapPipelineRun(ctx, run);
    },

    deletePipelineSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.pipelines.deletePipelineSchedule(a.id);
      return true;
    },

    // ---- ml: experiment create + promotion + inference (JWT passthrough) -----
    createExperiment: async (
      _p: unknown,
      a: {
        input: {
          name: string; modelType: string; description?: string;
          modelPipelineUrn: string; featureEngineeringPipelineUrn: string; trainingPipelineUrn: string;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      // The backend requires a workspace; the SDL input omits it, so source it from
      // the caller's verified JWT claim (the backend takes tenant from the same
      // token). Fail closed rather than misfile the experiment under an empty workspace.
      const workspaceId = String(ctx.identity.claims.workspace_id ?? "").trim();
      if (!workspaceId) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, "createExperiment: no workspace in caller token", {
          field: "workspace_id",
        });
      }
      const d = await ctx.clients.experiment.createExperiment(
        {
          workspace_id: workspaceId,
          name: a.input.name,
          model_type: a.input.modelType,
          model_pipeline_urn: a.input.modelPipelineUrn,
          feature_engineering_pipeline_urn: a.input.featureEngineeringPipelineUrn,
          training_pipeline_urn: a.input.trainingPipelineUrn,
          description: a.input.description,
        },
        a.idempotencyKey,
      );
      return mapExperiment(ctx, d);
    },

    archiveExperiment: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.experiment.archiveExperiment(a.id);
      return mapExperiment(ctx, d);
    },

    restoreExperiment: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.experiment.restoreExperiment(a.id);
      return mapExperiment(ctx, d);
    },

    promoteModelVersion: async (
      _p: unknown,
      a: { modelId: string; version: number; targetStage: string; rationale?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.experiment.promoteVersion(
        a.modelId,
        a.version,
        { target_stage: a.targetStage, rationale: a.rationale },
        a.idempotencyKey,
      );
      return {
        promotionId: d.promotion_id ?? "",
        status: d.status ?? "pending",
        operationId: d.operation_id ?? null,
      };
    },

    decidePromotion: async (
      _p: unknown,
      a: { promotionId: string; decision: string; message?: string },
      ctx: GraphQLContext,
    ) => {
      // The service enforces four-eyes (self-approval forbidden) + single-production;
      // a violation surfaces verbatim as its downstream error (no masking).
      return ctx.clients.experiment.decidePromotion(a.promotionId, {
        decision: a.decision,
        message: a.message,
      });
    },

    createInferenceJob: async (
      _p: unknown,
      a: {
        input: {
          modelVersionUrn: string; inputDatasetUrn: string;
          name?: string; description?: string; allowUnpromoted?: boolean;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.inference.createJob(
        {
          model_version_urn: a.input.modelVersionUrn,
          input_dataset_urn: a.input.inputDatasetUrn,
          name: a.input.name,
          description: a.input.description,
          allow_unpromoted: a.input.allowUnpromoted ?? false,
        },
        a.idempotencyKey,
      );
      return mapInferenceJob(ctx, d);
    },

    // ---- Tier 4b: ml ops (experiment-service registration/notes/cards) -------
    registerRunAsModel: async (
      _p: unknown,
      a: {
        experimentId: string;
        runId: string;
        input: { modelName: string; description?: string; flavor?: string; ownerId?: string };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      // RunNotFinished / ModelTypeMismatch surface verbatim as downstream errors.
      const d = await ctx.clients.experiment.registerRun(
        a.experimentId,
        a.runId,
        {
          model_name: a.input.modelName,
          description: a.input.description,
          flavor: a.input.flavor,
          owner_id: a.input.ownerId,
        },
        a.idempotencyKey,
      );
      return mapRegisterModelResult(d);
    },

    updateExperiment: async (
      _p: unknown,
      a: { id: string; input: { name?: string; description?: string; note?: string } },
      ctx: GraphQLContext,
    ) => {
      // Omitted GraphQL fields serialize as absent JSON keys, so the service's
      // exclude_unset PATCH semantics hold end-to-end.
      const d = await ctx.clients.experiment.patchExperiment(a.id, {
        name: a.input.name,
        description: a.input.description,
        note: a.input.note,
      });
      return mapExperiment(ctx, d);
    },

    upsertRunNote: async (
      _p: unknown,
      a: { runId: string; description: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.experiment.upsertRunNote(a.runId, a.description);
      return mapRunNote(d);
    },

    deleteRunNote: async (_p: unknown, a: { runId: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.experiment.deleteRunNote(a.runId);
      return d?.note_deleted ?? true;
    },

    updateModelCard: async (
      _p: unknown,
      a: {
        modelId: string;
        version: number;
        input: {
          intendedUse?: string;
          limitations?: string;
          evaluationSummary?: string;
          ethicalConsiderations?: string;
        };
      },
      ctx: GraphQLContext,
    ) => {
      // Only the fields the caller set are sent (absent keys stay absent), so
      // the service's exclude_unset overlay patch leaves the rest untouched.
      // The response is the full MERGED card, verbatim as JSON.
      return ctx.clients.experiment.patchModelCard(a.modelId, a.version, {
        intended_use: a.input.intendedUse,
        limitations: a.input.limitations,
        evaluation_summary: a.input.evaluationSummary,
        ethical_considerations: a.input.ethicalConsiderations,
      });
    },

    // ---- Tier 4b: ml ops (inference-service job lifecycle + validate + bulk) --
    cancelInferenceJob: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      // Illegal-state cancels 409 downstream and surface verbatim.
      const d = await ctx.clients.inference.cancelJob(a.id);
      return mapInferenceJob(ctx, d);
    },

    retryInferenceJob: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      // 202 {operation_id, job_id} → GET the NEW job (client mirrors createJob).
      const d = await ctx.clients.inference.retryJob(a.id, a.idempotencyKey);
      return mapInferenceJob(ctx, d);
    },

    deleteInferenceJob: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.inference.deleteJob(a.id);
      return true;
    },

    validateInference: async (
      _p: unknown,
      a: {
        input: {
          modelVersionUrn: string;
          inputDatasetUrn: string;
          allowUnpromoted?: boolean;
          allowEmpty?: boolean;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.inference.validate({
        model_version_urn: a.input.modelVersionUrn,
        input_dataset_urn: a.input.inputDatasetUrn,
        allow_unpromoted: a.input.allowUnpromoted ?? false,
        allow_empty: a.input.allowEmpty ?? false,
      });
      return mapCompatibilityReport(d);
    },

    bulkCreateInferenceJobs: async (
      _p: unknown,
      a: {
        input: {
          modelVersionUrn: string;
          inputDatasetUrns: string[];
          parameters?: Record<string, unknown>;
          outputDatasetName?: string;
          outputMode?: string;
        };
      },
      ctx: GraphQLContext,
    ) => {
      // The real per-dataset result list, verbatim (partial failure per entry).
      return ctx.clients.inference.bulkCreate({
        model_version_urn: a.input.modelVersionUrn,
        input_dataset_urns: a.input.inputDatasetUrns,
        parameters: a.input.parameters,
        output:
          a.input.outputDatasetName || a.input.outputMode
            ? { dataset_name: a.input.outputDatasetName, mode: a.input.outputMode }
            : undefined,
      });
    },

    // ---- Tier 4b: ml ops (inference-service schedules) ------------------------
    createInferenceSchedule: async (
      _p: unknown,
      a: {
        input: {
          name: string;
          inputSelector: Record<string, unknown>;
          output: Record<string, unknown>;
          modelVersionUrn?: string;
          modelUrn?: string;
          stageSelector?: string;
          cron?: string;
          intervalSeconds?: number;
          timezone?: string;
          overlapPolicy?: string;
          enabled?: boolean;
          notifyOnFailure?: boolean;
        };
      },
      ctx: GraphQLContext,
    ) => {
      // Server validation (verbatim on violation): exactly one of
      // model_version_urn/model_urn, model_urn requires stage_selector, and
      // exactly one of cron/interval_seconds.
      const d = await ctx.clients.inference.createSchedule({
        name: a.input.name,
        input_selector: a.input.inputSelector,
        output: a.input.output,
        model_version_urn: a.input.modelVersionUrn,
        model_urn: a.input.modelUrn,
        stage_selector: a.input.stageSelector,
        cron: a.input.cron,
        interval_seconds: a.input.intervalSeconds,
        timezone: a.input.timezone,
        overlap_policy: a.input.overlapPolicy,
        enabled: a.input.enabled,
        notify_on_failure: a.input.notifyOnFailure,
      });
      return mapInferenceSchedule(ctx, d);
    },

    updateInferenceSchedule: async (
      _p: unknown,
      a: {
        id: string;
        input: {
          cron?: string;
          intervalSeconds?: number;
          timezone?: string;
          overlapPolicy?: string;
          inputSelector?: Record<string, unknown>;
          output?: Record<string, unknown>;
          notifyOnFailure?: boolean;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.inference.patchSchedule(a.id, {
        cron: a.input.cron,
        interval_seconds: a.input.intervalSeconds,
        timezone: a.input.timezone,
        overlap_policy: a.input.overlapPolicy,
        input_selector: a.input.inputSelector,
        output: a.input.output,
        notify_on_failure: a.input.notifyOnFailure,
      });
      return mapInferenceSchedule(ctx, d);
    },

    deleteInferenceSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.inference.deleteSchedule(a.id);
      return true;
    },

    pauseInferenceSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.inference.pauseSchedule(a.id);
      return mapInferenceSchedule(ctx, d);
    },

    resumeInferenceSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      const d = await ctx.clients.inference.resumeSchedule(a.id);
      return mapInferenceSchedule(ctx, d);
    },

    triggerInferenceSchedule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      // The real fire result verbatim: {fired, job_id, status} | {fired: false, reason, error?}.
      ctx.clients.inference.triggerSchedule(a.id),

    // ---- semantic model authoring: create/version/review workflow ------------
    createSemanticModel: async (
      _p: unknown,
      a: { input: { name: string; description?: string; definition?: Record<string, unknown> }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const workspaceId = String(ctx.identity.claims.workspace_id ?? "").trim();
      if (!workspaceId) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, "createSemanticModel: no workspace in caller token", {
          field: "workspace_id",
        });
      }
      const d = await ctx.clients.semantic.createModel(
        {
          workspace_id: workspaceId,
          name: a.input.name,
          description: a.input.description,
          definition: a.input.definition,
        },
        a.idempotencyKey,
      );
      return mapSemanticModelSummary(ctx, d);
    },

    updateSemanticModel: async (
      _p: unknown,
      a: { id: string; input: { name?: string; description?: string } },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.semantic.patchModel(a.id, a.input);
      return mapSemanticModelSummary(ctx, d);
    },

    deleteSemanticModel: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.semantic.deleteModel(a.id);
      return true;
    },

    createSemanticModelVersion: async (
      _p: unknown,
      a: { modelId: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.semantic.createVersion(a.modelId, a.idempotencyKey);
      return mapSemanticModelVersion(ctx, a.modelId, d);
    },

    updateSemanticModelDraft: async (
      _p: unknown,
      a: { modelId: string; versionNo: number; definition: Record<string, unknown> },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.semantic.patchDraft(a.modelId, a.versionNo, a.definition);
      return mapSemanticModelVersion(ctx, a.modelId, d);
    },

    submitSemanticModelVersion: async (
      _p: unknown,
      a: { modelId: string; versionNo: number },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.semantic.submit(a.modelId, a.versionNo);
      return mapSemanticModelVersion(ctx, a.modelId, d);
    },

    approveSemanticModelVersion: async (
      _p: unknown,
      a: { modelId: string; versionNo: number; note?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.semantic.approve(a.modelId, a.versionNo, a.note);
      return mapSemanticModelVersion(ctx, a.modelId, d);
    },

    rejectSemanticModelVersion: async (
      _p: unknown,
      a: { modelId: string; versionNo: number; note: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.semantic.reject(a.modelId, a.versionNo, a.note);
      return mapSemanticModelVersion(ctx, a.modelId, d);
    },

    // ---- Tier 4a: bootstrap-from-dataset (semantic-service, 202 async) --------
    bootstrapSemanticModel: async (
      _p: unknown,
      a: { modelId: string; sources?: Record<string, unknown>; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) =>
      mapSemanticOperation(
        await ctx.clients.semantic.bootstrap(a.modelId, a.sources ?? {}, a.idempotencyKey),
      ),

    // ---- Tier 4a: verified NL↔SQL pairs (semantic-service, four-eyes) ---------
    createVerifiedQuery: async (
      _p: unknown,
      a: {
        input: { nlText: string; sqlText: string; variables?: unknown[]; model?: string; tags?: string[] };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      // verified-queries are workspace-scoped and the service requires it in
      // the body; fail closed when the caller's token carries no workspace.
      const workspaceId = String(ctx.identity.claims.workspace_id ?? "").trim();
      if (!workspaceId) {
        throw gqlError(ErrorCode.VALIDATION_FAILED, "createVerifiedQuery: no workspace in caller token", {
          field: "workspace_id",
        });
      }
      const d = await ctx.clients.semantic.createVerifiedQuery(
        {
          workspace_id: workspaceId,
          nl_text: a.input.nlText,
          sql_text: a.input.sqlText,
          variables: a.input.variables,
          model: a.input.model,
          tags: a.input.tags,
        },
        a.idempotencyKey,
      );
      return mapVerifiedQuery(ctx, d);
    },

    updateVerifiedQuery: async (
      _p: unknown,
      a: { id: string; input: { nlText?: string; sqlText?: string; variables?: unknown[]; tags?: string[] } },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.semantic.patchVerifiedQuery(a.id, {
        nl_text: a.input.nlText,
        sql_text: a.input.sqlText,
        variables: a.input.variables,
        tags: a.input.tags,
      });
      return mapVerifiedQuery(ctx, d);
    },

    submitVerifiedQuery: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapVerifiedQuery(ctx, await ctx.clients.semantic.submitVerifiedQuery(a.id)),

    approveVerifiedQuery: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapVerifiedQuery(ctx, await ctx.clients.semantic.approveVerifiedQuery(a.id)),

    rejectVerifiedQuery: async (_p: unknown, a: { id: string; note?: string }, ctx: GraphQLContext) =>
      mapVerifiedQuery(ctx, await ctx.clients.semantic.rejectVerifiedQuery(a.id, a.note)),

    archiveVerifiedQuery: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) =>
      mapVerifiedQuery(ctx, await ctx.clients.semantic.archiveVerifiedQuery(a.id)),

    // ==== Tier 2b: notification-service ======================================
    markNotificationRead: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.notification.setNotificationRead(a.id, true);
      return true;
    },

    markNotificationUnread: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.notification.setNotificationRead(a.id, false);
      return true;
    },

    markAllNotificationsRead: (_p: unknown, _a: unknown, ctx: GraphQLContext) =>
      ctx.clients.notification.markAllRead(),

    updateNotificationPreferences: async (
      _p: unknown,
      a: {
        input: {
          channelOverrides?: Record<string, string[]>;
          mutes?: { event_types?: string[]; resource_urns?: string[] };
          quietHours?: { tz: string; start: string; end: string } | null;
          digestConfig?: Record<string, string>;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.putPreferences(
        {
          channel_overrides: a.input.channelOverrides,
          mutes: a.input.mutes,
          quiet_hours: a.input.quietHours,
          digest_config: a.input.digestConfig,
        },
        a.idempotencyKey,
      );
      return mapNotificationPreferences(d);
    },

    createNotificationRule: async (
      _p: unknown,
      a: { input: NotificationRuleInputGQL; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.createRule(ruleBodyOf(a.input), a.idempotencyKey);
      return mapNotificationRule(d);
    },

    updateNotificationRule: async (
      _p: unknown,
      a: { id: string; input: NotificationRuleInputGQL; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.updateRule(a.id, ruleBodyOf(a.input), a.idempotencyKey);
      return mapNotificationRule(d);
    },

    deleteNotificationRule: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.notification.deleteRule(a.id);
      return true;
    },

    createNotificationWebhook: async (
      _p: unknown,
      a: { input: { url: string; eventTypes: string[]; active?: boolean }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.createWebhook(
        { url: a.input.url, event_types: a.input.eventTypes, active: a.input.active },
        a.idempotencyKey,
      );
      return mapWebhookEndpoint(d);
    },

    updateNotificationWebhook: async (
      _p: unknown,
      a: { id: string; input: { url?: string; eventTypes?: string[]; active?: boolean }; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.updateWebhook(
        a.id,
        { url: a.input.url, event_types: a.input.eventTypes, active: a.input.active },
        a.idempotencyKey,
      );
      return mapWebhookEndpoint(d);
    },

    deleteNotificationWebhook: async (_p: unknown, a: { id: string }, ctx: GraphQLContext) => {
      await ctx.clients.notification.deleteWebhook(a.id);
      return true;
    },

    rotateNotificationWebhookSecret: async (
      _p: unknown,
      a: { id: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.rotateWebhookSecret(a.id, a.idempotencyKey);
      return mapWebhookEndpoint(d);
    },

    redeliverNotificationWebhookDelivery: async (
      _p: unknown,
      a: { webhookId: string; deliveryId: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.notification.redeliverWebhookDelivery(a.webhookId, a.deliveryId, a.idempotencyKey);
      return true;
    },

    createNotificationTemplate: async (
      _p: unknown,
      a: {
        input: { key: string; channel: string; locale?: string; subjectTpl?: string; bodyHtmlTpl?: string; bodyTextTpl?: string };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.createTemplate(
        {
          key: a.input.key, channel: a.input.channel, locale: a.input.locale,
          subject_tpl: a.input.subjectTpl, body_html_tpl: a.input.bodyHtmlTpl, body_text_tpl: a.input.bodyTextTpl,
        },
        a.idempotencyKey,
      );
      return mapNotificationTemplate(d);
    },

    publishNotificationTemplate: async (
      _p: unknown,
      a: { key: string; templateId: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.publishTemplate(a.key, a.templateId, a.idempotencyKey);
      return mapNotificationTemplate(d);
    },

    previewNotificationTemplate: async (
      _p: unknown,
      a: { key: string; channel?: string; locale?: string; sampleEvent?: Record<string, unknown> },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.notification.previewTemplate(a.key, {
        channel: a.channel, locale: a.locale, sample_event: a.sampleEvent,
      });
      return { subject: d.subject, html: d.html, text: d.text };
    },

    clearEmailSuppression: async (_p: unknown, a: { emailHash: string }, ctx: GraphQLContext) => {
      await ctx.clients.notification.clearSuppression(a.emailHash);
      return true;
    },

    // ==== Tier 2b: tool-plane registry admin =================================
    registerTool: async (
      _p: unknown,
      a: {
        input: {
          toolId: string; displayName?: string; ownerService: string; ownerTeam?: string;
          enabledByDefault?: boolean; sideEffects?: string; tags?: string[];
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.registerTool(
        {
          tool_id: a.input.toolId, display_name: a.input.displayName, owner_service: a.input.ownerService,
          owner_team: a.input.ownerTeam, enabled_by_default: a.input.enabledByDefault,
          side_effects: a.input.sideEffects, tags: a.input.tags,
        },
        a.idempotencyKey,
      );
      return mapTool(d);
    },

    addToolVersion: async (
      _p: unknown,
      a: {
        toolId: string;
        input: {
          version: string; semanticDescription: string; inputSchema?: Record<string, unknown>;
          outputSchema?: Record<string, unknown>; permissionTier?: string; costWeight?: number;
          declaredSla?: { p95_ms?: number; error_rate_pct?: number }; sideEffects?: string; examples?: unknown;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.addToolVersion(
        a.toolId,
        {
          version: a.input.version, semantic_description: a.input.semanticDescription,
          input_schema: a.input.inputSchema, output_schema: a.input.outputSchema,
          permission_tier: a.input.permissionTier, cost_weight: a.input.costWeight,
          declared_sla: a.input.declaredSla, side_effects: a.input.sideEffects,
          examples: a.input.examples as { input?: Record<string, unknown>; description?: string }[] | undefined,
        },
        a.idempotencyKey,
      );
      return mapToolVersion(d);
    },

    publishToolVersion: async (
      _p: unknown,
      a: { toolId: string; version: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.publishToolVersion(a.toolId, a.version, a.idempotencyKey);
      return mapToolVersion(d);
    },

    deprecateToolVersion: async (
      _p: unknown,
      a: { toolId: string; version: string; deprecationEndsAt?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.deprecateToolVersion(
        a.toolId, a.version, a.deprecationEndsAt, a.idempotencyKey,
      );
      return { status: d.status, deprecationEndsAt: d.deprecation_ends_at ?? null };
    },

    retireToolVersion: async (
      _p: unknown,
      a: { toolId: string; version: string; force?: boolean; reason?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.retireToolVersion(
        a.toolId, a.version, a.force ?? false, a.reason, a.idempotencyKey,
      );
      return { status: d.status, deprecationEndsAt: null };
    },

    setToolEnablement: async (
      _p: unknown,
      a: {
        toolId: string;
        input: { enabled: boolean; maxTierOverride?: string; argumentConstraints?: Record<string, unknown>; rateLimitPerMin?: number };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.setToolEnablement(
        a.toolId,
        {
          enabled: a.input.enabled,
          max_tier_override: a.input.maxTierOverride,
          argument_constraints: a.input.argumentConstraints,
          rate_limit_override: a.input.rateLimitPerMin ? { per_min: a.input.rateLimitPerMin } : undefined,
        },
        a.idempotencyKey,
      );
      return mapTenantToolSettings(d);
    },

    submitByoTool: async (
      _p: unknown,
      a: {
        input: { manifest?: Record<string, unknown>; endpointUrl: string; authMethod?: string; requestedTier?: string; egressDescription?: string };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.submitBYO(
        {
          manifest: a.input.manifest, endpoint_url: a.input.endpointUrl, auth_method: a.input.authMethod,
          requested_tier: a.input.requestedTier, data_egress_description: a.input.egressDescription,
        },
        a.idempotencyKey,
      );
      return mapByoSubmission(d);
    },

    approveByoTool: async (
      _p: unknown,
      a: { id: string; message?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.decideBYO(a.id, "approve", a.message, a.idempotencyKey);
      return { id: d.id, status: d.status, decidedBy: d.decided_by };
    },

    rejectByoTool: async (
      _p: unknown,
      a: { id: string; message?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.toolPlane.decideBYO(a.id, "reject", a.message, a.idempotencyKey);
      return { id: d.id, status: d.status, decidedBy: d.decided_by };
    },

    // ==== Tier 2b: agent-runtime catalog/registry ============================
    publishAgentVersion: async (
      _p: unknown,
      a: { agentKey: string; version: number; force?: boolean; reason?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.agent.publishAgentVersion(
        a.agentKey, a.version, a.force, a.reason, a.idempotencyKey,
      );
      return { agentKey: d.agent_key, version: d.version, status: d.status };
    },

    putTenantAgentConfig: async (
      _p: unknown,
      a: {
        agentKey: string;
        input: { enabled?: boolean; pinnedVersion?: number | null; promptParams?: Record<string, unknown>; autoExecutePolicy?: Record<string, unknown>; selfApproval?: boolean };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      await ctx.clients.agent.putTenantAgentConfig(
        a.agentKey,
        {
          enabled: a.input.enabled,
          pinned_version: a.input.pinnedVersion,
          prompt_params: a.input.promptParams,
          auto_execute_policy: a.input.autoExecutePolicy,
          self_approval: a.input.selfApproval,
        },
        a.idempotencyKey,
      );
      // The PUT response is thin ({agent_key, enabled, pinned_version}); re-read
      // the config for the full row rather than fabricate the missing fields.
      const d = await ctx.clients.agent.tenantAgentConfig(a.agentKey);
      return mapTenantAgentConfig(d);
    },

    createCustomAgent: async (
      _p: unknown,
      a: {
        input: {
          displayName: string;
          persona: string;
          systemPrompt?: string;
          allowedTools: string[];
          proposeTool?: string | null;
          dataScopeWorkspaces?: string[];
          budgetMaxTokensPerSession?: number;
          blockPiiEgress?: boolean;
          redactPii?: boolean;
        };
      },
      ctx: GraphQLContext,
    ) => {
      const i = a.input;
      const dataScope =
        i.dataScopeWorkspaces && i.dataScopeWorkspaces.length > 0
          ? { workspaces: i.dataScopeWorkspaces }
          : undefined;
      const budget =
        i.budgetMaxTokensPerSession != null
          ? { max_tokens_per_session: i.budgetMaxTokensPerSession }
          : undefined;
      const pii =
        i.blockPiiEgress || i.redactPii
          ? { block_pii_egress: !!i.blockPiiEgress, redact: !!i.redactPii }
          : undefined;
      const d = await ctx.clients.agent.createCustomAgent({
        display_name: i.displayName,
        persona: i.persona,
        system_prompt: i.systemPrompt,
        allowed_tools: i.allowedTools,
        propose_tool: i.proposeTool ?? undefined,
        data_scope: dataScope,
        budget,
        pii,
      });
      return {
        agentKey: d.agent_key,
        status: d.status,
        graphRef: d.graph_ref,
        allowedTools: d.allowed_tools ?? [],
        persona: d.persona,
        ownerTenant: d.owner_tenant,
        guardrailPolicy: d.guardrail_policy ?? {},
      };
    },

    autobindPersonaCopilots: (
      _p: unknown,
      a: { roles: string[]; proposeTool?: string | null },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.agent.autobindPersonaCopilots(a.roles, a.proposeTool ?? undefined).then((d) => ({
        created: (d.created ?? []).map((x) => ({ role: x.role, agentKey: x.agent_key })),
        skipped: (d.skipped ?? []).map((x) => ({ role: x.role, agentKey: x.agent_key })),
      })),

    setAgentCeilings: (
      _p: unknown,
      a: { maxBudgetTokens: number; maxTier: string },
      ctx: GraphQLContext,
    ) =>
      ctx.clients.agent.setAgentCeilings(a.maxBudgetTokens, a.maxTier).then((d) => ({
        maxBudgetTokens: d.max_budget_tokens,
        maxTier: d.max_tier,
        updatedAt: d.updated_at ?? null,
        updatedBy: d.updated_by ?? null,
      })),

    // ---- BRD 54 inc2: governed decision tables ------------------------------
    createDecisionModel: async (
      _p: unknown,
      a: {
        input: {
          name: string;
          workspaceId?: string;
          rules: Array<{ when: Array<{ column: string; op: string; value?: unknown }>; then: { dispositionCode: string; severity: string }; note?: string }>;
          defaultOutcome?: { dispositionCode: string; severity: string } | null;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.agent.createDecisionModel(
        {
          name: a.input.name,
          workspace_id: a.input.workspaceId,
          rules: a.input.rules.map((r) => ({
            when: r.when.map((c) => ({ column: c.column, op: c.op, value: c.value })),
            then: { disposition_code: r.then.dispositionCode, severity: r.then.severity },
            note: r.note,
          })),
          default_outcome: a.input.defaultOutcome
            ? { disposition_code: a.input.defaultOutcome.dispositionCode, severity: a.input.defaultOutcome.severity }
            : null,
        },
        a.idempotencyKey,
      );
      return mapDecisionModel(d);
    },

    batchEvaluateDecisionModel: async (
      _p: unknown,
      a: { id: string; input: { workspaceId?: string; caseIds?: string[]; limit?: number }; propose?: boolean; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.agent.batchEvaluateDecisionModel(
        a.id,
        { workspace_id: a.input.workspaceId, case_ids: a.input.caseIds, limit: a.input.limit },
        Boolean(a.propose),
        a.idempotencyKey,
      );
      return mapBatchEvaluate(d);
    },

    approveDecisionModel: async (
      _p: unknown, a: { id: string; idempotencyKey?: string }, ctx: GraphQLContext,
    ) => mapDecisionModel(await ctx.clients.agent.approveDecisionModel(a.id, a.idempotencyKey)),

    newDecisionModelVersion: async (
      _p: unknown,
      a: {
        id: string;
        input: {
          rules: Array<{ when: Array<{ column: string; op: string; value?: unknown }>; then: { dispositionCode: string; severity: string }; note?: string }>;
          defaultOutcome?: { dispositionCode: string; severity: string } | null;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.agent.newDecisionModelVersion(
        a.id,
        {
          rules: a.input.rules.map((r) => ({
            when: r.when.map((c) => ({ column: c.column, op: c.op, value: c.value })),
            then: { disposition_code: r.then.dispositionCode, severity: r.then.severity },
            note: r.note,
          })),
          default_outcome: a.input.defaultOutcome
            ? { disposition_code: a.input.defaultOutcome.dispositionCode, severity: a.input.defaultOutcome.severity }
            : null,
        },
        a.idempotencyKey,
      );
      return mapDecisionModel(d);
    },

    // ---- BRD 56: entity resolution (steward surface) ------------------------
    resolveEntities: async (
      _p: unknown,
      a: {
        datasetId: string;
        input: {
          pkColumn: string;
          config: {
            entityType?: string;
            deterministicKeys?: string[][];
            scoringFields?: Array<{ column: string; weight?: number }>;
            blockingFields?: string[];
            autoMergeThreshold?: number;
            reviewThreshold?: number;
          };
          rowLimit?: number;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const cfg = a.input.config;
      const d = await ctx.clients.dataset.resolveEntities(
        a.datasetId,
        {
          pk_column: a.input.pkColumn,
          row_limit: a.input.rowLimit,
          config: {
            entity_type: cfg.entityType,
            deterministic_keys: cfg.deterministicKeys ?? [],
            scoring_fields: (cfg.scoringFields ?? []).map((f) => ({ column: f.column, weight: f.weight })),
            blocking_fields: cfg.blockingFields ?? [],
            auto_merge_threshold: cfg.autoMergeThreshold,
            review_threshold: cfg.reviewThreshold,
          },
        },
        a.idempotencyKey,
      );
      return mapResolveEntities(d);
    },

    proposeEntityMerge: async (
      _p: unknown,
      a: {
        input: {
          datasetId: string;
          runId: string;
          candidateId: string;
          leftPk?: string;
          rightPk?: string;
          score?: number;
          workspaceId?: string;
          rationale?: string;
        };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const i = a.input;
      const d = await ctx.clients.agent.proposeEntityMerge(
        {
          dataset_id: i.datasetId,
          run_id: i.runId,
          candidate_id: i.candidateId,
          left_pk: i.leftPk,
          right_pk: i.rightPk,
          score: i.score,
          workspace_id: i.workspaceId,
          rationale: i.rationale,
        },
        a.idempotencyKey,
      );
      return mapEntityMergeProposal(d);
    },

    materializeResolvedEntities: async (
      _p: unknown,
      a: {
        runId: string;
        input: { name?: string; workspaceId?: string; attributes: Array<{ column: string; agg?: string }> };
        idempotencyKey?: string;
      },
      ctx: GraphQLContext,
    ) => {
      const d = await ctx.clients.dataset.materializeResolved(
        a.runId,
        {
          name: a.input.name,
          workspace_id: a.input.workspaceId,
          attributes: a.input.attributes.map((at) => ({ column: at.column, agg: at.agg })),
        },
        a.idempotencyKey,
      );
      return mapMaterializeResolved(d);
    },

    // ---- BRD 23: capability packs -------------------------------------------
    planPackInstall: async (
      _p: unknown, a: { pack: string; workspaceId: string; version?: string }, ctx: GraphQLContext,
    ) => mapPackInstallPlan(await ctx.clients.pack.plan(a.pack, a.workspaceId, a.version)),

    installPack: async (
      _p: unknown,
      a: { pack: string; workspaceId: string; version?: string; idempotencyKey?: string },
      ctx: GraphQLContext,
    ) => mapPackInstall(await ctx.clients.pack.install(a.pack, a.workspaceId, a.version, a.idempotencyKey)),

    uninstallPack: async (
      _p: unknown, a: { installId: string; idempotencyKey?: string }, ctx: GraphQLContext,
    ) => mapPackUninstall(await ctx.clients.pack.uninstall(a.installId, a.idempotencyKey)),

    completePackInstall: async (
      _p: unknown, a: { installId: string; idempotencyKey?: string }, ctx: GraphQLContext,
    ) => mapPackComplete(await ctx.clients.pack.complete(a.installId, a.idempotencyKey)),
  },

  // ------------------------------------------------------------ field resolvers
  Viewer: {
    // roles + capabilities are a pure passthrough of the caller's OWN rbac
    // projection (rbac GET /me/capabilities, JWT forwarded). No decision is made
    // here — this is display data for the UI gate. One request hits rbac at most
    // once (both fields share a memoized promise on the parent). On any downstream
    // failure we fail SAFE: return [] so the UI hides features rather than
    // over-exposing them (the services still enforce regardless).
    roles: (parent: ViewerParent, _a: unknown, ctx: GraphQLContext) =>
      viewerCaps(parent, ctx).then((c) => c.roles),
    capabilities: (parent: ViewerParent, _a: unknown, ctx: GraphQLContext) =>
      viewerCaps(parent, ctx).then((c) => c.capabilities),
    // Distinguishable degradation signal: true when the rbac call FAILED and the
    // empty roles/capabilities are the fail-closed fallback. The UI keeps the
    // fail-closed nav but shows a "permissions unavailable" notice instead of
    // presenting the outage as "you have no access".
    capsDegraded: (parent: ViewerParent, _a: unknown, ctx: GraphQLContext) =>
      viewerCaps(parent, ctx).then((c) => c.degraded),
    tenantName: (parent: ViewerParent, _a: unknown, ctx: GraphQLContext) =>
      viewerTenant(parent, ctx).then((t) => t.displayName || t.name),
    tenantDisplayName: (parent: ViewerParent, _a: unknown, ctx: GraphQLContext) =>
      viewerTenant(parent, ctx).then((t) => t.displayName),
    workspaceId: (parent: ViewerParent) => parent.workspaceId || null,
    workspaceName: (parent: ViewerParent, _a: unknown, ctx: GraphQLContext) =>
      viewerCaps(parent, ctx).then((c) => c.workspaceName || null),
    // Per-tenant UI label overrides (BRD 23 inc3) the app overlays onto its base
    // i18n catalog. Lazy + fail-safe ([] on any downstream error).
    displayLabels: (parent: ViewerParent, _a: unknown, ctx: GraphQLContext) =>
      viewerLabels(parent, ctx),
  },

  Tenant: {
    // Lazy: only fetched when a query actually asks for embedConfig, so
    // listing tenants doesn't pay for an extra identity-service round trip
    // per row. A 404 (never configured) maps to the "unconfigured" shape
    // rather than null — Tenant.embedConfig is always answerable.
    embedConfig: (parent: { id: string }, _a: unknown, ctx: GraphQLContext) =>
      nullOn404(ctx.clients.identity.embedConfig(parent.id)).then((d) =>
        d
          ? { __typename: "EmbedConfig" as const, configured: d.configured, allowedOrigins: d.allowed_origins, updatedAt: d.updated_at ?? null }
          : { __typename: "EmbedConfig" as const, configured: false, allowedOrigins: [], updatedAt: null },
      ),
  },

  Case: {
    assignee: (parent: { _assigneeId?: string | null }, _a: unknown, ctx: GraphQLContext) =>
      parent._assigneeId
        ? ctx.loaders.userById.load(parent._assigneeId).then((u) => (u ? mapUser(ctx, u) : null))
        : null,

    sourceDataset: (parent: { _datasetUrn?: string }, _a: unknown, ctx: GraphQLContext) => {
      const id = urnId(parent._datasetUrn);
      if (!id) return null;
      return ctx.loaders.datasetById.load(id).then((d) => (d ? mapDataset(ctx, d) : null));
    },

    proposals: (parent: { urn: string }, _a: unknown, ctx: GraphQLContext) =>
      ctx.loaders.proposalsByResourceUrn.load(parent.urn).then((ps) => ps.map((p) => mapProposal(ctx, p))),

    // Evidence attachments (task #77). Resolved on demand (detail path only) —
    // one case-service call per case; the UI requests it only on the detail page.
    // n1-safe: detail-path only; never resolved over a case list, so no fan-out.
    evidence: async (parent: { id: string }, _a: unknown, ctx: GraphQLContext) => {
      const rows = await ctx.clients.case.listEvidence(parent.id);
      return rows.map((e) => ({
        id: e.id,
        caseId: e.case_id ?? parent.id,
        filename: e.filename ?? "evidence",
        contentType: e.content_type ?? "application/octet-stream",
        sizeBytes: e.size_bytes ?? 0,
        uploadedBy: e.uploaded_by ?? null,
        createdAt: e.created_at ?? null,
      }));
    },
  },

  // ==== Tier 4b: case ops field resolvers =====================================
  CaseComment: {
    // Batched like Case.assignee: one identity call hydrates a page of authors.
    author: (parent: { _authorId?: string | null }, _a: unknown, ctx: GraphQLContext) =>
      parent._authorId
        ? ctx.loaders.userById.load(parent._authorId).then((u) => (u ? mapUser(ctx, u) : null))
        : null,
  },

  CaseActivity: {
    // Only human actors live in identity-service — agent/system actor ids are
    // NOT user ids, so hydrating them would be a guaranteed-miss loader call.
    actor: (
      parent: { actorType?: string | null; actorId?: string | null },
      _a: unknown,
      ctx: GraphQLContext,
    ) =>
      parent.actorType === "user" && parent.actorId
        ? ctx.loaders.userById.load(parent.actorId).then((u) => (u ? mapUser(ctx, u) : null))
        : null,
  },

  Dataset: {
    profile: (parent: { id: string }, _a: unknown, ctx: GraphQLContext) =>
      ctx.loaders.profileByDatasetId.load(parent.id).then((p) => (p ? mapProfile(p) : null)),
  },

  Dashboard: {
    charts: async (
      parent: { id: string; _dto?: any },
      args: { filters?: Array<{ field: string; op: string; value: unknown; origin?: string }> },
      ctx: GraphQLContext,
    ) => {
      // Chart metadata (name/type/config/sources) comes from a single list call;
      // GET /dashboards/{id} does not embed the child charts. One batched data
      // call then hydrates every chart's rows (AC-1) — one list + one batch, no N+1.
      // Only a 404 (dashboard vanished / tenant-masked) reads as "no charts";
      // anything else (403, 5xx, transport) SURFACES — a 403 must render as
      // PERMISSION_DENIED on the field, never as a silently empty dashboard.
      const defs = await ctx.clients.chart.dashboardCharts(parent.id).catch((e) => {
        if (e instanceof DownstreamError && e.httpStatus === 404) return [] as ChartDTO[];
        throw e;
      });
      // A failed batch-data call resolves to an error MARKER (never rejects, so
      // no unhandled rejection): each Chart.data then yields a proper per-chart
      // error entry instead of a silent permanent null.
      const dataPromise: Promise<ChartDataDTO[] | { batchError: unknown }> = ctx.clients.chart
        .dashboardData(parent.id, args.filters)
        .then((res) => normalizeBatch(res) as ChartDataDTO[] | { batchError: unknown })
        .catch((e) => ({ batchError: e as unknown }));
      return defs.map((c) => ({ ...mapChart(ctx, c), _dataPromise: dataPromise }));
    },
  },

  Chart: {
    data: async (
      parent: { id: string; _dataPromise?: Promise<ChartDataDTO[] | { batchError: unknown }> },
      _a: unknown,
      ctx: GraphQLContext,
    ) => {
      if (parent._dataPromise) {
        const all = await parent._dataPromise;
        // Batch failure marker: rethrow the downstream failure per chart so the
        // formatter maps it (403 -> PERMISSION_DENIED, outage -> SERVICE_UNAVAILABLE)
        // on this nullable field, per the chartDataResult per-chart error contract.
        if (!Array.isArray(all)) throw all.batchError;
        return chartDataResult(all.find((e) => e.chart_id === parent.id));
      }
      // Standalone chart (queried outside a dashboard): single data call.
      const d = await ctx.clients.chart.chartData(parent.id);
      return chartDataResult(shapedOf(d));
    },
  },

  AgentRun: {
    // n1-safe: AgentRun is only ever fetched singly (Query.agentRun / Case.lastAgentRun);
    // it never appears in a list field, and the trace tree has no batch endpoint.
    trace: (parent: { id: string }, _a: unknown, ctx: GraphQLContext) => ctx.clients.agent.runTrace(parent.id),
    tokenStream: (parent: { id: string }, _a: unknown, ctx: GraphQLContext) => ({
      hubUrl: ctx.config.realtimeHubUrl,
      // Real topic scheme: realtime-hub SchemeAgentRun is "agent_run:<run_id>"
      // (token/tool_call/proposal/run_completed events for one run).
      topics: [`agent_run:${parent.id}`],
    }),
  },

  Experiment: {
    // Batched: one /runs call hydrates the runs for a whole page of experiments
    // (runsByExperimentId loader) — not one call per experiment (BFF-FR-030).
    runs: async (parent: { id: string }, _a: unknown, ctx: GraphQLContext, _info: GraphQLResolveInfo) => {
      const rows = await ctx.loaders.runsByExperimentId.load(parent.id);
      const nodes = rows.map((d) => mapRun(ctx, d));
      return {
        nodes,
        edges: nodes.map((node) => ({ cursor: null, node })),
        pageInfo: { nextCursor: null, hasMore: false },
      };
    },
  },

  Run: {
    // Batched: one /models call hydrates every run's model across the list
    // (modelById loader) — not one call per run (BFF-FR-030).
    model: (parent: { _modelId?: string | null }, _a: unknown, ctx: GraphQLContext) =>
      parent._modelId
        ? ctx.loaders.modelById.load(parent._modelId).then((m) => (m ? mapModel(ctx, m) : null))
        : null,
  },

  // ==== Tier 2a: eval (eval-service) field resolvers =========================
  EvalRun: {
    // Per-request dataloader dedups repeat requests for the same run's cases.
    cases: (parent: { id: string }, _a: unknown, ctx: GraphQLContext) =>
      ctx.loaders.evalCasesByRunId.load(parent.id).then((rows) => rows.map(mapEvalCaseResult)),

    // The suite this run was pinned to — resolved from suitePins, not a separate
    // input, since eval-service has no "list suites" endpoint (only get-by-id).
    // Loaded through a per-request loader so a page of runs sharing a suite
    // costs one fetch, not one per run (N+1 guard); the loader returns null on
    // a missing/404 suite.
    suite: (
      parent: { _suitePins?: { suite_id?: string; suite_version?: number } },
      _a: unknown,
      ctx: GraphQLContext,
    ) => {
      const suiteId = parent._suitePins?.suite_id;
      if (!suiteId) return null;
      const key = `${suiteId}@${parent._suitePins?.suite_version ?? ""}`;
      return ctx.loaders.evalSuiteByKey.load(key).then((d) => (d ? mapEvalSuite(ctx, d) : null));
    },

    // The gate verdict for this run's candidate, found by the same agent+digest
    // +suite/dataset-pin match eval-service's own CI idempotency lookup uses
    // (app/api/routes/ci.py ci_evaluate) — there is no run->gate foreign key.
    gate: async (
      parent: {
        _agentKey?: string;
        _contentDigest?: string | null;
        _suitePins?: { suite_id?: string; suite_version?: number; datasets?: { version?: number }[] };
      },
      _a: unknown,
      ctx: GraphQLContext,
    ) => {
      if (!parent._agentKey || !parent._contentDigest) return null;
      // Loader dedups the gate lookup across runs sharing an agent+digest.
      const gates = await ctx.loaders.evalGatesByKey.load(
        `${parent._agentKey}::${parent._contentDigest}`,
      );
      const suiteId = parent._suitePins?.suite_id;
      const suiteVersion = parent._suitePins?.suite_version;
      const datasetVersion = parent._suitePins?.datasets?.[0]?.version;
      const match =
        gates.find(
          (g) =>
            g.suite_id === suiteId &&
            g.suite_version === suiteVersion &&
            (datasetVersion === undefined || g.dataset_version === datasetVersion),
        ) ?? null;
      return match ? mapEvalGateResult(ctx, match) : null;
    },
  },
};

// Re-exported so the formatter can special-case downstream failures.
export { DownstreamError, ErrorCode };
