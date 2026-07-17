/** DTO -> GraphQL mappers + URN/enum helpers. Pure reshaping only (no logic). */
import type { GraphQLContext } from "../context.js";
import type {
  DatasetDTO, ProfileDTO, LineageDTO, DatasetVersionDTO, ProfileColumnDTO,
  DatasetConsumersDTO, SimilarDatasetDTO, ReprofileDTO,
} from "../clients/dataset.js";
import type {
  SavedQueryDTO, ExecutionDTO, ResultsDTO, SavedQueryVersionDTO, QueryStatDTO,
} from "../clients/query.js";
import type {
  CaseDTO,
  // Tier 4b: case ops (lifecycle, comments/timeline, export, catalog, SLA).
  CaseCommentDTO, CaseActivityDTO, CaseOperationDTO, DispositionDTO, CaseFieldDTO, CaseSlaPolicyDTO,
} from "../clients/case.js";
import type { DashboardDTO, ChartDTO, ChartTypeDTO, ChartShapedDataDTO } from "../clients/chart.js";
import type {
  ReportSubscriptionDTO,
  // Tier 2b: notification-service (inbox/preferences/rules/webhooks/templates/ops).
  NotificationDTO, NotificationPreferencesDTO, NotificationRuleDTO,
  WebhookEndpointDTO, WebhookDeliveryDTO, NotificationTemplateDTO, SuppressionDTO,
} from "../clients/notification.js";
import type {
  ProposalDTO, AgentRunDTO, AgentKillSwitchDTO,
  // Tier 2b: agent-runtime catalog/registry.
  AgentDefinitionDTO, AgentVersionDTO, TenantAgentConfigDTO, AgentRunListItemDTO,
} from "../clients/agent.js";
import type {
  ToolKillSwitchDTO,
  // Tier 2b: tool-plane registry admin.
  ToolDTO, ToolVersionDTO, ToolHealthDTO, TenantToolSettingsDTO, BYOSubmissionDTO,
} from "../clients/toolplane.js";
import type { MemoryRecordDTO, ErasureRequestDTO } from "../clients/memory.js";
import type {
  ExperimentDTO, RunDTO, ModelDTO, MetricsDTO, RegistryModelDTO, ModelVersionDTO, PromotionDTO,
  // Tier 4b: ml ops (register/notes/artifacts).
  RegisterRunResultDTO, RunNoteDTO, RunArtifactDTO,
} from "../clients/experiment.js";
import type {
  InferenceJobDTO,
  // Tier 4b: ml ops (validate + schedules).
  CompatibilityReportDTO, InferenceScheduleDTO,
} from "../clients/inference.js";
import type {
  UserDTO, ServiceAccountDTO, TenantDTO,
  // Tier 4b: identity/rbac admin (service-account create/rotate carries api_key once).
  CreatedServiceAccountDTO,
} from "../clients/identity.js";
import type {
  WorkspaceDTO, GroupDTO, MemberDTO, RoleDTO, ExplainAuthzDTO,
  // Tier 4b: identity/rbac admin (content grants + bulk membership).
  ContentGrantDTO, EffectiveAccessEntryDTO, BulkMembersResponseDTO,
} from "../clients/rbac.js";
import type { AuditEventDTO, ChainVerifyResultDTO, ComplianceJobDTO, OperationDTO } from "../clients/audit.js";
import { budgetScopeString, type BudgetDTO, type RateCardDTO, type AnomalyDTO } from "../clients/usage.js";
import type {
  ConnectorTypeDTO, ConnectionDTO, ConnectionTestDTO, IngestionDTO, UploadDTO,
  ScheduleDTO, ScheduleFireDTO, ConnectionPreviewDTO, WritebackDTO,
} from "../clients/ingestion.js";
import type {
  ComponentDTO, AlgorithmDTO, TemplateDTO, PipelineRunDTO, ValidationReportDTO, StepParamDTO,
  TemplateVersionDTO, CompiledManifestDTO, RunManifestDTO, PipelineScheduleDTO,
} from "../clients/pipelines.js";
import type {
  SemanticModelDTO, SemanticDefinitionDTO, SemanticDimensionDTO, SemanticMeasureDTO,
  SemanticVersionDTO, CompileResultDTO, VerifiedQueryDTO, VerifiedQuerySearchHitDTO,
  SemanticOperationDTO,
} from "../clients/semantic.js";
import type {
  EvalSuiteDTO, EvalRunDTO, EvalCaseResultDTO, EvalDatasetDTO, EvalCaseDTO, EvalScorerDTO,
  EvalGateResultDTO, EvalCanaryDTO, EvalTrendPointDTO, EvalSloRowDTO,
} from "../clients/eval.js";
import type {
  AiProviderDTO, AiLadderDTO, AiBudgetDTO, AiSpendRowDTO, AiVirtualKeyDTO, AiGuardrailPolicyDTO,
  AiCostBreakdownDTO, AiCostRollupDTO,
} from "../clients/aigateway.js";

/** Build a resource URN (MASTER-FR-013) from the caller's tenant claim. */
export function urn(ctx: GraphQLContext, service: string, type: string, id: string): string {
  const tenant = ctx.identity.claims.tenant_id ?? "unknown";
  return `wr:${tenant}:${service}:${type}/${id}`;
}

/** Extract the trailing resource id from a URN (wr:t:svc:type/<id>). */
export function urnId(u: string | undefined | null): string | undefined {
  if (!u) return undefined;
  const slash = u.lastIndexOf("/");
  return slash >= 0 ? u.slice(slash + 1) : undefined;
}

const up = (s?: string | null): string | null => (s ? s.toUpperCase() : null);

// --- mapped node shapes carry the original ids field resolvers need.
export interface MappedCase {
  __typename: "Case";
  id: string;
  urn: string;
  caseNumber?: number;
  title: string | null;
  status: string | null;
  severity: string | null;
  dueDate?: string;
  createdAt?: string;
  // Pack/dataset-provided evidence summary; present on BOTH the search
  // projection and caseView ('note' carries the investigator briefing).
  displayProjection: Record<string, string> | null;
  // Tier 4b: lifecycle/resolution detail fields (caseView only — the search
  // projection omits them, so they are null on list rows).
  description: string | null;
  dispositionId: string | null;
  resolutionNote: string | null;
  resolvedAt: string | null;
  closedAt: string | null;
  caseVersion: number | null;
  reassignCount: number | null;
  _assigneeId?: string | null;
  _datasetUrn?: string;
}

export function mapUser(ctx: GraphQLContext, d: UserDTO) {
  return {
    __typename: "User" as const,
    id: d.id,
    urn: urn(ctx, "identity", "user", d.id),
    email: d.email,
    fullName: d.full_name ?? null,
    status: d.status ?? null,
    lastLoginAt: d.last_login_at ?? null,
    createdAt: d.created_at ?? null,
  };
}

// --- admin: rbac workspaces + groups (pure reshaping, no logic) --------------
export function mapWorkspace(ctx: GraphQLContext, d: WorkspaceDTO) {
  return {
    __typename: "Workspace" as const,
    id: d.id,
    urn: urn(ctx, "rbac", "workspace", d.id),
    name: d.name,
    description: d.description ?? null,
    public: d.public ?? false,
    // rbac has no status field: presence of archived_at IS the archived state.
    archived: d.archived_at != null,
    archivedAt: d.archived_at ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapGroup(ctx: GraphQLContext, d: GroupDTO) {
  return {
    __typename: "Group" as const,
    id: d.id,
    urn: urn(ctx, "rbac", "group", d.id),
    name: d.name,
    description: d.description ?? null,
    // rbac serializes the type under `group_type`.
    groupType: d.group_type ?? null,
    system: d.system ?? false,
    autoGenerated: d.auto_generated ?? false,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapGroupMember(d: MemberDTO) {
  return {
    userId: d.user_id,
    expiresAt: d.expires_at ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapRole(d: RoleDTO) {
  return {
    __typename: "Role" as const,
    id: d.id,
    name: d.name,
    system: d.system ?? false,
    version: d.version ?? null,
    actions: d.actions ?? [],
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapAuthzExplanation(d: ExplainAuthzDTO) {
  return {
    __typename: "AuthzExplanation" as const,
    allowed: d.allowed,
    reason: d.reason,
    chain: (d.chain ?? []).map((s) => ({
      __typename: "AuthzChainStep" as const,
      type: s.type,
      group: s.group ?? null,
      groupType: s.group_type ?? null,
      role: s.role ?? null,
      action: s.action ?? null,
      workspaceScoped: s.workspace_scoped ?? null,
      viaGroup: s.via_group ?? null,
      workspace: s.workspace ?? null,
      level: s.level ?? null,
      subject: s.subject ?? null,
      admin: s.admin ?? null,
      detail: s.detail ?? null,
    })),
  };
}

// --- admin: identity service accounts + tenant ------------------------------
export function mapServiceAccount(ctx: GraphQLContext, d: ServiceAccountDTO) {
  return {
    __typename: "ServiceAccount" as const,
    id: d.id,
    urn: urn(ctx, "identity", "service_account", d.id),
    name: d.name,
    scopes: d.scopes ?? [],
    expiresAt: d.expires_at ?? null,
    lastUsedAt: d.last_used_at ?? null,
    revokedAt: d.revoked_at ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapTenant(ctx: GraphQLContext, d: TenantDTO) {
  return {
    __typename: "Tenant" as const,
    id: d.id,
    urn: urn(ctx, "identity", "tenant", d.id),
    name: d.name,
    displayName: d.display_name ?? null,
    ownerEmail: d.owner_email ?? null,
    tier: d.tier ?? null,
    cloud: d.cloud ?? null,
    status: d.status ?? null,
    subdomain: d.subdomain ?? null,
    platformVersion: d.platform_version ?? null,
    autoUpgrade: d.auto_upgrade ?? null,
    modules: d.modules ?? [],
    quotas: d.quotas
      ? {
          cpu: d.quotas.cpu ?? null,
          memory: d.quotas.memory ?? null,
          processingCpu: d.quotas.processing_cpu ?? null,
          processingMemory: d.quotas.processing_memory ?? null,
        }
      : null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

// --- admin: audit trail -----------------------------------------------------
/** Flatten the audit eventDTO (nested actor / via_agent objects) into the flat
 * UI shape. `payload` is null when the body was withheld. */
export function mapAuditEvent(ctx: GraphQLContext, d: AuditEventDTO) {
  return {
    __typename: "AuditEvent" as const,
    eventId: d.event_id,
    urn: urn(ctx, "audit", "event", d.event_id),
    eventType: d.event_type,
    tenantId: d.tenant_id ?? null,
    actorType: d.actor?.type ?? null,
    actorId: d.actor?.id ?? null,
    viaAgentId: d.via_agent?.agent_id ?? null,
    viaAgentVersion: d.via_agent?.version ?? null,
    action: d.action ?? null,
    resourceUrn: d.resource_urn ?? null,
    occurredAt: d.occurred_at,
    ingestedAt: d.ingested_at ?? null,
    traceId: d.trace_id ?? null,
    payloadDigest: d.payload_digest ?? null,
    bodyWithheld: d.body_withheld ?? false,
    payload: d.payload ?? null,
    chainSeq: d.chain_seq ?? null,
    chainHash: d.chain_hash ?? null,
  };
}

export function mapChainVerifyResult(d: ChainVerifyResultDTO) {
  return {
    __typename: "ChainVerifyResult" as const,
    valid: d.valid,
    eventsChecked: d.events_checked,
    chainHead: d.chain_head,
    manifestMatch: d.manifest_match,
    firstMismatchSeq: d.first_mismatch_seq ?? null,
    sealed: d.sealed,
  };
}

export function mapComplianceJob(d: ComplianceJobDTO | OperationDTO) {
  return {
    __typename: "ComplianceJob" as const,
    operationId: d.operation_id,
    status: d.status,
    resultUrl: "result_url" in d ? d.result_url ?? null : null,
    error: "error" in d ? d.error ?? null : null,
  };
}

export function mapDataset(ctx: GraphQLContext, d: DatasetDTO) {
  return {
    __typename: "Dataset" as const,
    id: d.id,
    urn: urn(ctx, "dataset", "dataset", d.id),
    name: d.name,
    description: d.description ?? null,
    status: d.status ?? d.lifecycle ?? null,
    tags: d.tags ?? [],
    // dataset_payload nests the row count under current_version on the detail
    // path (the list path serializes no current_version -> null).
    rowCount: d.row_count ?? d.current_version?.row_count ?? null,
    createdAt: d.created_at ?? null,
    // dataset-service has no status="archived" value: presence of deleted_at IS
    // the archive marker (same convention as rbac Workspace.archived_at).
    archived: d.deleted_at != null,
    archivedAt: d.deleted_at ?? null,
  };
}

export function mapProfile(d: ProfileDTO) {
  return {
    // get_summary nests the counts under `table` (dataset-service services.py).
    rowCount: d.row_count ?? d.table?.row_count ?? null,
    columnCount: d.column_count ?? d.table?.column_count ?? null,
    fullJsonUrl: d.full_json_url ?? null,
    htmlReportUrl: d.html_report_url ?? null,
  };
}

export function mapLineage(d: LineageDTO) {
  return {
    nodes: (d.nodes ?? []).map((n) => ({
      urn: n.urn,
      kind: n.kind ?? null,
      name: n.name ?? null,
      status: n.status ?? null,
    })),
    edges: (d.edges ?? []).map((e) => ({
      fromUrn: e.from_urn,
      toUrn: e.to_urn,
      activity: e.activity ?? null,
      occurredAt: e.occurred_at ?? null,
    })),
    truncated: d.truncated ?? false,
  };
}

export function mapDatasetConsumers(d: DatasetConsumersDTO) {
  return {
    downstreamEdges: d.downstream_edges ?? 0,
    byService: d.by_service ?? {},
    byActivity: d.by_activity ?? {},
    truncated: d.truncated ?? false,
  };
}

export function mapSimilarDataset(d: SimilarDatasetDTO) {
  return {
    id: d.id ?? d.dataset_id ?? null,
    urn: d.urn ?? null,
    name: d.name ?? null,
    score: typeof d.score === "number" ? d.score : null,
  };
}

export function mapDatasetVersion(d: DatasetVersionDTO) {
  return {
    __typename: "DatasetVersion" as const,
    id: d.id,
    urn: d.urn ?? null,
    versionNo: d.version_no,
    icebergSnapshotId: d.iceberg_snapshot_id != null ? String(d.iceberg_snapshot_id) : null,
    schema: d.schema ?? null,
    schemaDiff: d.schema_diff ?? null,
    breakingChange: d.breaking_change ?? null,
    rowCount: d.row_count ?? null,
    bytes: d.bytes ?? null,
    producedByUrn: d.produced_by_urn ?? null,
    profileStatus: d.profile_status ?? null,
    expired: d.expired ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapReprofile(d: ReprofileDTO) {
  return {
    operationId: d.operation_id ?? null,
    profileId: d.profile_id ?? null,
    status: d.status ?? null,
  };
}

export function mapIngestion(ctx: GraphQLContext, d: IngestionDTO) {
  return {
    __typename: "Ingestion" as const,
    id: d.id,
    urn: urn(ctx, "ingestion", "ingestion", d.id),
    mode: d.ingestion_mode,
    status: d.status,
    trigger: d.trigger ?? null,
    connectionId: d.connection_id ?? null,
    datasetUrn: d.dataset_urn ?? null,
    fileFormat: d.file_format ?? null,
    statement: d.statement ?? null,
    rowsAppended: d.rows_appended ?? null,
    bytesReceived: d.bytes_received ?? null,
    bytesTotal: d.bytes_total ?? null,
    attempts: d.attempts ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapUpload(d: UploadDTO) {
  return {
    __typename: "Upload" as const,
    uploadId: d.upload_id,
    ingestionId: d.ingestion_id ?? null,
    status: d.status ?? null,
    partSize: d.part_size ?? null,
    bytesTotal: d.bytes_total ?? null,
    sha256: d.sha256 ?? null,
    expiresAt: d.expires_at ?? null,
    parts: (d.parts ?? []).map((p) => ({ n: p.n, etag: p.etag, size: p.size })),
  };
}

export function mapSavedQuery(ctx: GraphQLContext, d: SavedQueryDTO) {
  return {
    __typename: "SavedQuery" as const,
    id: d.id,
    urn: urn(ctx, "query", "query", d.id),
    name: d.name,
    description: d.description ?? null,
    tags: d.tags ?? [],
    moduleNames: d.module_names ?? [],
    // sqlText/variables/version_no hydrate on the single-resource path only.
    sqlText: d.sql_text ?? null,
    variables: d.variables ?? null,
    versionNo: d.version_no ?? d.current_version_no ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

/** Normalize a results column (bare string OR {name,type}) into QueryColumn. */
function mapColumn(c: string | { name?: string; type?: string }) {
  if (typeof c === "string") return { name: c, type: null };
  return { name: c.name ?? "", type: c.type ?? null };
}

/** Combine a terminal execution + its first results page into one QueryResult. */
export function mapQueryResult(exec: ExecutionDTO, results: ResultsDTO | null) {
  const stats = results?.stats ?? exec.stats ?? {};
  return {
    executionId: exec.execution_id ?? exec.id ?? "",
    status: exec.status ?? "unknown",
    engine: exec.engine ?? (stats as { engine?: string }).engine ?? null,
    cacheHit: exec.cache_hit ?? (stats as { cache_hit?: boolean }).cache_hit ?? null,
    durationMs: (stats as { duration_ms?: number }).duration_ms ?? null,
    resultRows: (stats as { result_rows?: number }).result_rows ?? null,
    scanBytes: (stats as { actual_scan_bytes?: number }).actual_scan_bytes ?? null,
    columns: (results?.columns ?? []).map(mapColumn),
    rows: results?.rows ?? [],
    hasMore: results?.page?.has_more ?? false,
    warnings: results?.warnings ?? exec.warnings ?? null,
    error: exec.error ?? null,
  };
}

export function mapSavedQueryVersion(d: SavedQueryVersionDTO) {
  return {
    __typename: "SavedQueryVersion" as const,
    id: d.id,
    versionNo: d.version_no,
    sqlText: d.sql_text ?? null,
    variables: d.variables ?? null,
    datasetRefs: d.dataset_refs ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
  };
}

/** An execution HISTORY row (no result rows — distinct from mapQueryResult). */
export function mapQueryExecution(ctx: GraphQLContext, d: ExecutionDTO) {
  const id = d.execution_id ?? d.id ?? "";
  const stats = d.stats ?? {};
  return {
    __typename: "QueryExecution" as const,
    id,
    urn: urn(ctx, "query", "execution", id),
    status: d.status ?? "unknown",
    engine: d.engine ?? null,
    cacheHit: d.cache_hit ?? null,
    savedQueryId: d.saved_query_id ?? null,
    queryVersionNo: d.query_version_no ?? null,
    sqlText: (d as { sql_text?: string }).sql_text ?? null,
    createdBy: (d as { created_by?: string }).created_by ?? null,
    createdAt: d.created_at ?? null,
    startedAt: d.started_at ?? null,
    finishedAt: d.finished_at ?? null,
    durationMs: stats.duration_ms ?? null,
    resultRows: stats.result_rows ?? null,
    scanBytes: stats.actual_scan_bytes ?? null,
    queuePosition: (d as { queue_position?: number }).queue_position ?? null,
    error: d.error ?? null,
  };
}

export function mapQueryStats(d: { since?: string; top_queries?: QueryStatDTO[] }) {
  return {
    since: d.since ?? null,
    topQueries: (d.top_queries ?? []).map((s) => ({
      sqlFingerprint: s.sql_fingerprint,
      executions: s.executions ?? 0,
      totalScanBytes: s.total_scan_bytes ?? 0,
      failures: s.failures ?? 0,
      topUser: s.top_user ?? null,
    })),
  };
}

export function mapCase(ctx: GraphQLContext, d: CaseDTO): MappedCase {
  return {
    __typename: "Case",
    id: d.id,
    urn: urn(ctx, "case", "case", d.id),
    caseNumber: d.case_number,
    title: d.title ?? (d.case_number != null ? `Case #${d.case_number}` : null),
    status: up(d.status),
    severity: up(d.severity),
    dueDate: d.due_date,
    createdAt: d.created_at,
    displayProjection:
      d.display_projection && Object.keys(d.display_projection).length > 0
        ? d.display_projection
        : null,
    description: d.description ?? null,
    dispositionId: d.disposition_id ?? null,
    resolutionNote: d.resolution_note ?? null,
    resolvedAt: d.resolved_at ?? null,
    closedAt: d.closed_at ?? null,
    caseVersion: d.case_version ?? null,
    reassignCount: d.reassign_count ?? null,
    // case-service's CRUD view serializes assigned_to_id while the search
    // projection emits assignee_id (being aligned) — accept BOTH during the
    // transition so Case.assignee hydrates from either surface.
    _assigneeId: d.assigned_to_id ?? d.assignee_id ?? null,
    _datasetUrn: d.dataset_urn,
  };
}

// ==== Tier 4b: case ops (case-service) =======================================

export function mapCaseComment(_ctx: GraphQLContext, d: CaseCommentDTO) {
  return {
    __typename: "CaseComment" as const,
    id: d.id,
    caseId: d.case_id ?? null,
    authorId: d.author_id ?? null,
    body: d.body ?? null,
    editedAt: d.edited_at ?? null,
    createdAt: d.created_at ?? null,
    // CaseComment.author hydrates via the userById loader from this id.
    _authorId: d.author_id ?? null,
  };
}

export function mapCaseActivity(_ctx: GraphQLContext, d: CaseActivityDTO) {
  return {
    __typename: "CaseActivity" as const,
    id: d.id,
    caseId: d.case_id ?? null,
    eventType: d.event_type ?? null,
    actorType: d.actor_type ?? null,
    actorId: d.actor_id ?? null,
    viaAgent: d.via_agent ?? null,
    proposalUrn: d.proposal_urn ?? null,
    oldValue: d.old_value ?? null,
    newValue: d.new_value ?? null,
    occurredAt: d.occurred_at ?? null,
  };
}

export function mapCaseOperation(d: CaseOperationDTO) {
  const result = d.result ?? {};
  return {
    __typename: "CaseOperation" as const,
    id: d.id,
    kind: d.kind ?? null,
    status: d.status ?? null,
    succeeded: d.succeeded ?? null,
    failed: d.failed ?? null,
    total: d.total ?? null,
    rowCount: result.row_count ?? null,
    downloadUrl: result.download_url ?? null,
    expiresAt: result.expires_at ?? null,
    error: result.error ?? null,
  };
}

export function mapDisposition(ctx: GraphQLContext, d: DispositionDTO) {
  return {
    __typename: "Disposition" as const,
    id: d.id,
    urn: urn(ctx, "case", "disposition", d.id),
    workspaceId: d.workspace_id ?? null,
    code: d.code ?? null,
    label: d.label ?? null,
    category: d.category ?? null,
    requiresNote: d.requires_note ?? false,
    active: d.active ?? false,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

/** case-service serializes CaseField.purpose as int16 (0=create, 1=update,
 * 2=both) although the CREATE request takes the string — map it back so the
 * GraphQL surface speaks one language. */
function purposeString(p?: number): string | null {
  switch (p) {
    case 0: return "create";
    case 1: return "update";
    case 2: return "both";
    default: return null;
  }
}

export function mapCaseField(ctx: GraphQLContext, d: CaseFieldDTO) {
  return {
    __typename: "CaseField" as const,
    id: d.id,
    urn: urn(ctx, "case", "case_field", d.id),
    workspaceId: d.workspace_id ?? null,
    queryUrn: d.query_urn ?? null,
    name: d.name ?? null,
    dataType: d.data_type ?? null,
    purpose: purposeString(d.purpose),
    fieldMeta: d.field_meta ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapCaseSlaPolicy(d: CaseSlaPolicyDTO) {
  return {
    __typename: "CaseSlaPolicy" as const,
    workspaceId: d.workspace_id ?? null,
    warnBeforeSeconds: d.warn_before_seconds ?? null,
    onBreach: d.on_breach ?? null,
    maxReassignCount: d.max_reassign_count ?? null,
  };
}

export function mapDashboard(ctx: GraphQLContext, d: DashboardDTO) {
  return {
    __typename: "Dashboard" as const,
    id: d.id,
    urn: urn(ctx, "chart", "dashboard", d.id),
    // chart-service serializes the dashboard label as `name`; keep the legacy
    // `title` read path as a fallback so both shapes resolve a non-null title.
    title: d.title ?? d.name ?? "",
    module: d.module ?? null,
    archived: d.archived ?? false,
    _dto: d,
  };
}

export function mapReportSubscription(ctx: GraphQLContext, d: ReportSubscriptionDTO) {
  return {
    __typename: "ReportSubscription" as const,
    id: d.id,
    urn: urn(ctx, "notification", "report_subscription", d.id),
    dashboardId: d.dashboard_id,
    workspaceId: d.workspace_id,
    name: d.name,
    recipients: d.recipients,
    cadence: d.cadence,
    sendHour: d.send_hour,
    sendWeekday: d.send_weekday ?? null,
    timezone: d.timezone,
    format: d.format,
    enabled: d.enabled,
    lastSentAt: d.last_sent_at ?? null,
    lastStatus: d.last_status ?? null,
    lastError: d.last_error ?? null,
    createdBy: d.created_by,
    createdAt: d.created_at,
    updatedAt: d.updated_at,
  };
}

export function mapChart(ctx: GraphQLContext, d: ChartDTO) {
  return {
    __typename: "Chart" as const,
    id: d.id,
    urn: urn(ctx, "chart", "chart", d.id),
    name: d.name ?? null,
    chartType: d.chart_type ?? null,
    spec: d.spec ?? null,
    provenance: d.provenance ?? null,
    // Authoring round-trip: the editor reads back what it wrote so it can
    // rehydrate the form. Present on the authoring/get views (chartView);
    // absent (null) on the read-only batch path.
    config: d.config ?? null,
    displayMeta: d.display_meta ?? null,
    sources: d.sources ?? null,
  };
}

/** Map a GET /chart-types catalog entry into the UI ChartType (snake→camel). */
export function mapChartType(d: ChartTypeDTO) {
  return {
    name: d.name,
    family: d.family,
    dataClass: d.data_class ?? null,
    requiredFields: d.required_fields ?? [],
    configSchema: d.config_schema ?? null,
  };
}

/** Map a POST /charts/preview ShapedResult into the editor's live-preview shape. */
export function mapChartShapedData(d: ChartShapedDataDTO) {
  return {
    chartId: d.chart_id ?? null,
    chartType: d.chart_type ?? null,
    columns: d.columns ?? null,
    rows: d.rows ?? null,
    graph: d.graph ?? null,
    artifact: d.artifact ?? null,
    rowCount: d.row_count ?? null,
    truncated: d.truncated ?? null,
  };
}

/**
 * Surface the tool-plane risk tier so the UI can fail closed on bulk-approve.
 * PURE PASSTHROUGH of the downstream `tier` (read|write-proposal|write-direct|
 * admin) — no authz/business decision is made here. When the payload carries no
 * explicit tier we fall back to its side-effect class, and a wholly missing
 * classification maps to the "unknown" sentinel (the UI treats unknown as high).
 */
export function riskTierOf(d: ProposalDTO): string {
  if (d.tier) return String(d.tier);
  if (d.risk_tier) return String(d.risk_tier);
  if (d.side_effects === "destructive") return "write-direct";
  return "unknown";
}

export function mapProposal(ctx: GraphQLContext, d: ProposalDTO) {
  return {
    __typename: "Proposal" as const,
    id: d.id,
    urn: urn(ctx, "agent", "proposal", d.id),
    agentKey: d.agent_key ?? null,
    // proposal_view serializes the proposed call as tool_id + args; the legacy
    // tool/args_diff names are accepted for defensiveness during transition.
    tool: d.tool_id ?? d.tool ?? null,
    argsDiff: d.args ?? d.args_diff ?? null,
    rationale: d.rationale ?? null,
    affectedUrns: d.affected_urns ?? [],
    predictedEffect: d.predicted_effect ?? null,
    status: up(d.status),
    decision: d.decision ?? null,
    createdAt: d.created_at ?? null,
    riskTier: riskTierOf(d),
  };
}

export function mapAgentRun(ctx: GraphQLContext, d: AgentRunDTO) {
  return {
    __typename: "AgentRun" as const,
    id: d.id,
    urn: urn(ctx, "agent", "run", d.id),
    agentKey: d.agent_key ?? null,
    status: up(d.status),
    // run_view carries no top-level cost today; read it (or usage.cost*) if present.
    costUsd: d.cost_usd ?? d.usage?.cost_usd ?? d.usage?.cost ?? null,
    // run_view nests token counts under `usage`; legacy `token_usage` accepted.
    tokenUsage: {
      inputTokens: d.usage?.input_tokens ?? d.token_usage?.input_tokens ?? null,
      outputTokens: d.usage?.output_tokens ?? d.token_usage?.output_tokens ?? null,
    },
  };
}

export function mapAgentKillSwitch(_ctx: GraphQLContext, d: AgentKillSwitchDTO) {
  return {
    __typename: "KillSwitch" as const,
    id: d.kill_id,
    target: "AGENT" as const,
    scope: d.scope,
    agentKey: d.agent_key ?? null,
    toolId: null,
    version: d.version != null ? String(d.version) : null,
    tenantId: d.tenant_id ?? null,
    active: d.active,
    reason: d.reason,
    setBy: d.set_by,
    createdAt: d.created_at ?? null,
  };
}

export function mapToolKillSwitch(_ctx: GraphQLContext, d: ToolKillSwitchDTO) {
  return {
    __typename: "KillSwitch" as const,
    id: d.id,
    target: "TOOL" as const,
    scope: d.scope,
    agentKey: null,
    toolId: d.tool_id ?? null,
    version: d.version ?? null,
    tenantId: d.tenant_id ?? null,
    active: d.active,
    reason: d.reason,
    setBy: d.set_by,
    createdAt: d.created_at ?? null,
  };
}

export function mapMemoryRecord(ctx: GraphQLContext, d: MemoryRecordDTO) {
  return {
    __typename: "MemoryRecord" as const,
    id: d.memory_id,
    urn: urn(ctx, "memory", "record", d.memory_id),
    scope: d.scope,
    scopeRef: d.scope_ref,
    content: d.content,
    confidence: d.confidence ?? null,
    status: d.status,
    tags: d.tags ?? [],
    provenance: d.provenance ?? null,
    retrievalCount: d.retrieval_count ?? null,
    classifierScore: d.classifier_score ?? null,
    ttlExpiresAt: d.ttl_expires_at ?? null,
    mergedFrom: d.merged_from ?? null,
    revalidateAt: d.revalidate_at ?? null,
  };
}

export function mapErasureRequest(_ctx: GraphQLContext, d: ErasureRequestDTO) {
  return {
    __typename: "ErasureRequest" as const,
    operationId: d.operation_id,
    status: d.status,
    report: d.report ?? null,
    completedAt: d.completed_at ?? null,
  };
}

export function mapExperiment(ctx: GraphQLContext, d: ExperimentDTO) {
  return {
    __typename: "Experiment" as const,
    id: d.id,
    urn: urn(ctx, "experiment", "experiment", d.id),
    name: d.name ?? "",
    description: d.description ?? null,
    archived: d.archived ?? false,
  };
}

/** Reduce a run's metrics payload to {name: value} floats. The detail path
 * serializes {name: {value, step, logged_at}} (last point per key); plain
 * {name: number} and the legacy [{key, value}] list are accepted too. */
function flattenRunMetrics(m: MetricsDTO | null | undefined): Record<string, number> | null {
  if (!m) return null;
  const out: Record<string, number> = {};
  if (Array.isArray(m)) {
    for (const e of m) {
      if (e && typeof e.key === "string" && typeof e.value === "number") out[e.key] = e.value;
    }
  } else {
    for (const [k, v] of Object.entries(m)) {
      if (typeof v === "number") out[k] = v;
      else if (v && typeof v === "object" && typeof v.value === "number") out[k] = v.value;
    }
  }
  return out;
}

/** Params pass through as-is ({name: value}); the legacy [{key, value}] list
 * shape is normalized to a record so the UI's Object.entries render works. */
function normalizeRunParams(p: RunDTO["params"]): Record<string, string> | null {
  if (!p) return null;
  if (Array.isArray(p)) {
    const out: Record<string, string> = {};
    for (const e of p) if (e && typeof e.key === "string") out[e.key] = e.value;
    return out;
  }
  return p;
}

// experiment-service run status (scheduled/running/finished/failed/killed —
// MLflow-derived) → the bff RunStatus enum (QUEUED/RUNNING/SUCCEEDED/FAILED/
// CANCELLED). A naive uppercase yields FINISHED/KILLED/SCHEDULED, which the enum
// can't represent and errors the whole field.
const RUN_STATUS: Record<string, string> = {
  scheduled: "QUEUED",
  queued: "QUEUED",
  running: "RUNNING",
  finished: "SUCCEEDED",
  succeeded: "SUCCEEDED",
  completed: "SUCCEEDED",
  failed: "FAILED",
  killed: "CANCELLED",
  cancelled: "CANCELLED",
};
function mapRunStatus(s?: string | null): string | null {
  if (!s) return null;
  return RUN_STATUS[s.toLowerCase()] ?? "RUNNING";
}

export function mapRun(ctx: GraphQLContext, d: RunDTO) {
  return {
    __typename: "Run" as const,
    id: d.id,
    urn: urn(ctx, "experiment", "run", d.id),
    name: d.name ?? null,
    status: mapRunStatus(d.status),
    metrics: flattenRunMetrics(d.metrics),
    params: normalizeRunParams(d.params),
    // Tier 4b: ml ops — register-as-model needs the owning experiment id.
    experimentId: d.experiment_id ?? null,
    _modelId: d.model_id ?? null,
  };
}

export function mapModel(ctx: GraphQLContext, d: ModelDTO) {
  return {
    __typename: "RegisteredModel" as const,
    id: d.id,
    urn: urn(ctx, "experiment", "model", d.id),
    name: d.name ?? null,
    stage: d.stage ?? null,
  };
}

/** A registered model header (experiment-service _model_payload). `versions` are
 * attached separately (empty on the list path, hydrated on the detail path). */
export function mapRegistryModel(
  ctx: GraphQLContext,
  d: RegistryModelDTO,
  versions: ModelVersionDTO[] = [],
) {
  return {
    __typename: "Model" as const,
    // The backend serializes its own urn; prefer it, else build from tenant+id.
    id: d.id,
    urn: d.urn ?? urn(ctx, "experiment", "model", d.id),
    name: d.name ?? null,
    modelType: d.model_type ?? null,
    ownerId: d.owner_id ?? null,
    description: d.description ?? null,
    createdAt: d.created_at ?? null,
    versions: versions.map((v) => mapModelVersion(ctx, v)),
  };
}

export function mapPromotion(ctx: GraphQLContext, d: PromotionDTO) {
  return {
    __typename: "Promotion" as const,
    id: d.id,
    urn: d.urn ?? urn(ctx, "experiment", "promotion", d.id),
    modelVersionId: d.model_version_id ?? null,
    targetStage: d.target_stage ?? null,
    fromStage: d.from_stage ?? null,
    status: d.status ?? null,
    rationale: d.rationale ?? null,
    requestedBy: d.requested_by ?? null,
    viaAgent: d.via_agent ?? null,
    decision: d.decision ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapModelVersion(ctx: GraphQLContext, d: ModelVersionDTO) {
  return {
    modelId: d.model_id,
    version: d.version,
    urn: d.urn ?? urn(ctx, "experiment", "model_version", `${d.model_id}@${d.version}`),
    stage: d.stage ?? null,
    sourceRunId: d.source_run_id ?? null,
    flavor: d.flavor ?? null,
    mlflowModelRef: d.mlflow_model_ref ?? null,
    stageUpdatedAt: d.stage_updated_at ?? null,
  };
}

/** Flatten the inference-service job_payload (nested model/dataset/timestamps
 * objects) into the flat UI InferenceJob shape. `status` passes through as the
 * JobStatus NAME. */
export function mapInferenceJob(ctx: GraphQLContext, d: InferenceJobDTO) {
  return {
    __typename: "InferenceJob" as const,
    id: d.id,
    urn: urn(ctx, "inference", "job", d.id),
    name: d.name ?? null,
    description: d.description ?? null,
    status: d.status ?? "validating",
    model: d.model
      ? {
          urn: d.model.urn ?? null,
          name: d.model.name ?? null,
          version: d.model.version ?? null,
          stageAtSubmit: d.model.stage_at_submit ?? null,
        }
      : null,
    inputDataset: d.input_dataset
      ? { urn: d.input_dataset.urn ?? null, version: d.input_dataset.version ?? null }
      : null,
    outputDataset: d.output_dataset
      ? { urn: d.output_dataset.urn ?? null, version: d.output_dataset.version ?? null }
      : null,
    rowCount: d.row_count ?? null,
    // A rejected job's error is a structured {code, message, details} object;
    // the SDL field is String, so non-string errors serialize to their JSON
    // (verbatim content, GraphQL-representable form).
    error: d.error == null ? null : typeof d.error === "string" ? d.error : JSON.stringify(d.error),
    pipelineRunUrn: d.pipeline_run_urn ?? null,
    scheduleId: d.schedule_id ?? null,
    // Tier 4b: ml ops — set on the NEW job created by retryInferenceJob.
    retriedFromJobId: d.retried_from_job_id ?? null,
    createdAt: d.timestamps?.created_at ?? null,
    submittedAt: d.timestamps?.submitted_at ?? null,
    startedAt: d.timestamps?.started_at ?? null,
    finishedAt: d.timestamps?.finished_at ?? null,
  };
}

// --- ingestion: connector catalog + connections (pure reshaping, no logic) ---
export function mapConnectorType(d: ConnectorTypeDTO) {
  return {
    connectorType: d.connector_type,
    displayName: d.display_name,
    category: d.category,
    fields: (d.fields ?? []).map((f) => ({
      name: f.name,
      type: f.type,
      required: !!f.required,
      secret: !!f.secret,
      default: f.default ?? null,
      enum: (f.enum ?? null) as string[] | null,
      help: f.help ?? null,
    })),
    secretFields: d.secret_fields ?? [],
    configSchema: d.config_schema ?? {},
  };
}

export function mapConnection(ctx: GraphQLContext, d: ConnectionDTO) {
  return {
    __typename: "DataConnection" as const,
    id: d.id,
    urn: urn(ctx, "ingestion", "connection", d.id),
    name: d.name,
    connectorType: d.connector_type,
    config: d.config ?? {},
    // Secrets are write-only: expose only WHICH secrets are set, never values.
    secretFields: d.secrets ? Object.keys(d.secrets) : [],
    secretSet: d.secret_set ?? (d.secrets ? Object.keys(d.secrets).length > 0 : false),
    trafficDirection: d.traffic_direction ?? null,
    tags: d.tags ?? [],
    workspaceId: d.workspace_id ?? null,
    lastTestStatus: d.last_test_status ?? null,
    lastTestedAt: d.last_tested_at ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapConnectionTest(d: ConnectionTestDTO) {
  return {
    status: d.status === "ok" ? "OK" : "FAILED",
    latencyMs: d.latency_ms ?? null,
    errorCategory: d.error_category ?? null,
    errorDetail: d.error_detail ?? null,
  };
}

export function mapWriteback(ctx: GraphQLContext, d: WritebackDTO) {
  return {
    __typename: "Writeback" as const,
    id: d.id,
    urn: urn(ctx, "ingestion", "writeback", d.id),
    connectionId: d.connection_id,
    workspaceId: d.workspace_id ?? null,
    decisionKind: d.decision_kind,
    decisionRef: d.decision_ref,
    target: d.target ?? {},
    payload: d.payload ?? {},
    status: d.status,
    requestedBy: d.requested_by,
    approvedBy: d.approved_by ?? null,
    attempts: d.attempts ?? 0,
    lastError: d.last_error ?? null,
    targetRef: d.target_ref ?? null,
    deliveredAt: d.delivered_at ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapIngestionSchedule(ctx: GraphQLContext, d: ScheduleDTO) {
  return {
    __typename: "IngestionSchedule" as const,
    id: d.id,
    urn: urn(ctx, "ingestion", "schedule", d.id),
    connectionId: d.connection_id,
    ingestionTemplate: d.ingestion_template ?? null,
    cron: d.cron ?? null,
    intervalSeconds: d.interval_seconds ?? null,
    timezone: d.timezone ?? null,
    watermark: d.watermark ?? null,
    overlapPolicy: d.overlap_policy ?? null,
    enabled: d.enabled ?? false,
    workspaceId: d.workspace_id ?? null,
    lastFiredAt: d.last_fired_at ?? null,
    nextFireAt: d.next_fire_at ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapScheduleRunNow(d: ScheduleFireDTO) {
  return {
    skipped: d.skipped ?? false,
    ingestionId: d.ingestion_id ?? null,
    buffered: d.buffered ?? null,
    status: d.status ?? null,
  };
}

export function mapConnectionPreview(d: ConnectionPreviewDTO) {
  return {
    columns: d.columns ?? [],
    rows: d.rows ?? [],
  };
}

// --- pipeline-orchestrator: no-code builder catalog + templates + runs ------
// Pure reshaping only (snake->camel, dict->list, urn). No logic, no authz.

/** A component/algorithm `parameters` map (keyed by name) -> the UI's flat list.
 * The backend uses `minimum`/`maximum`/`enum`; `help` has no backend field today
 * (we fall back to `item_description` when present, else null). */
function mapStepParams(params: Record<string, StepParamDTO> | null | undefined) {
  return Object.entries(params ?? {}).map(([name, p]) => ({
    name,
    type: String(p.type ?? "string"),
    // Semantic format (JSON-Schema type+format split) — drives the UI widget +
    // data-binding; `itemFormat` is the element semantic for array params.
    format: ((p as { format?: string }).format ?? null) as string | null,
    itemFormat: ((p as { item_format?: string }).item_format ?? null) as string | null,
    required: !!p.required,
    default: p.default ?? null,
    enumValues: (p.enum ?? null) as string[] | null,
    min: p.minimum ?? null,
    max: p.maximum ?? null,
    help: (p.help ?? (p.item_description as string | undefined) ?? null) as string | null,
  }));
}

export function mapPipelineStepType(d: ComponentDTO) {
  return {
    name: d.name,
    displayName: d.label ?? d.name,
    category: d.component_type ?? "other",
    description: (d as { description?: string | null }).description ?? null,
    minInputs: d.min_inputs ?? 0,
    maxInputs: d.max_inputs ?? 0,
    maxOutputs: d.max_outputs ?? 0,
    // Defend the non-null SDL: drop any malformed port missing name/type rather
    // than crash the whole pipelineStepTypes query on non-null resolution.
    outputs: (d.outputs ?? [])
      .filter((o) => o && o.name != null && o.type != null)
      .map((o) => ({ name: o.name, type: o.type })),
    parameters: mapStepParams(d.parameters),
  };
}

export function mapAlgorithmTemplate(d: AlgorithmDTO) {
  return {
    name: d.name,
    displayName: d.label ?? d.name,
    family: d.model_type ?? null,
    // The backend exposes availability as the keys of `input_type`
    // (training | tuning | tuning_cross_validation).
    modes: Object.keys(d.input_type ?? {}),
    parameters: mapStepParams(d.parameters),
  };
}

export function mapPipelineTemplate(ctx: GraphQLContext, d: TemplateDTO) {
  return {
    __typename: "PipelineTemplate" as const,
    id: d.id,
    urn: urn(ctx, "pipeline", "template", d.id),
    name: d.name,
    pipelineType: d.pipeline_type,
    activeVersionId: d.active_version_id ?? null,
    // The DAG definition of the active version — the backend serializes it on
    // single-template reads (get/create/update/…), but not on the list; null there.
    definition: (d.definition ?? null) as unknown,
    validationStatus: d.validation_status ?? null,
    isSystem: d.is_system ?? null,
    archived: d.archived ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapPipelineRun(ctx: GraphQLContext, d: PipelineRunDTO) {
  return {
    __typename: "PipelineRun" as const,
    id: d.id,
    urn: urn(ctx, "pipeline", "run", d.id),
    templateId: d.template_id,
    status: d.status,
    error: (d as { error?: unknown }).error ?? null,
    retriedFromRunId: (d as { retried_from_run_id?: string }).retried_from_run_id ?? null,
    createdAt: d.created_at ?? null,
    startedAt: d.started_at ?? null,
    finishedAt: d.finished_at ?? null,
  };
}

export function mapPipelineTemplateVersion(d: TemplateVersionDTO) {
  return {
    __typename: "PipelineTemplateVersion" as const,
    id: d.id,
    templateId: d.template_id,
    versionNo: d.version_no,
    validationStatus: d.validation_status ?? null,
    validationReport: d.validation_report ?? null,
    manifestDigest: d.manifest_digest ?? null,
    argoTemplateName: d.argo_template_name ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapPipelineSchedule(ctx: GraphQLContext, d: PipelineScheduleDTO) {
  return {
    __typename: "PipelineSchedule" as const,
    id: d.id,
    urn: urn(ctx, "pipeline", "schedule", d.id),
    scheduleId: d.id,
    templateId: d.template_id,
    name: d.name ?? null,
    cron: d.cron,
    timezone: d.timezone ?? null,
    runParameters: (d.run_parameters ?? null) as unknown,
    enabled: d.enabled ?? false,
    nextFireAt: d.next_fire_at ?? null,
    lastFireAt: d.last_fire_at ?? null,
    lastRunId: d.last_run_id ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapCompiledPipelineManifest(d: CompiledManifestDTO) {
  return {
    templateId: d.template_id ?? null,
    versionId: d.version_id ?? null,
    manifestDigest: d.manifest_digest ?? null,
    argoTemplateName: d.argo_template_name ?? null,
    manifest: d.manifest ?? null,
  };
}

export function mapPipelineRunManifest(d: RunManifestDTO) {
  return {
    runId: d.run_id ?? null,
    manifest: d.manifest ?? null,
    resolvedParameters: d.resolved_parameters ?? null,
  };
}

/** Map the backend validation report {status, items[{code,alias,field,problem}]}
 * into the UI contract {valid, issues[{code,message,node}]}. */
export function mapValidationReport(d: ValidationReportDTO) {
  return {
    valid: d.status === "valid",
    issues: (d.items ?? []).map((it) => ({
      code: it.code,
      message: it.problem ?? "",
      node: it.alias ?? null,
    })),
  };
}

// --- semantic-service: semantic models + published dimensions/measures ------
// Pure reshaping only (snake->camel, urn). No logic, no authz.

export function mapSemanticDimension(d: SemanticDimensionDTO) {
  return {
    name: d.name,
    entity: d.entity ?? null,
    dimType: d.type ?? null,
  };
}

export function mapSemanticMeasure(d: SemanticMeasureDTO) {
  return {
    name: d.name,
    agg: d.agg ?? null,
    entity: d.entity ?? null,
  };
}

/** Map a model header (+ optional published definition) into the UI SemanticModel.
 * `def` is null on the list path (headers only) → dimensions/measures resolve to
 * empty arrays; the editor calls semanticModel(name) to hydrate them once picked. */
export function mapSemanticModel(ctx: GraphQLContext, d: SemanticModelDTO, def?: SemanticDefinitionDTO | null) {
  return {
    __typename: "SemanticModel" as const,
    id: d.id,
    urn: urn(ctx, "semantic", "model", d.id),
    name: d.name,
    dimensions: (def?.dimensions ?? []).map(mapSemanticDimension),
    measures: (def?.measures ?? []).map(mapSemanticMeasure),
  };
}

// --- semantic-service: model authoring (create/version/review workflow) -----
// Pure reshaping only (snake->camel, urn, status uppercasing). No logic, no authz.

/** datasetSchema resolver payload: the version's authoritative `schema` map
 * when populated, else the profile's inferred columns (see DatasetVersionDTO
 * doc — the version schema is empty for older-ingested datasets on this
 * deployment; real column data still exists via profiling). */
export function mapDatasetSchema(
  version: DatasetVersionDTO | null,
  profileColumns: ProfileColumnDTO[] | null,
): { name: string; type: string | null; nullable: boolean | null; tags: string[]; inferred: boolean }[] {
  const schema = version?.schema ?? {};
  const names = Object.keys(schema);
  if (names.length > 0) {
    return names.map((name) => ({
      name,
      type: schema[name]?.type ?? null,
      nullable: schema[name]?.nullable ?? null,
      tags: schema[name]?.tags ?? [],
      inferred: false,
    }));
  }
  return (profileColumns ?? []).map((c) => ({
    name: c.name,
    type: c.logical_type ?? null,
    nullable: null,
    tags: [],
    inferred: true,
  }));
}

export function mapSemanticModelSummary(ctx: GraphQLContext, d: SemanticModelDTO) {
  return {
    __typename: "SemanticModelSummary" as const,
    id: d.id,
    urn: urn(ctx, "semantic", "model", d.id),
    workspaceId: d.workspace_id ?? null,
    name: d.name,
    description: d.description ?? null,
    publishedVersionNo: d.published_version_no ?? null,
    draftVersionNo: d.draft_version?.version_no ?? null,
    healthStatus: d.health?.status ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

/** Raw JSON `definition` document (app/domain/definition.py shape, snake_case)
 * -> the typed SemanticModelDefinition GraphQL view. Defensive against missing
 * arrays (a fresh draft may have none yet). */
export function mapSemanticModelDefinitionFull(raw: unknown): {
  entities: unknown[];
  dimensions: unknown[];
  measures: unknown[];
  joinPaths: unknown[];
} | null {
  if (!raw || typeof raw !== "object") return null;
  const doc = raw as Record<string, unknown[]>;
  const entities = (doc.entities ?? []).map((e) => {
    const r = e as Record<string, unknown>;
    return {
      name: r.name,
      datasetUrn: r.dataset_urn,
      table: r.table,
      primaryKey: r.primary_key ?? [],
      datasetVersionPolicy: r.dataset_version_policy ?? { policy: "latest" },
      description: r.description ?? null,
    };
  });
  const dimensions = (doc.dimensions ?? []).map((dm) => {
    const r = dm as Record<string, unknown>;
    return {
      name: r.name,
      entity: r.entity,
      column: r.column ?? null,
      expr: r.expr ?? null,
      dimType: r.type ?? "categorical",
      timeGrains: r.time_grains ?? [],
      synonyms: r.synonyms ?? [],
      description: r.description ?? null,
      deprecated: !!r.deprecated,
      successor: r.successor ?? null,
    };
  });
  const measures = (doc.measures ?? []).map((m) => {
    const r = m as Record<string, unknown>;
    return {
      name: r.name,
      entity: r.entity ?? null,
      agg: r.agg ?? null,
      expr: r.expr ?? null,
      exprMetric: r.expr_metric ?? null,
      filters: r.filters ?? null,
      format: r.format ?? null,
      synonyms: r.synonyms ?? [],
      description: r.description ?? null,
      deprecated: !!r.deprecated,
      successor: r.successor ?? null,
    };
  });
  const joinPaths = (doc.join_paths ?? []).map((j) => {
    const r = j as Record<string, unknown>;
    const on = (r.on as Record<string, unknown>[] | undefined) ?? [];
    return {
      name: r.name,
      fromEntity: r.from_entity,
      toEntity: r.to_entity,
      joinType: r.join_type,
      on: on.map((p) => ({ fromColumn: p.from_column, toColumn: p.to_column })),
      cardinality: r.cardinality,
    };
  });
  return { entities, dimensions, measures, joinPaths };
}

const SEMANTIC_VERSION_STATUS = new Set(["draft", "in_review", "published", "rejected", "superseded"]);

export function mapSemanticModelVersion(ctx: GraphQLContext, modelId: string, d: SemanticVersionDTO) {
  return {
    __typename: "SemanticModelVersion" as const,
    id: d.id,
    urn: urn(ctx, "semantic", "version", d.id),
    modelId,
    versionNo: d.version_no,
    status: SEMANTIC_VERSION_STATUS.has(d.status) ? d.status.toUpperCase() : "DRAFT",
    definition: mapSemanticModelDefinitionFull(d.definition ?? null),
    definitionJson: d.definition ?? null,
    diff: d.diff ?? null,
    submittedBy: d.submitted_by ?? null,
    approvedBy: d.approved_by ?? null,
    decisionNote: d.decision_note ?? null,
    publishedAt: d.published_at ?? null,
    createdAt: d.created_at,
  };
}

export function mapSemanticCompileResult(d: CompileResultDTO, validationError?: string) {
  return {
    sql: d.sql,
    engineDialect: d.engine_dialect ?? null,
    outputSchema: (d.output_schema ?? []).map((c) => ({ name: c.name, type: c.type ?? null, role: c.role ?? null })),
    warnings: d.warnings ?? [],
    provenance: d.provenance ?? null,
    validationAvailable: !validationError && d.validation != null,
    validationValid: d.validation?.valid ?? null,
    validationMessage: validationError ?? d.validation?.message ?? null,
  };
}

const VERIFIED_QUERY_STATUS = new Set(["draft", "pending_review", "approved", "rejected", "archived"]);

export function mapVerifiedQuery(ctx: GraphQLContext, d: VerifiedQueryDTO) {
  return {
    __typename: "VerifiedQuery" as const,
    id: d.id,
    urn: urn(ctx, "semantic", "verified_query", d.id),
    workspaceId: d.workspace_id ?? null,
    modelId: d.model_id ?? null,
    nlText: d.nl_text,
    sqlText: d.sql_text,
    variables: d.variables ?? null,
    status: VERIFIED_QUERY_STATUS.has(d.status) ? d.status.toUpperCase() : "DRAFT",
    tags: d.tags ?? [],
    provenance: d.provenance ?? null,
    healthNote: d.health_note ?? null,
    submittedBy: d.submitted_by ?? null,
    approvedBy: d.approved_by ?? null,
    decidedAt: d.decided_at ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapVerifiedQuerySearchHit(d: VerifiedQuerySearchHitDTO) {
  return {
    __typename: "VerifiedQuerySearchHit" as const,
    id: d.id,
    nlText: d.nl_text,
    sqlText: d.sql_text,
    variables: d.variables ?? null,
    tags: d.tags ?? [],
    modelId: d.model_id ?? null,
    score: d.score,
  };
}

export function mapSemanticOperation(d: SemanticOperationDTO) {
  return {
    operationId: d.operation_id,
    kind: d.kind ?? null,
    status: d.status ?? null,
    report: d.report ?? null,
    createdAt: d.created_at ?? null,
    finishedAt: d.finished_at ?? null,
  };
}

// --- usage: budgets + rate cards ---------------------------------------------
export function mapBudget(ctx: GraphQLContext, d: BudgetDTO) {
  return {
    __typename: "Budget" as const,
    id: d.id,
    urn: urn(ctx, "usage", "budget", d.id),
    scope: budgetScopeString(d.scope) ?? null,
    meterKey: d.meter_key ?? null,
    window: d.window ?? null,
    // budgetView serializes the limit as `limit_value`; `limit` is a client-side
    // normalized alias the usage client fills on the list path only — accept both.
    limitUsd: d.limit_value ?? d.limit ?? null,
    thresholds: d.thresholds ?? [],
    actionAt100: d.action_at_100 ?? d.action ?? null,
    status: d.status ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapRateCard(ctx: GraphQLContext, d: RateCardDTO) {
  return {
    __typename: "RateCard" as const,
    id: d.id,
    urn: urn(ctx, "usage", "ratecard", d.id),
    version: d.version ?? null,
    effectiveFrom: d.effective_from ?? null,
    status: d.status ?? null,
    items: d.items ?? {},
    createdAt: d.created_at ?? null,
  };
}

export function mapAnomaly(ctx: GraphQLContext, d: AnomalyDTO) {
  return {
    __typename: "Anomaly" as const,
    id: d.id,
    urn: urn(ctx, "usage", "anomaly", d.id),
    meterKey: d.meter_key,
    day: d.day,
    observed: d.observed,
    mean: d.mean,
    stddev: d.stddev,
    z: d.z,
    status: d.status,
    dismissedBy: d.dismissed_by ?? null,
    suppressedReason: d.suppressed_reason ?? null,
    createdAt: d.created_at,
  };
}

/** Map a GraphQL DecisionKind enum to the agent-runtime decide action. */
export function decisionAction(kind: string): "approve" | "reject" | "edit_args" | "respond" {
  switch (kind) {
    case "APPROVE": return "approve";
    case "REJECT": return "reject";
    case "EDIT_ARGS": return "edit_args";
    case "RESPOND": return "respond";
    default: return "approve";
  }
}

// ===========================================================================
// Tier 2a: eval (eval-service) mappers.
// ===========================================================================
export function mapEvalSuite(ctx: GraphQLContext, d: EvalSuiteDTO) {
  return {
    __typename: "EvalSuite" as const,
    id: d.id,
    urn: urn(ctx, "eval", "suite", `${d.suite_id}@${d.version}`),
    suiteId: d.suite_id,
    agentKey: d.agent_key,
    version: d.version,
    datasets: d.datasets ?? [],
    scorers: d.scorers ?? [],
    gateRule: d.gate_rule,
    baselineVersion: d.baseline_version ?? null,
    judgeLadderPin: d.judge_ladder_pin ?? null,
    minCases: d.min_cases ?? 0,
    createdAt: d.created_at ?? null,
  };
}

export function mapEvalCaseResult(d: EvalCaseResultDTO) {
  return {
    __typename: "EvalCaseResult" as const,
    id: d.id,
    runId: d.run_id,
    caseId: d.case_id,
    scorerKey: d.scorer_key,
    scorerVersion: d.scorer_version,
    score: d.score,
    passed: d.passed,
    details: d.details ?? null,
    traceRef: d.trace_ref ?? null,
    latencyMs: d.latency_ms ?? null,
    costUsd: d.cost_usd ?? 0,
    weight: d.weight ?? 1,
    createdAt: d.created_at ?? null,
  };
}

/** `cases`/`suite`/`gate` are lazy field resolvers (resolvers/index.ts) — not
 * hydrated here to avoid an N+1 on every run in a list. */
export function mapEvalRun(ctx: GraphQLContext, d: EvalRunDTO) {
  return {
    __typename: "EvalRun" as const,
    id: d.id,
    urn: urn(ctx, "eval", "run", d.id),
    trigger: d.trigger,
    agentKey: d.agent_key,
    candidate: d.candidate ?? {},
    baseline: d.baseline ?? null,
    suitePins: d.suite_pins ?? {},
    memorySnapshotVer: d.memory_snapshot_ver ?? null,
    status: d.status,
    totals: d.totals ?? {},
    costUsd: d.cost_usd ?? 0,
    costCapUsd: d.cost_cap_usd ?? 0,
    startedBy: d.started_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
    _agentKey: d.agent_key,
    _contentDigest: (d.candidate as { content_digest?: string } | undefined)?.content_digest ?? null,
    _suitePins: d.suite_pins ?? {},
  };
}

export function mapEvalDataset(ctx: GraphQLContext, d: EvalDatasetDTO) {
  return {
    __typename: "EvalDataset" as const,
    id: d.id,
    urn: urn(ctx, "eval", "dataset", `${encodeURIComponent(d.dataset_key)}@${d.version}`),
    datasetKey: d.dataset_key,
    agentKey: d.agent_key,
    version: d.version,
    status: d.status,
    description: d.description ?? null,
    caseCount: d.case_count ?? 0,
    provenanceSummary: d.provenance_summary ?? null,
    frozenBy: d.frozen_by ?? null,
    frozenAt: d.frozen_at ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapEvalCase(ctx: GraphQLContext, d: EvalCaseDTO) {
  return {
    __typename: "EvalCase" as const,
    id: d.id,
    urn: urn(ctx, "eval", "case", d.id),
    datasetKey: d.dataset_key,
    datasetVersion: d.dataset_version,
    input: d.input ?? {},
    expected: d.expected ?? {},
    source: d.source,
    sourceRef: d.source_ref ?? null,
    tags: d.tags ?? [],
    weight: d.weight ?? 1,
    status: d.status,
    anonymizationAttestedBy: d.anonymization_attested_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapEvalScorer(ctx: GraphQLContext, d: EvalScorerDTO) {
  return {
    __typename: "EvalScorer" as const,
    id: d.id,
    urn: urn(ctx, "eval", "scorer", `${d.scorer_key}@${d.version}`),
    scorerKey: d.scorer_key,
    version: d.version,
    kind: d.kind,
    gateEligible: d.gate_eligible ?? false,
    configSchema: d.config_schema ?? null,
    applicableExpectedKinds: d.applicable_expected_kinds ?? [],
    imageRef: d.image_ref ?? null,
    judgePromptRef: d.judge_prompt_ref ?? null,
    judgePromptVer: d.judge_prompt_ver ?? null,
    judgeAgreement: d.judge_agreement ?? null,
    status: d.status,
    createdAt: d.created_at ?? null,
  };
}

export function mapEvalGateResult(ctx: GraphQLContext, d: EvalGateResultDTO) {
  return {
    __typename: "EvalGateResult" as const,
    id: d.id,
    urn: urn(ctx, "eval", "gate", d.gate_run_id),
    gateRunId: d.gate_run_id,
    runId: d.run_id,
    agentKey: d.agent_key,
    contentDigest: d.content_digest,
    suiteId: d.suite_id,
    suiteVersion: d.suite_version,
    datasetVersion: d.dataset_version,
    gatePassed: d.gate_passed,
    verdicts: d.verdicts ?? [],
    failedCasesSample: d.failed_cases_sample ?? [],
    reportUrl: d.report_url ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapEvalCanary(ctx: GraphQLContext, d: EvalCanaryDTO) {
  return {
    __typename: "EvalCanary" as const,
    id: d.id,
    urn: urn(ctx, "eval", "canary", d.comparison_id),
    comparisonId: d.comparison_id,
    agentKey: d.agent_key,
    candidateVersion: d.candidate_version,
    baselineVersion: d.baseline_version,
    sampleSpec: d.sample_spec ?? null,
    mode: d.mode,
    status: d.status,
    report: d.report ?? {},
    samples: d.samples ?? 0,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapEvalTrendPoint(d: EvalTrendPointDTO) {
  return {
    __typename: "EvalTrendPoint" as const,
    runId: d.run_id,
    agentVersion: d.agent_version ?? null,
    scorer: d.scorer,
    mean: d.mean ?? null,
    passRate: d.pass_rate ?? null,
    at: d.at,
  };
}

export function mapEvalSloRow(d: EvalSloRowDTO) {
  return {
    __typename: "EvalSloRow" as const,
    agentKey: d.agent_key,
    agentVersion: d.agent_version ?? null,
    tenantId: d.tenant_id ?? null,
    window: d.window,
    windowStart: d.window_start,
    metrics: d.metrics ?? {},
    targets: d.targets ?? {},
    sampleN: d.sample_n ?? 0,
  };
}

// ===========================================================================
// Tier 2a: ai-gateway admin mappers.
// ===========================================================================
export function mapAiProvider(ctx: GraphQLContext, d: AiProviderDTO) {
  return {
    __typename: "AiProviderDeployment" as const,
    id: d.id,
    urn: urn(ctx, "aigateway", "provider", d.id),
    provider: d.provider,
    modelFamily: d.model_family,
    deploymentName: d.deployment_name,
    region: d.region,
    cloud: d.cloud,
    endpointVaultRef: d.endpoint_vault_ref,
    tpmLimit: d.tpm_limit ?? 0,
    rpmLimit: d.rpm_limit ?? 0,
    priority: d.priority ?? 100,
    status: d.status,
    circuitState: d.circuit_state ?? null,
    healthy: d.healthy ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapAiLadder(d: AiLadderDTO) {
  return {
    __typename: "AiModelLadder" as const,
    id: d.id,
    requestClass: d.request_class,
    scope: d.scope,
    rungs: d.rungs ?? [],
    version: d.version ?? 1,
    maxRung: d.max_rung ?? null,
  };
}

export function mapAiBudget(ctx: GraphQLContext, d: AiBudgetDTO) {
  return {
    __typename: "AiBudget" as const,
    id: d.id,
    urn: urn(ctx, "aigateway", "budget", d.id),
    scopeType: d.scope_type,
    scopeRef: d.scope_ref,
    window: d.window,
    limitUsd: d.limit_usd,
    degradePct: d.degrade_pct ?? 95,
    status: d.status,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapAiSpendRow(d: AiSpendRowDTO) {
  return {
    __typename: "AiSpendRow" as const,
    budgetId: d.budget_id,
    scopeType: d.scope_type,
    scopeRef: d.scope_ref,
    window: d.window,
    windowStart: d.window_start,
    limitUsd: d.limit_usd,
    spendUsd: d.spend_usd,
    reservedUsd: d.reserved_usd,
    resetAt: d.reset_at,
  };
}

// ADDED (provider-agnostic + cost-detail): map the ai-gateway cost breakdown.
function mapAiCostRollup(r: AiCostRollupDTO) {
  return {
    provider: r.provider ?? null,
    model: r.model ?? null,
    modelAlias: r.model_alias ?? null,
    requestClass: r.request_class ?? null,
    requests: r.requests ?? 0,
    inputTokens: r.input_tokens ?? 0,
    outputTokens: r.output_tokens ?? 0,
    costUsd: r.cost_usd ?? 0,
  };
}

export function mapAiCostBreakdown(d: AiCostBreakdownDTO) {
  return {
    __typename: "AiCostBreakdown" as const,
    window: {
      since: d.window.since,
      hours: d.window.hours,
      priceVersion: d.window.price_version,
    },
    totals: {
      requests: d.totals.requests,
      inputTokens: d.totals.input_tokens,
      outputTokens: d.totals.output_tokens,
      costUsd: d.totals.cost_usd,
    },
    byProvider: (d.by_provider ?? []).map(mapAiCostRollup),
    byModel: (d.by_model ?? []).map(mapAiCostRollup),
    byRequestClass: (d.by_request_class ?? []).map(mapAiCostRollup),
    detail: (d.detail ?? []).map(mapAiCostRollup),
  };
}

export function mapAiVirtualKey(ctx: GraphQLContext, d: AiVirtualKeyDTO) {
  return {
    __typename: "AiVirtualKey" as const,
    id: d.id,
    urn: urn(ctx, "aigateway", "key", d.id),
    principalType: d.principal_type,
    principalId: d.principal_id,
    allowedRequestClasses: d.allowed_request_classes ?? [],
    maxRung: d.max_rung ?? 0,
    expiresAt: d.expires_at ?? null,
    status: d.status,
    createdAt: d.created_at ?? null,
    // Only present on create/rotate responses (shown once, AIG-FR-030).
    secret: d.secret ?? null,
  };
}

export function mapAiGuardrailPolicy(d: AiGuardrailPolicyDTO) {
  return {
    __typename: "AiGuardrailPolicy" as const,
    policy: d.policy ?? {},
    version: d.version ?? 0,
  };
}

// ===========================================================================
// Tier 2b: notification-service mappers (inbox/preferences/rules/webhooks/
// templates/admin ops).
// ===========================================================================
export function mapNotification(ctx: GraphQLContext, d: NotificationDTO) {
  return {
    __typename: "Notification" as const,
    id: d.id,
    urn: urn(ctx, "notification", "notification", d.id),
    eventType: d.event_type,
    severityClass: d.severity_class ?? null,
    title: d.title,
    body: d.body ?? null,
    resourceUrn: d.resource_urn ?? null,
    deepLink: d.deep_link ?? null,
    readAt: d.read_at ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapNotificationPreferences(d: NotificationPreferencesDTO) {
  return {
    __typename: "NotificationPreferences" as const,
    channelOverrides: d.channel_overrides ?? {},
    mutes: d.mutes ?? null,
    quietHours: d.quiet_hours ?? null,
    digestConfig: d.digest_config ?? {},
    updatedAt: d.updated_at ?? null,
  };
}

export function mapNotificationRule(d: NotificationRuleDTO) {
  return {
    __typename: "NotificationRule" as const,
    id: d.id,
    scope: d.scope,
    subjectType: d.subject_type,
    subjectId: d.subject_id,
    eventTypes: d.event_types ?? [],
    resourceFilter: d.resource_filter ?? null,
    channels: d.channels ?? [],
    digestEnabled: d.digest_enabled ?? false,
    digestWindow: d.digest_window ?? null,
    active: d.active,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapWebhookEndpoint(d: WebhookEndpointDTO) {
  return {
    __typename: "WebhookEndpoint" as const,
    id: d.id,
    url: d.url,
    eventTypes: d.event_types ?? [],
    secrets: (d.secrets ?? []).map((s) => ({
      version: s.version,
      secret: s.secret,
      createdAt: s.created_at ?? null,
      expiresAt: s.expires_at ?? null,
    })),
    active: d.active,
    verifiedAt: d.verified_at ?? null,
    circuitState: d.circuit_state ?? null,
    consecutiveFailures: d.consecutive_failures ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapWebhookDelivery(d: WebhookDeliveryDTO) {
  return {
    __typename: "WebhookDelivery" as const,
    id: d.id,
    eventId: d.event_id ?? null,
    status: d.status,
    attempts: d.attempts ?? 0,
    lastError: d.last_error || null,
    providerMsgId: d.provider_msg_id || null,
    nextRetryAt: d.next_retry_at ?? null,
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapNotificationTemplate(d: NotificationTemplateDTO) {
  return {
    __typename: "NotificationTemplate" as const,
    id: d.id,
    key: d.key,
    channel: d.channel,
    locale: d.locale,
    version: d.version,
    subjectTpl: d.subject_tpl ?? null,
    bodyHtmlTpl: d.body_html_tpl ?? null,
    bodyTextTpl: d.body_text_tpl ?? null,
    status: d.status,
    publishedAt: d.published_at ?? null,
    createdBy: d.created_by ?? null,
    createdAt: d.created_at ?? null,
  };
}

export function mapEmailSuppression(d: SuppressionDTO) {
  return {
    __typename: "EmailSuppression" as const,
    id: d.id,
    emailHash: d.email_hash,
    reason: d.reason,
    createdAt: d.created_at ?? null,
    clearedAt: d.cleared_at ?? null,
  };
}

// ===========================================================================
// Tier 2b: tool-plane registry admin mappers.
// ===========================================================================
export function mapTool(d: ToolDTO) {
  return {
    __typename: "Tool" as const,
    toolId: d.tool_id,
    displayName: d.display_name ?? null,
    ownerService: d.owner_service,
    ownerTeam: d.owner_team ?? null,
    enabledByDefault: d.enabled_by_default ?? false,
    sideEffects: d.side_effects ?? "none",
    tags: d.tags ?? [],
    createdAt: d.created_at ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapToolVersion(d: ToolVersionDTO) {
  return {
    __typename: "ToolVersion" as const,
    toolId: d.tool_id,
    version: d.version,
    status: d.status,
    semanticDescription: d.semantic_description ?? null,
    permissionTier: d.permission_tier ?? null,
    costWeight: d.cost_weight ?? null,
    sideEffects: d.side_effects ?? null,
    inputSchema: d.input_schema ?? null,
    outputSchema: d.output_schema ?? null,
    declaredSla: d.declared_sla ?? null,
    deprecationEndsAt: d.deprecation_ends_at ?? null,
    publishedAt: d.published_at ?? null,
  };
}

export function mapToolHealth(d: ToolHealthDTO) {
  return {
    __typename: "ToolHealth" as const,
    toolId: d.tool_id,
    versions: (d.versions ?? []).map((v) => ({
      version: v.version,
      status: v.status,
      declaredSla: v.declared_sla ?? null,
      health: v.health ?? null,
    })),
  };
}

export function mapTenantToolSettings(d: TenantToolSettingsDTO) {
  return {
    __typename: "TenantToolSettings" as const,
    toolId: d.tool_id,
    enabled: d.enabled,
    maxTierOverride: d.max_tier_override || null,
    argumentConstraints: d.argument_constraints ?? null,
    rateLimitOverride: d.rate_limit_override ?? null,
    updatedAt: d.updated_at ?? null,
  };
}

export function mapByoSubmission(d: BYOSubmissionDTO) {
  return {
    __typename: "ByoSubmission" as const,
    id: d.id,
    manifest: d.manifest ?? null,
    endpointUrl: d.endpoint_url,
    authMethod: d.auth_method,
    requestedTier: d.requested_tier,
    egressDescription: d.egress_description || null,
    status: d.status,
    decidedBy: d.decided_by || null,
    decisionMessage: d.decision_message || null,
    createdAt: d.created_at ?? null,
  };
}

// ===========================================================================
// Tier 2b: agent-runtime catalog/registry mappers.
// ===========================================================================
export function mapAgentDefinition(d: AgentDefinitionDTO) {
  return {
    __typename: "AgentDefinition" as const,
    agentKey: d.agent_key,
    displayName: d.display_name,
    description: d.description ?? null,
    ownerTeam: d.owner_team ?? null,
    defaultWriteMode: d.default_write_mode ?? null,
    status: d.status ?? null,
    latestPublishedVersion: d.latest_published_version ?? null,
  };
}

export function mapAgentVersionInfo(d: AgentVersionDTO) {
  return {
    __typename: "AgentVersionInfo" as const,
    agentKey: d.agent_key,
    version: d.version,
    status: d.status,
    graphRef: d.graph_ref ?? null,
    graphDigest: d.graph_digest ?? null,
    guardrailProfile: d.guardrail_profile ?? null,
    evalGateResultId: d.eval_gate_result_id ?? null,
    toolset: d.toolset ?? null,
    modelConfig: d.model_config ?? null,
  };
}

export function mapTenantAgentConfig(d: TenantAgentConfigDTO) {
  return {
    __typename: "TenantAgentConfig" as const,
    agentKey: d.agent_key,
    configured: d.configured,
    enabled: d.enabled,
    pinnedVersion: d.pinned_version ?? null,
    promptParams: d.prompt_params ?? null,
    autoExecutePolicy: d.auto_execute_policy ?? null,
    selfApproval: d.self_approval ?? false,
  };
}

export function mapAgentRunListItem(ctx: GraphQLContext, d: AgentRunListItemDTO) {
  return {
    __typename: "AgentRunListItem" as const,
    id: d.id,
    urn: urn(ctx, "agent", "run", d.id),
    sessionId: d.session_id ?? null,
    agentKey: d.agent_key ?? null,
    agentVersion: d.agent_version ?? null,
    status: up(d.status),
    principalType: d.principal_type ?? null,
    usage: d.usage ?? null,
    error: d.error ?? null,
    createdAt: d.created_at ?? null,
  };
}

// ==== Tier 4b: identity/rbac admin ===========================================

/** identity CreatedServiceAccount ({service_account, api_key}) — the api_key is
 * passed through VERBATIM (shown exactly once; never persisted anywhere). */
export function mapCreatedServiceAccount(ctx: GraphQLContext, d: CreatedServiceAccountDTO) {
  return {
    __typename: "CreatedServiceAccount" as const,
    serviceAccount: mapServiceAccount(ctx, d.service_account),
    apiKey: d.api_key,
  };
}

export function mapEffectiveAccessEntry(d: EffectiveAccessEntryDTO) {
  return {
    __typename: "EffectiveAccessEntry" as const,
    subjectType: d.subject_type,
    subjectId: d.subject_id,
    level: d.level,
    provenance: d.provenance,
    via: d.via ?? null,
    grantId: d.grant_id,
    workspaceId: d.workspace_id,
  };
}

export function mapContentGrant(d: ContentGrantDTO) {
  return {
    __typename: "ContentGrant" as const,
    id: d.id,
    workspaceId: d.workspace_id,
    resourceUrn: d.resource_urn,
    subjectType: d.subject_type,
    subjectId: d.subject_id,
    level: d.level,
    implicit: d.implicit ?? false,
    createdAt: d.created_at ?? null,
  };
}

/** rbac bulk-membership response ({results, succeeded, failed}) — the REAL
 * per-entry partial-failure report, never a blind success. */
export function mapBulkGroupMembershipResult(d: BulkMembersResponseDTO) {
  return {
    __typename: "BulkGroupMembershipResult" as const,
    results: (d.results ?? []).map((r) => ({
      __typename: "GroupMemberOpResult" as const,
      userId: r.user_id,
      op: r.op,
      ok: r.ok,
      code: r.code ?? null,
    })),
    succeeded: d.succeeded ?? 0,
    failed: d.failed ?? 0,
  };
}

// ==== Tier 4b: ml ops (experiment-service register/notes/artifacts + ========
// ==== inference-service validate/schedules) =================================

/** register response ({model_id, version, stage, model_created}). */
export function mapRegisterModelResult(d: RegisterRunResultDTO) {
  return {
    __typename: "RegisterModelResult" as const,
    modelId: d.model_id,
    version: d.version,
    stage: d.stage ?? null,
    modelCreated: d.model_created ?? false,
  };
}

/** Run note routes serialize {run_id, description}. */
export function mapRunNote(d: RunNoteDTO) {
  return {
    __typename: "RunNote" as const,
    runId: d.run_id,
    description: d.description ?? null,
  };
}

/** One artifact index row ({path, size_bytes, content_type}). */
export function mapRunArtifact(d: RunArtifactDTO) {
  return {
    __typename: "RunArtifact" as const,
    path: d.path,
    sizeBytes: d.size_bytes ?? null,
    contentType: d.content_type ?? null,
  };
}

/** The validate compatibility report (CompatibilityReport.as_dict + the
 * stage_error the route folds in when only the stage policy fails). */
export function mapCompatibilityReport(d: CompatibilityReportDTO) {
  return {
    __typename: "InferenceCompatibilityReport" as const,
    compatible: d.compatible ?? false,
    modelStage: d.model_stage ?? null,
    columns: (d.columns ?? []).map((c) => ({
      __typename: "CompatColumn" as const,
      name: c.name,
      requiredType: c.required_type ?? null,
      actualType: c.actual_type ?? null,
      verdict: c.verdict,
    })),
    warnings: d.warnings ?? [],
    rowCount: d.row_count ?? null,
    stageError: d.stage_error ?? null,
  };
}

// inference-service serializes overlap_policy as the raw OverlapPolicy IntEnum
// value (schemas.py schedule_payload does NOT name-convert it, unlike
// stage_selector) — normalize to the name the create/patch bodies accept.
const OVERLAP_POLICY_NAMES: Record<number, string> = { 0: "skip", 1: "queue", 2: "cancel_running" };
function overlapPolicyName(v: string | number | null | undefined): string | null {
  if (v == null) return null;
  if (typeof v === "number") return OVERLAP_POLICY_NAMES[v] ?? String(v);
  return v;
}

/** schedule_payload → InferenceSchedule. nextFireAt is next_fire_preview.at
 * (null while paused); overlap_policy arrives as the IntEnum value and is
 * normalized to its name (skip | queue | cancel_running). */
export function mapInferenceSchedule(ctx: GraphQLContext, d: InferenceScheduleDTO) {
  return {
    __typename: "InferenceSchedule" as const,
    id: d.id,
    urn: urn(ctx, "inference", "schedule", d.id),
    name: d.name ?? null,
    enabled: d.enabled ?? false,
    pausedReason: d.paused_reason ?? null,
    modelVersionUrn: d.model_version_urn ?? null,
    modelUrn: d.model_urn ?? null,
    stageSelector: d.stage_selector ?? null,
    inputSelector: d.input_selector ?? null,
    output: d.output ?? null,
    cron: d.cron ?? null,
    intervalSeconds: d.interval_seconds ?? null,
    timezone: d.timezone ?? null,
    overlapPolicy: overlapPolicyName(d.overlap_policy),
    consecutiveFailures: d.consecutive_failures ?? null,
    temporalScheduleId: d.temporal_schedule_id ?? null,
    notifyOnFailure: d.notify_on_failure ?? null,
    nextFireAt: d.next_fire_preview?.at ?? null,
  };
}
