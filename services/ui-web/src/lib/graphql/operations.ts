/**
 * Typed GraphQL documents against the bff-graphql schema. One string per
 * operation; hashes for these become the persisted-operation manifest in prod
 * (UI-FR-046). Every document here is exercised against the REAL schema in e2e.
 */
import type {
  AgentRun,
  AlgorithmTemplate,
  AuditEvent,
  Case,
  CaseComment,
  CaseActivity,
  CaseOperation,
  Disposition,
  CaseField,
  CaseSlaPolicy,
  Chart,
  ChartShapedData,
  ChartType,
  Connection,
  ConnectionTestResult,
  ConnectorType,
  CostPanel,
  Budget,
  CreateBudgetInput,
  UpdateBudgetInput,
  RateCard,
  CreateRateCardInput,
  Anomaly,
  CreateChartInput,
  CreateConnectionInput,
  CreateDashboardInput,
  CreateExperimentInput,
  CreateInferenceJobInput,
  CreatePipelineInput,
  CreateWorkspaceInput,
  Dashboard,
  DataConnection,
  Dataset,
  DatasetLineage,
  Experiment,
  Ingestion,
  CreateIngestionInput,
  Upload,
  CreateUploadInput,
  CompleteUploadInput,
  QueryResult,
  RunSqlInput,
  SavedQuery,
  // Tier 4a: data-plane secondary CRUD/lifecycle.
  SavedQueryVersion,
  SavedQueryInput,
  QueryExecution,
  QueryStats,
  IngestionSchedule,
  CreateIngestionScheduleInput,
  UpdateIngestionScheduleInput,
  ScheduleRunNowResult,
  UpdateConnectionInput,
  ConnectionPreviewInput,
  ConnectionPreview,
  DatasetConsumers,
  DatasetVersion,
  SimilarDataset,
  ReprofileResult,
  VerifiedQuery,
  VerifiedQuerySearchHit,
  CreateVerifiedQueryInput,
  UpdateVerifiedQueryInput,
  SemanticOperation,
  PipelineTemplateVersion,
  CompiledPipelineManifest,
  PipelineRunManifest,
  Group,
  GroupMember,
  CreateTeamInput,
  UpdateTeamInput,
  Role,
  // Tier 4b: identity/rbac admin (lifecycle, roles, grants, bulk membership).
  UpdateWorkspaceInput,
  CreateGroupInput,
  UpdateGroupInput,
  GroupMemberOpInput,
  BulkGroupMembershipResult,
  CreateRoleInput,
  UpdateRoleInput,
  CreateServiceAccountInput,
  CreatedServiceAccount,
  EffectiveAccessEntry,
  ContentGrant,
  CreateContentGrantInput,
  InferenceJob,
  // Tier 4b: ml ops (register/notes/artifacts/compare/cards + inference validate/schedules).
  RegisterModelResult,
  RegisterRunInput,
  UpdateExperimentInput,
  RunNote,
  RunArtifact,
  RunComparison,
  ModelCardOverlayInput,
  InferenceCompatibilityReport,
  ValidateInferenceInput,
  InferenceSchedule,
  CreateInferenceScheduleInput,
  UpdateInferenceScheduleInput,
  InviteUserInput,
  JSONValue,
  Model,
  ModelVersion,
  PipelineRun,
  PromotionRequest,
  Promotion,
  PipelineStepType,
  PipelineTemplate,
  PipelineSchedule,
  CreatePipelineScheduleInput,
  Proposal,
  ReportSubscription,
  Run,
  RunPipelineInput,
  SemanticModel,
  ServiceAccount,
  SetEmbedConfigResult,
  TenantIdpConfig,
  Tenant,
  UpdateChartInput,
  UpdateDashboardInput,
  UpdatePipelineInput,
  User,
  ValidationResult,
  Viewer,
  Workspace,
  Writeback,
  DecisionModel,
  CreateDecisionModelInput,
  BatchEvaluateResult,
  ResolutionRun,
  ResolutionRunDetail,
  MergeCandidate,
  ResolveEntitiesResult,
  EntityMergeProposal,
  MaterializeResolvedResult,
  Pack,
  PackInstall,
  PackInstallPlan,
  PackUninstallResult,
  PackCompleteResult,
  DatasetColumn,
  SemanticModelSummary,
  SemanticModelVersion,
  CreateSemanticModelInput,
  UpdateSemanticModelInput,
  CompileSemanticModelInput,
  SemanticCompileResult,
  KillSwitch,
  KillSwitchLiftResult,
  MemoryRecord,
  ErasureRequest,
  AuthzExplanation,
  ChainVerifyResult,
  ComplianceJob,
  EvalSuite,
  EvalRun,
  EvalDataset,
  EvalCase,
  EvalScorer,
  EvalGateResult,
  EvalCanary,
  EvalTrendPoint,
  EvalSloRow,
  CreateEvalSuiteInput,
  UpdateEvalSuiteInput,
  CreateEvalRunInput,
  CreateEvalDatasetInput,
  CreateEvalCaseInput,
  EvalCasePatchInput,
  CreateEvalScorerInput,
  UpdateEvalScorerInput,
  CreateEvalCanaryInput,
  AiProviderDeployment,
  AiModelLadder,
  AiBudget,
  AiSpendRow,
  AiCostBreakdown,
  AiVirtualKey,
  AiGuardrailPolicy,
  CreateAiProviderInput,
  PatchAiProviderInput,
  CreateAiBudgetInput,
  PatchAiBudgetInput,
  CreateAiVirtualKeyInput,
  // Tier 2b: notification-service + tool-plane registry + agent catalog.
  Notification,
  NotificationPreferences,
  NotificationRule,
  WebhookEndpoint,
  WebhookDelivery,
  NotificationTemplate,
  NotificationTemplatePreview,
  NotificationDeliveryStats,
  EmailSuppression,
  Tool,
  ToolVersion,
  ToolVersionLifecycleResult,
  ToolHealth,
  ToolSchema,
  TenantToolSettings,
  ByoSubmission,
  ByoDecision,
  AgentDefinition,
  AgentVersionInfo,
  AgentVersionPublishResult,
  TenantAgentConfig,
  AgentRunListItem,
} from "./types";

export const ME = /* GraphQL */ `
  query Me {
    me { userId tenantId tenantName workspaceId workspaceName type scopes isPlatformAdmin roles capabilities capsDegraded displayLabels { key value } }
  }
`;
export interface MeResult {
  me: Viewer;
}

export const DATASETS = /* GraphQL */ `
  query Datasets($first: Int, $after: String, $q: String, $filter: DatasetFilter) {
    datasets(first: $first, after: $after, q: $q, filter: $filter) {
      nodes { id urn name description status tags rowCount createdAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface DatasetsResult {
  datasets: Connection<Dataset>;
}

export const DATASET = /* GraphQL */ `
  query Dataset($id: ID!) {
    dataset(id: $id) {
      id urn name description status tags rowCount createdAt
      profile { rowCount columnCount fullJsonUrl htmlReportUrl }
    }
  }
`;
export interface DatasetResult {
  dataset: Dataset | null;
}

export const ARCHIVE_DATASET = /* GraphQL */ `
  mutation ArchiveDataset($id: ID!, $force: Boolean) {
    archiveDataset(id: $id, force: $force)
  }
`;
export interface ArchiveDatasetResult {
  archiveDataset: boolean;
}

export const RESTORE_DATASET = /* GraphQL */ `
  mutation RestoreDataset($id: ID!) {
    restoreDataset(id: $id) { id urn name description status tags rowCount createdAt archived archivedAt }
  }
`;
export interface RestoreDatasetResult {
  restoreDataset: Dataset;
}

export const UPDATE_DATASET = /* GraphQL */ `
  mutation UpdateDataset($id: ID!, $input: UpdateDatasetInput!) {
    updateDataset(id: $id, input: $input) {
      id urn name description status tags rowCount createdAt archived archivedAt
    }
  }
`;
export interface UpdateDatasetResult {
  updateDataset: Dataset;
}

/* ---------- ingestion: connector catalog + data-source connections ---------- */
export const CONNECTOR_TYPES = /* GraphQL */ `
  query ConnectorTypes {
    connectorTypes {
      connectorType displayName category secretFields
      fields { name type required secret default enum help }
    }
  }
`;
export interface ConnectorTypesResult {
  connectorTypes: ConnectorType[];
}

const CONNECTION_FIELDS = /* GraphQL */ `
  id urn name connectorType config secretFields secretSet
  trafficDirection tags workspaceId lastTestStatus lastTestedAt createdAt updatedAt
`;

export const CONNECTIONS = /* GraphQL */ `
  query Connections($first: Int, $after: String, $q: String, $connectorType: String) {
    connections(first: $first, after: $after, q: $q, connectorType: $connectorType) {
      nodes { ${CONNECTION_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ConnectionsResult {
  connections: Connection<DataConnection>;
}

export const CONNECTION = /* GraphQL */ `
  query ConnectionDetail($id: ID!) {
    connection(id: $id) { ${CONNECTION_FIELDS} }
  }
`;
export interface ConnectionDetailResult {
  connection: DataConnection | null;
}

export const CREATE_CONNECTION = /* GraphQL */ `
  mutation CreateConnection($input: CreateConnectionInput!, $idempotencyKey: String!) {
    createConnection(input: $input, idempotencyKey: $idempotencyKey) { ${CONNECTION_FIELDS} }
  }
`;
export interface CreateConnectionResult {
  createConnection: DataConnection;
}
export type { CreateConnectionInput };

export const TEST_CONNECTION = /* GraphQL */ `
  mutation TestConnection($id: ID, $type: String, $config: JSON, $secrets: JSON) {
    testConnection(id: $id, type: $type, config: $config, secrets: $secrets) {
      status latencyMs errorCategory errorDetail
    }
  }
`;
export interface TestConnectionResult {
  testConnection: ConnectionTestResult;
}

const WRITEBACK_FIELDS = `
  id urn connectionId workspaceId decisionKind decisionRef target payload
  status requestedBy approvedBy attempts lastError targetRef deliveredAt createdAt updatedAt
`;

export const WRITEBACKS = /* GraphQL */ `
  query Writebacks($status: String, $workspaceId: String, $first: Int) {
    writebacks(status: $status, workspaceId: $workspaceId, first: $first) { ${WRITEBACK_FIELDS} }
  }
`;
export interface WritebacksResult {
  writebacks: Writeback[];
}

export const WRITEBACK = /* GraphQL */ `
  query WritebackDetail($id: ID!) {
    writeback(id: $id) { ${WRITEBACK_FIELDS} }
  }
`;
export interface WritebackResult {
  writeback: Writeback | null;
}

export const CREATE_WRITEBACK = /* GraphQL */ `
  mutation CreateWriteback($input: CreateWritebackInput!, $idempotencyKey: String!) {
    createWriteback(input: $input, idempotencyKey: $idempotencyKey) { ${WRITEBACK_FIELDS} }
  }
`;
export interface CreateWritebackResult {
  createWriteback: Writeback;
}

export const APPROVE_WRITEBACK = /* GraphQL */ `
  mutation ApproveWriteback($id: ID!) {
    approveWriteback(id: $id) { ${WRITEBACK_FIELDS} }
  }
`;
export interface ApproveWritebackResult {
  approveWriteback: Writeback;
}

export const REJECT_WRITEBACK = /* GraphQL */ `
  mutation RejectWriteback($id: ID!) {
    rejectWriteback(id: $id) { ${WRITEBACK_FIELDS} }
  }
`;
export interface RejectWritebackResult {
  rejectWriteback: Writeback;
}

export const RETRY_WRITEBACK = /* GraphQL */ `
  mutation RetryWriteback($id: ID!) {
    retryWriteback(id: $id) { ${WRITEBACK_FIELDS} }
  }
`;
export interface RetryWritebackResult {
  retryWriteback: Writeback;
}

// ---- BRD 54 inc2: governed decision tables ---------------------------------
const DECISION_MODEL_FIELDS = `
  id name version status workspaceId datasetUrn createdBy approvedBy approvedAt
  rules { when { column op value } then { dispositionCode severity } note }
  defaultOutcome { dispositionCode severity }
`;

export const DECISION_MODELS = /* GraphQL */ `
  query DecisionModels {
    decisionModels { ${DECISION_MODEL_FIELDS} }
  }
`;
export interface DecisionModelsResult { decisionModels: DecisionModel[] }

export const DECISION_MODEL = /* GraphQL */ `
  query DecisionModelDetail($id: ID!) {
    decisionModel(id: $id) { ${DECISION_MODEL_FIELDS} }
  }
`;
export interface DecisionModelResult { decisionModel: DecisionModel | null }

export const CREATE_DECISION_MODEL = /* GraphQL */ `
  mutation CreateDecisionModel($input: CreateDecisionModelInput!, $idempotencyKey: String!) {
    createDecisionModel(input: $input, idempotencyKey: $idempotencyKey) { ${DECISION_MODEL_FIELDS} }
  }
`;
export interface CreateDecisionModelResult { createDecisionModel: DecisionModel }
export type { CreateDecisionModelInput };

export const BATCH_EVALUATE_DECISION_MODEL = /* GraphQL */ `
  mutation BatchEvaluate($id: ID!, $input: BatchEvaluateInput!, $propose: Boolean!, $idempotencyKey: String!) {
    batchEvaluateDecisionModel(id: $id, input: $input, propose: $propose, idempotencyKey: $idempotencyKey) {
      modelId proposed
      summary { cases matched unmatched proposalsCreated byOutcome }
      results { caseId matched ruleIndex explanation outcome { dispositionCode severity } proposalId proposalStatus executed }
    }
  }
`;
export interface BatchEvaluateResultData { batchEvaluateDecisionModel: BatchEvaluateResult }

export const DECISION_MODEL_VERSIONS = /* GraphQL */ `
  query DecisionModelVersions($id: ID!) {
    decisionModelVersions(id: $id) { ${DECISION_MODEL_FIELDS} }
  }
`;
export interface DecisionModelVersionsResult { decisionModelVersions: DecisionModel[] }

export const APPROVE_DECISION_MODEL = /* GraphQL */ `
  mutation ApproveDecisionModel($id: ID!, $idempotencyKey: String!) {
    approveDecisionModel(id: $id, idempotencyKey: $idempotencyKey) { ${DECISION_MODEL_FIELDS} }
  }
`;
export interface ApproveDecisionModelResult { approveDecisionModel: DecisionModel }

export const NEW_DECISION_MODEL_VERSION = /* GraphQL */ `
  mutation NewDecisionModelVersion($id: ID!, $input: CreateDecisionModelInput!, $idempotencyKey: String!) {
    newDecisionModelVersion(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${DECISION_MODEL_FIELDS} }
  }
`;
export interface NewDecisionModelVersionResult { newDecisionModelVersion: DecisionModel }

// ---- BRD 56: entity resolution (steward surface) ---------------------------
const RESOLUTION_RUN_FIELDS = /* GraphQL */ `
  runId datasetId configId entityType recordCount resolvedEntityCount
  mergedClusterCount reviewCandidateCount status createdBy createdAt
`;

export const RESOLUTION_RUNS = /* GraphQL */ `
  query ResolutionRuns($datasetId: ID!, $limit: Int) {
    resolutionRuns(datasetId: $datasetId, limit: $limit) { ${RESOLUTION_RUN_FIELDS} }
  }
`;
export interface ResolutionRunsResult { resolutionRuns: ResolutionRun[] }

export const RESOLUTION_RUN = /* GraphQL */ `
  query ResolutionRunDetail($id: ID!) {
    resolutionRun(id: $id) {
      ${RESOLUTION_RUN_FIELDS}
      clusters {
        resolvedEntityId memberCount confidence method
        members { memberPk method evidence }
      }
    }
  }
`;
export interface ResolutionRunResult { resolutionRun: ResolutionRunDetail | null }

export const MERGE_CANDIDATES = /* GraphQL */ `
  query MergeCandidates($runId: ID!, $status: String) {
    mergeCandidates(runId: $runId, status: $status) {
      id runId datasetId entityType leftPk rightPk score evidence status
      proposalId decidedBy decidedAt createdAt
    }
  }
`;
export interface MergeCandidatesResult { mergeCandidates: MergeCandidate[] }

export const RESOLVE_ENTITIES = /* GraphQL */ `
  mutation ResolveEntities($datasetId: ID!, $input: ResolveEntitiesInput!, $idempotencyKey: String!) {
    resolveEntities(datasetId: $datasetId, input: $input, idempotencyKey: $idempotencyKey) {
      datasetId entityType recordCount resolvedEntityCount mergedClusterCount
      reviewCandidateCount runId configId configVersion
    }
  }
`;
export interface ResolveEntitiesResultData { resolveEntities: ResolveEntitiesResult }

export const PROPOSE_ENTITY_MERGE = /* GraphQL */ `
  mutation ProposeEntityMerge($input: ProposeEntityMergeInput!, $idempotencyKey: String!) {
    proposeEntityMerge(input: $input, idempotencyKey: $idempotencyKey) {
      proposalId status executed runId
    }
  }
`;
export interface ProposeEntityMergeResultData { proposeEntityMerge: EntityMergeProposal }

export const MATERIALIZE_RESOLVED = /* GraphQL */ `
  mutation MaterializeResolvedEntities($runId: ID!, $input: MaterializeResolvedInput!, $idempotencyKey: String!) {
    materializeResolvedEntities(runId: $runId, input: $input, idempotencyKey: $idempotencyKey) {
      resolvedDatasetId resolvedDatasetUrn name rowCount columns versionNo icebergTable
    }
  }
`;
export interface MaterializeResolvedResultData { materializeResolvedEntities: MaterializeResolvedResult }

// ---- BRD 23: capability packs (pack-service) -------------------------------
const PACK_FIELDS = /* GraphQL */ `
  name version description publisherName categories regulatory
  components { kind count } deferredKinds
`;
const LEDGER_FIELDS = /* GraphQL */ `
  id kind identity targetUrn targetId origin action detail reversible tombstoned
`;

export const PACKS = /* GraphQL */ `
  query Packs { packs { ${PACK_FIELDS} } }
`;
export interface PacksResult { packs: Pack[] }

export const PACK = /* GraphQL */ `
  query PackDetail($name: String!) {
    pack(name: $name) { ${PACK_FIELDS} deferred { kind reason } }
  }
`;
export interface PackResult { pack: Pack | null }

export const PACK_INSTALLS = /* GraphQL */ `
  query PackInstalls($workspaceId: String) {
    packInstalls(workspaceId: $workspaceId) {
      id pack version workspaceId status summary createdBy createdAt
    }
  }
`;
export interface PackInstallsResult { packInstalls: PackInstall[] }

export const PACK_INSTALL = /* GraphQL */ `
  query PackInstall($id: ID!) {
    packInstall(id: $id) {
      id pack version workspaceId status summary createdBy createdAt
      plan { kind identity name action detail }
      ledger { ${LEDGER_FIELDS} }
    }
  }
`;
export interface PackInstallResult { packInstall: PackInstall | null }

export const PLAN_PACK_INSTALL = /* GraphQL */ `
  mutation PlanPackInstall($pack: String!, $workspaceId: String!, $version: String) {
    planPackInstall(pack: $pack, workspaceId: $workspaceId, version: $version) {
      pack version workspaceId plan { kind identity name action detail }
    }
  }
`;
export interface PlanPackInstallResult { planPackInstall: PackInstallPlan }

export const INSTALL_PACK = /* GraphQL */ `
  mutation InstallPack($pack: String!, $workspaceId: String!, $version: String, $idempotencyKey: String!) {
    installPack(pack: $pack, workspaceId: $workspaceId, version: $version, idempotencyKey: $idempotencyKey) {
      id pack version workspaceId status summary ledger { ${LEDGER_FIELDS} }
    }
  }
`;
export interface InstallPackResult { installPack: PackInstall }

export const UNINSTALL_PACK = /* GraphQL */ `
  mutation UninstallPack($installId: ID!, $idempotencyKey: String!) {
    uninstallPack(installId: $installId, idempotencyKey: $idempotencyKey) {
      id status reversed tombstoned
    }
  }
`;
export interface UninstallPackResult { uninstallPack: PackUninstallResult }

export const COMPLETE_PACK_INSTALL = /* GraphQL */ `
  mutation CompletePackInstall($installId: ID!, $idempotencyKey: String!) {
    completePackInstall(installId: $installId, idempotencyKey: $idempotencyKey) {
      id status dashboards { ${LEDGER_FIELDS} }
    }
  }
`;
export interface CompletePackInstallResult { completePackInstall: PackCompleteResult }

export const DELETE_CONNECTION = /* GraphQL */ `
  mutation DeleteConnection($id: ID!) {
    deleteConnection(id: $id)
  }
`;
export interface DeleteConnectionResult {
  deleteConnection: boolean;
}

export const UPDATE_CONNECTION = /* GraphQL */ `
  mutation UpdateConnection($id: ID!, $input: UpdateConnectionInput!) {
    updateConnection(id: $id, input: $input) { ${CONNECTION_FIELDS} }
  }
`;
export interface UpdateConnectionResult {
  updateConnection: DataConnection;
}
export type { UpdateConnectionInput };

export const CONNECTION_PREVIEW = /* GraphQL */ `
  query ConnectionPreview($id: ID!, $input: ConnectionPreviewInput!) {
    connectionPreview(id: $id, input: $input) { columns rows }
  }
`;
export interface ConnectionPreviewResult {
  connectionPreview: ConnectionPreview;
}
export type { ConnectionPreviewInput };

/* ---------- ingestion runs (ingestion-service) ---------- */
const INGESTION_FIELDS = /* GraphQL */ `
  id urn mode status trigger connectionId datasetUrn fileFormat statement
  rowsAppended bytesReceived bytesTotal attempts createdAt updatedAt
`;

export const INGESTIONS = /* GraphQL */ `
  query Ingestions($first: Int, $after: String, $status: String, $mode: String) {
    ingestions(first: $first, after: $after, status: $status, mode: $mode) {
      nodes { ${INGESTION_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface IngestionsResult {
  ingestions: Connection<Ingestion>;
}

export const INGESTION = /* GraphQL */ `
  query Ingestion($id: ID!) {
    ingestion(id: $id) { ${INGESTION_FIELDS} }
  }
`;
export interface IngestionResult {
  ingestion: Ingestion | null;
}

export const CREATE_INGESTION = /* GraphQL */ `
  mutation CreateIngestion($input: CreateIngestionInput!, $idempotencyKey: String) {
    createIngestion(input: $input, idempotencyKey: $idempotencyKey) { ${INGESTION_FIELDS} }
  }
`;
export interface CreateIngestionResult {
  createIngestion: Ingestion;
}
export type { CreateIngestionInput };

const UPLOAD_FIELDS = /* GraphQL */ `uploadId ingestionId status partSize bytesTotal sha256 expiresAt parts { n etag size }`;

export const CREATE_UPLOAD = /* GraphQL */ `
  mutation CreateUpload($input: CreateUploadInput!, $idempotencyKey: String) {
    createUpload(input: $input, idempotencyKey: $idempotencyKey) { ${UPLOAD_FIELDS} }
  }
`;
export interface CreateUploadResult {
  createUpload: Upload;
}
export type { CreateUploadInput };

export const UPLOAD = /* GraphQL */ `
  query UploadStatus($id: ID!) {
    upload(id: $id) { ${UPLOAD_FIELDS} }
  }
`;
export interface UploadResult {
  upload: Upload | null;
}

export const COMPLETE_UPLOAD = /* GraphQL */ `
  mutation CompleteUpload($uploadId: ID!, $input: CompleteUploadInput!) {
    completeUpload(uploadId: $uploadId, input: $input) { ${INGESTION_FIELDS} }
  }
`;
export interface CompleteUploadResult {
  completeUpload: Ingestion;
}
export type { CompleteUploadInput };

/* ---------- ingestion lifecycle: cancel / retry / reingest ---------- */
export const CANCEL_INGESTION = /* GraphQL */ `
  mutation CancelIngestion($id: ID!) {
    cancelIngestion(id: $id) { ${INGESTION_FIELDS} }
  }
`;
export interface CancelIngestionResult {
  cancelIngestion: Ingestion;
}

export const RETRY_INGESTION = /* GraphQL */ `
  mutation RetryIngestion($id: ID!) {
    retryIngestion(id: $id) { ${INGESTION_FIELDS} }
  }
`;
export interface RetryIngestionResult {
  retryIngestion: Ingestion;
}

export const REINGEST_INGESTION = /* GraphQL */ `
  mutation ReingestIngestion($id: ID!) {
    reingestIngestion(id: $id) { ${INGESTION_FIELDS} }
  }
`;
export interface ReingestIngestionResult {
  reingestIngestion: Ingestion;
}

/* ---------- recurring ingestion schedules (ingestion-service /schedules) ---------- */
const SCHEDULE_FIELDS = /* GraphQL */ `
  id urn connectionId ingestionTemplate cron intervalSeconds timezone watermark
  overlapPolicy enabled workspaceId lastFiredAt nextFireAt createdAt updatedAt
`;

export const INGESTION_SCHEDULES = /* GraphQL */ `
  query IngestionSchedules($first: Int, $after: String) {
    ingestionSchedules(first: $first, after: $after) {
      nodes { ${SCHEDULE_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface IngestionSchedulesResult {
  ingestionSchedules: Connection<IngestionSchedule>;
}

export const CREATE_INGESTION_SCHEDULE = /* GraphQL */ `
  mutation CreateIngestionSchedule($input: CreateIngestionScheduleInput!, $idempotencyKey: String) {
    createIngestionSchedule(input: $input, idempotencyKey: $idempotencyKey) { ${SCHEDULE_FIELDS} }
  }
`;
export interface CreateIngestionScheduleResult {
  createIngestionSchedule: IngestionSchedule;
}
export type { CreateIngestionScheduleInput };

export const UPDATE_INGESTION_SCHEDULE = /* GraphQL */ `
  mutation UpdateIngestionSchedule($id: ID!, $input: UpdateIngestionScheduleInput!) {
    updateIngestionSchedule(id: $id, input: $input) { ${SCHEDULE_FIELDS} }
  }
`;
export interface UpdateIngestionScheduleResult {
  updateIngestionSchedule: IngestionSchedule;
}
export type { UpdateIngestionScheduleInput };

export const DELETE_INGESTION_SCHEDULE = /* GraphQL */ `
  mutation DeleteIngestionSchedule($id: ID!) {
    deleteIngestionSchedule(id: $id)
  }
`;
export interface DeleteIngestionScheduleResult {
  deleteIngestionSchedule: boolean;
}

export const PAUSE_INGESTION_SCHEDULE = /* GraphQL */ `
  mutation PauseIngestionSchedule($id: ID!) {
    pauseIngestionSchedule(id: $id) { ${SCHEDULE_FIELDS} }
  }
`;
export interface PauseIngestionScheduleResult {
  pauseIngestionSchedule: IngestionSchedule;
}

export const RESUME_INGESTION_SCHEDULE = /* GraphQL */ `
  mutation ResumeIngestionSchedule($id: ID!) {
    resumeIngestionSchedule(id: $id) { ${SCHEDULE_FIELDS} }
  }
`;
export interface ResumeIngestionScheduleResult {
  resumeIngestionSchedule: IngestionSchedule;
}

export const RUN_INGESTION_SCHEDULE_NOW = /* GraphQL */ `
  mutation RunIngestionScheduleNow($id: ID!) {
    runIngestionScheduleNow(id: $id) { skipped ingestionId buffered status }
  }
`;
export interface RunIngestionScheduleNowResult {
  runIngestionScheduleNow: ScheduleRunNowResult;
}

/* ---------- dataset lineage (dataset-service) ---------- */
export const DATASET_LINEAGE = /* GraphQL */ `
  query DatasetLineage($urn: String!, $direction: String, $depth: Int) {
    datasetLineage(urn: $urn, direction: $direction, depth: $depth) {
      nodes { urn kind name status }
      edges { fromUrn toUrn activity occurredAt }
      truncated
    }
  }
`;
export interface DatasetLineageResult {
  datasetLineage: DatasetLineage;
}

/* ---------- dataset consumers / versions / similarity / re-profile ---------- */
export const DATASET_CONSUMERS = /* GraphQL */ `
  query DatasetConsumers($id: ID!) {
    datasetConsumers(id: $id) { downstreamEdges byService byActivity truncated }
  }
`;
export interface DatasetConsumersResult {
  datasetConsumers: DatasetConsumers;
}

export const DATASET_VERSIONS = /* GraphQL */ `
  query DatasetVersions($datasetId: ID!, $first: Int, $after: String) {
    datasetVersions(datasetId: $datasetId, first: $first, after: $after) {
      nodes {
        id urn versionNo icebergSnapshotId schema schemaDiff breakingChange
        rowCount bytes producedByUrn profileStatus expired createdAt
      }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface DatasetVersionsResult {
  datasetVersions: Connection<DatasetVersion>;
}

export const SIMILAR_DATASETS = /* GraphQL */ `
  query SimilarDatasets($datasetId: ID!) {
    similarDatasets(datasetId: $datasetId) { id urn name score }
  }
`;
export interface SimilarDatasetsResult {
  similarDatasets: SimilarDataset[];
}

export const CREATE_CASES = /* GraphQL */ `
  mutation CreateCases($input: CreateCasesInput!, $idempotencyKey: String) {
    createCases(input: $input, idempotencyKey: $idempotencyKey) {
      created { id caseNumber status }
      deduplicated { id rowPk caseNumber }
    }
  }
`;

export const DATASET_AGGREGATE = /* GraphQL */ `
  query DatasetAggregate($datasetId: ID!, $dimension: String!, $measure: String, $agg: String!, $limit: Int) {
    datasetAggregate(datasetId: $datasetId, dimension: $dimension, measure: $measure, agg: $agg, limit: $limit) {
      columns
      rows
      sql
    }
  }
`;

export const DATASET_ROWS = /* GraphQL */ `
  query DatasetRows($datasetId: ID!, $offset: Int, $limit: Int, $sort: String, $dir: String, $filters: [RowFilterInput!]) {
    datasetRows(datasetId: $datasetId, offset: $offset, limit: $limit, sort: $sort, dir: $dir, filters: $filters) {
      columns
      rows
      total
      filtered
      offset
      limit
      truncated
    }
  }
`;

export const CHART_DRILL_TARGET = /* GraphQL */ `
  query ChartDrillTarget($chartId: ID!, $dimension: String!) {
    chartDrillTarget(chartId: $chartId, dimension: $dimension) {
      datasetId
      datasetUrn
      column
    }
  }
`;
export interface ChartDrillTargetResult {
  chartDrillTarget: { datasetId: string; datasetUrn: string; column: string } | null;
}

export const REPROFILE_DATASET = /* GraphQL */ `
  mutation ReprofileDataset($id: ID!, $versionNo: Int, $idempotencyKey: String) {
    reprofileDataset(id: $id, versionNo: $versionNo, idempotencyKey: $idempotencyKey) {
      operationId profileId status
    }
  }
`;
export interface ReprofileDatasetResult {
  reprofileDataset: ReprofileResult;
}

/* ---------- queries (query-service) ---------- */
export const SAVED_QUERIES = /* GraphQL */ `
  query SavedQueries($first: Int, $after: String, $workspaceId: ID) {
    savedQueries(first: $first, after: $after, workspaceId: $workspaceId) {
      nodes { id urn name description tags moduleNames versionNo createdAt updatedAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface SavedQueriesResult {
  savedQueries: Connection<SavedQuery>;
}

export const SAVED_QUERY = /* GraphQL */ `
  query SavedQuery($id: ID!) {
    savedQuery(id: $id) {
      id urn name description tags moduleNames sqlText variables versionNo createdAt updatedAt
    }
  }
`;
export interface SavedQueryResult {
  savedQuery: SavedQuery | null;
}

const QUERY_RESULT_FIELDS = /* GraphQL */ `
  executionId status engine cacheHit durationMs resultRows scanBytes
  columns { name type }
  rows hasMore warnings error
`;

export const RUN_SQL = /* GraphQL */ `
  mutation RunSql($input: RunSqlInput!) {
    runSql(input: $input) { ${QUERY_RESULT_FIELDS} }
  }
`;
export interface RunSqlResult {
  runSql: QueryResult;
}
export type { RunSqlInput };

export const RUN_SAVED_QUERY = /* GraphQL */ `
  mutation RunSavedQuery($id: ID!, $limit: Int) {
    runSavedQuery(id: $id, limit: $limit) { ${QUERY_RESULT_FIELDS} }
  }
`;
export interface RunSavedQueryResult {
  runSavedQuery: QueryResult;
}

/* ---------- saved-query authoring + versions (query-service) ---------- */
const SAVED_QUERY_FIELDS = /* GraphQL */ `
  id urn name description tags moduleNames sqlText variables versionNo createdAt updatedAt
`;

export const CREATE_SAVED_QUERY = /* GraphQL */ `
  mutation CreateSavedQuery($input: SavedQueryInput!, $idempotencyKey: String) {
    createSavedQuery(input: $input, idempotencyKey: $idempotencyKey) { ${SAVED_QUERY_FIELDS} }
  }
`;
export interface CreateSavedQueryResult {
  createSavedQuery: SavedQuery;
}
export type { SavedQueryInput };

export const UPDATE_SAVED_QUERY = /* GraphQL */ `
  mutation UpdateSavedQuery($id: ID!, $input: SavedQueryInput!) {
    updateSavedQuery(id: $id, input: $input) { ${SAVED_QUERY_FIELDS} }
  }
`;
export interface UpdateSavedQueryResult {
  updateSavedQuery: SavedQuery;
}

export const DELETE_SAVED_QUERY = /* GraphQL */ `
  mutation DeleteSavedQuery($id: ID!) {
    deleteSavedQuery(id: $id)
  }
`;
export interface DeleteSavedQueryResult {
  deleteSavedQuery: boolean;
}

export const SAVED_QUERY_VERSIONS = /* GraphQL */ `
  query SavedQueryVersions($queryId: ID!, $first: Int, $after: String) {
    savedQueryVersions(queryId: $queryId, first: $first, after: $after) {
      nodes { id versionNo sqlText variables datasetRefs createdBy createdAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface SavedQueryVersionsResult {
  savedQueryVersions: Connection<SavedQueryVersion>;
}

/* ---------- query execution history (query-service) ---------- */
const QUERY_EXECUTION_FIELDS = /* GraphQL */ `
  id urn status engine cacheHit savedQueryId queryVersionNo createdBy createdAt
  startedAt finishedAt durationMs resultRows scanBytes queuePosition error
`;

export const QUERY_EXECUTIONS = /* GraphQL */ `
  query QueryExecutions($first: Int, $after: String, $status: String, $savedQueryId: ID) {
    queryExecutions(first: $first, after: $after, status: $status, savedQueryId: $savedQueryId) {
      nodes { ${QUERY_EXECUTION_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface QueryExecutionsResult {
  queryExecutions: Connection<QueryExecution>;
}

export const QUERY_EXECUTION = /* GraphQL */ `
  query QueryExecutionDetail($id: ID!) {
    queryExecution(id: $id) { ${QUERY_EXECUTION_FIELDS} sqlText }
  }
`;
export interface QueryExecutionDetailResult {
  queryExecution: QueryExecution | null;
}

export const CANCEL_QUERY_EXECUTION = /* GraphQL */ `
  mutation CancelQueryExecution($id: ID!) {
    cancelQueryExecution(id: $id) { ${QUERY_EXECUTION_FIELDS} }
  }
`;
export interface CancelQueryExecutionResult {
  cancelQueryExecution: QueryExecution;
}

export const QUERY_STATS = /* GraphQL */ `
  query QueryStats($since: DateTime, $limit: Int) {
    queryStats(since: $since, limit: $limit) {
      since
      topQueries { sqlFingerprint executions totalScanBytes failures topUser }
    }
  }
`;
export interface QueryStatsResult {
  queryStats: QueryStats;
}

/* ---------- pipelines: step catalog, templates, runs (no-code builder) ---------- */
const STEP_PARAM_FIELDS = /* GraphQL */ `
  name type required default enumValues min max help format itemFormat
`;

export const PIPELINE_STEP_TYPES = /* GraphQL */ `
  query PipelineStepTypes {
    pipelineStepTypes {
      name displayName category description
      minInputs maxInputs maxOutputs
      outputs { name type }
      parameters { ${STEP_PARAM_FIELDS} }
    }
  }
`;
export interface PipelineStepTypesResult {
  pipelineStepTypes: PipelineStepType[];
}

export const ALGORITHM_TEMPLATES = /* GraphQL */ `
  query AlgorithmTemplates {
    algorithmTemplates {
      name displayName family modes
      parameters { ${STEP_PARAM_FIELDS} }
    }
  }
`;
export interface AlgorithmTemplatesResult {
  algorithmTemplates: AlgorithmTemplate[];
}

const PIPELINE_TEMPLATE_FIELDS = /* GraphQL */ `
  id urn name pipelineType activeVersionId definition
  validationStatus isSystem archived createdBy createdAt updatedAt
`;

export const PIPELINE_TEMPLATES = /* GraphQL */ `
  query PipelineTemplates($first: Int, $after: String, $q: String, $pipelineType: String, $includeArchived: Boolean) {
    pipelineTemplates(first: $first, after: $after, q: $q, pipelineType: $pipelineType, includeArchived: $includeArchived) {
      nodes { ${PIPELINE_TEMPLATE_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface PipelineTemplatesResult {
  pipelineTemplates: Connection<PipelineTemplate>;
}

export const PIPELINE_TEMPLATE = /* GraphQL */ `
  query PipelineTemplate($id: ID!) {
    pipelineTemplate(id: $id) { ${PIPELINE_TEMPLATE_FIELDS} }
  }
`;
export interface PipelineTemplateResult {
  pipelineTemplate: PipelineTemplate | null;
}

export const PIPELINE_RUNS = /* GraphQL */ `
  query PipelineRuns($first: Int, $after: String, $templateId: ID, $status: String) {
    pipelineRuns(first: $first, after: $after, templateId: $templateId, status: $status) {
      nodes { id urn templateId status createdAt startedAt finishedAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface PipelineRunsResult {
  pipelineRuns: Connection<PipelineRun>;
}

export const CREATE_PIPELINE = /* GraphQL */ `
  mutation CreatePipeline($input: CreatePipelineInput!, $idempotencyKey: String!) {
    createPipeline(input: $input, idempotencyKey: $idempotencyKey) { ${PIPELINE_TEMPLATE_FIELDS} }
  }
`;
export interface CreatePipelineResult {
  createPipeline: PipelineTemplate;
}
export type { CreatePipelineInput };

export const UPDATE_PIPELINE = /* GraphQL */ `
  mutation UpdatePipeline($id: ID!, $input: UpdatePipelineInput!, $idempotencyKey: String!) {
    updatePipeline(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${PIPELINE_TEMPLATE_FIELDS} }
  }
`;
export interface UpdatePipelineResult {
  updatePipeline: PipelineTemplate;
}
export type { UpdatePipelineInput };

export const VALIDATE_PIPELINE = /* GraphQL */ `
  mutation ValidatePipeline($definition: JSON!, $pipelineType: String!) {
    validatePipeline(definition: $definition, pipelineType: $pipelineType) {
      valid
      issues { code message node }
    }
  }
`;
export interface ValidatePipelineResult {
  validatePipeline: ValidationResult;
}

export const RUN_PIPELINE = /* GraphQL */ `
  mutation RunPipeline($id: ID!, $input: RunPipelineInput!, $idempotencyKey: String!) {
    runPipeline(id: $id, input: $input, idempotencyKey: $idempotencyKey) {
      id urn templateId status createdAt startedAt finishedAt
    }
  }
`;
export interface RunPipelineResult {
  runPipeline: PipelineRun;
}
export type { RunPipelineInput };

/* ---------- pipeline run lifecycle: terminate / retry / manifest ---------- */
const PIPELINE_RUN_FIELDS = /* GraphQL */ `
  id urn templateId status error retriedFromRunId createdAt startedAt finishedAt
`;

export const TERMINATE_PIPELINE_RUN = /* GraphQL */ `
  mutation TerminatePipelineRun($id: ID!) {
    terminatePipelineRun(id: $id) { ${PIPELINE_RUN_FIELDS} }
  }
`;
export interface TerminatePipelineRunResult {
  terminatePipelineRun: PipelineRun;
}

export const RETRY_PIPELINE_RUN = /* GraphQL */ `
  mutation RetryPipelineRun($id: ID!, $idempotencyKey: String) {
    retryPipelineRun(id: $id, idempotencyKey: $idempotencyKey) { ${PIPELINE_RUN_FIELDS} }
  }
`;
export interface RetryPipelineRunResult {
  retryPipelineRun: PipelineRun;
}

export const PIPELINE_RUN_MANIFEST = /* GraphQL */ `
  query PipelineRunManifest($id: ID!) {
    pipelineRunManifest(id: $id) { runId manifest resolvedParameters }
  }
`;
export interface PipelineRunManifestResult {
  pipelineRunManifest: PipelineRunManifest;
}

/* ---------- pipeline template lifecycle ---------- */
export const PIPELINE_TEMPLATE_VERSIONS = /* GraphQL */ `
  query PipelineTemplateVersions($templateId: ID!, $first: Int, $after: String) {
    pipelineTemplateVersions(templateId: $templateId, first: $first, after: $after) {
      nodes {
        id templateId versionNo validationStatus validationReport
        manifestDigest argoTemplateName createdAt
      }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface PipelineTemplateVersionsResult {
  pipelineTemplateVersions: Connection<PipelineTemplateVersion>;
}

export const CLONE_PIPELINE_TEMPLATE = /* GraphQL */ `
  mutation ClonePipelineTemplate($id: ID!, $idempotencyKey: String) {
    clonePipelineTemplate(id: $id, idempotencyKey: $idempotencyKey) { ${PIPELINE_TEMPLATE_FIELDS} }
  }
`;
export interface ClonePipelineTemplateResult {
  clonePipelineTemplate: PipelineTemplate;
}

export const ACTIVATE_PIPELINE_TEMPLATE_VERSION = /* GraphQL */ `
  mutation ActivatePipelineTemplateVersion($templateId: ID!, $versionId: ID!) {
    activatePipelineTemplateVersion(templateId: $templateId, versionId: $versionId) { ${PIPELINE_TEMPLATE_FIELDS} }
  }
`;
export interface ActivatePipelineTemplateVersionResult {
  activatePipelineTemplateVersion: PipelineTemplate;
}

export const COMPILE_PIPELINE_TEMPLATE = /* GraphQL */ `
  mutation CompilePipelineTemplate($id: ID!) {
    compilePipelineTemplate(id: $id) {
      templateId versionId manifestDigest argoTemplateName manifest
    }
  }
`;
export interface CompilePipelineTemplateResult {
  compilePipelineTemplate: CompiledPipelineManifest;
}

export const DELETE_PIPELINE_TEMPLATE = /* GraphQL */ `
  mutation DeletePipelineTemplate($id: ID!) {
    deletePipelineTemplate(id: $id) { ${PIPELINE_TEMPLATE_FIELDS} }
  }
`;
export interface DeletePipelineTemplateResult {
  deletePipelineTemplate: PipelineTemplate;
}

export const RESTORE_PIPELINE_TEMPLATE = /* GraphQL */ `
  mutation RestorePipelineTemplate($id: ID!) {
    restorePipelineTemplate(id: $id) { ${PIPELINE_TEMPLATE_FIELDS} }
  }
`;
export interface RestorePipelineTemplateResult {
  restorePipelineTemplate: PipelineTemplate;
}

/* ---------- recurring pipeline schedules (PIPE-FR-050) ---------- */
const PIPELINE_SCHEDULE_FIELDS = /* GraphQL */ `
  id urn scheduleId templateId name cron timezone runParameters enabled
  nextFireAt lastFireAt lastRunId createdAt
`;

export const PIPELINE_SCHEDULES = /* GraphQL */ `
  query PipelineSchedules {
    pipelineSchedules { ${PIPELINE_SCHEDULE_FIELDS} }
  }
`;
export interface PipelineSchedulesResult {
  pipelineSchedules: PipelineSchedule[];
}

export const CREATE_PIPELINE_SCHEDULE = /* GraphQL */ `
  mutation CreatePipelineSchedule($input: CreatePipelineScheduleInput!, $idempotencyKey: String) {
    createPipelineSchedule(input: $input, idempotencyKey: $idempotencyKey) { ${PIPELINE_SCHEDULE_FIELDS} }
  }
`;
export interface CreatePipelineScheduleResult {
  createPipelineSchedule: PipelineSchedule;
}
export type { CreatePipelineScheduleInput };

export const PAUSE_PIPELINE_SCHEDULE = /* GraphQL */ `
  mutation PausePipelineSchedule($id: ID!) {
    pausePipelineSchedule(id: $id) { ${PIPELINE_SCHEDULE_FIELDS} }
  }
`;
export interface PausePipelineScheduleResult {
  pausePipelineSchedule: PipelineSchedule;
}

export const RESUME_PIPELINE_SCHEDULE = /* GraphQL */ `
  mutation ResumePipelineSchedule($id: ID!) {
    resumePipelineSchedule(id: $id) { ${PIPELINE_SCHEDULE_FIELDS} }
  }
`;
export interface ResumePipelineScheduleResult {
  resumePipelineSchedule: PipelineSchedule;
}

export const RUN_NOW_PIPELINE_SCHEDULE = /* GraphQL */ `
  mutation RunNowPipelineSchedule($id: ID!) {
    runNowPipelineSchedule(id: $id) {
      id urn templateId status createdAt startedAt finishedAt
    }
  }
`;
export interface RunNowPipelineScheduleResult {
  runNowPipelineSchedule: PipelineRun;
}

export const DELETE_PIPELINE_SCHEDULE = /* GraphQL */ `
  mutation DeletePipelineSchedule($id: ID!) {
    deletePipelineSchedule(id: $id)
  }
`;
export interface DeletePipelineScheduleResult {
  deletePipelineSchedule: boolean;
}

export const CASE_SEARCH = /* GraphQL */ `
  query CaseSearch($q: String, $filter: CaseFilter, $first: Int, $after: String) {
    caseSearch(q: $q, filter: $filter, first: $first, after: $after) {
      nodes {
        id urn caseNumber title status severity dueDate createdAt displayProjection
        assignee { id email fullName }
        sourceDataset { id name }
      }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface CaseSearchResult {
  caseSearch: Connection<Case>;
}

export const CASE_DETAIL = /* GraphQL */ `
  query CaseDetail($id: ID!) {
    case(id: $id) {
      id urn caseNumber title status severity dueDate createdAt displayProjection
      description dispositionId resolutionNote resolvedAt closedAt caseVersion reassignCount
      assignee { id email fullName }
      sourceDataset { id urn name status rowCount }
      proposals {
        id urn agentKey tool riskTier rationale predictedEffect status affectedUrns argsDiff createdAt
      }
      evidence { id caseId filename contentType sizeBytes uploadedBy createdAt }
    }
  }
`;
export interface CaseDetailResult {
  case: Case | null;
}

export const UPDATE_CASE = /* GraphQL */ `
  mutation UpdateCase($id: ID!, $patch: CasePatchInput!, $idempotencyKey: String!) {
    updateCase(id: $id, patch: $patch, idempotencyKey: $idempotencyKey) {
      id urn caseNumber title status severity dueDate
    }
  }
`;
export interface UpdateCaseResult {
  updateCase: Case;
}

export const BULK_ASSIGN_CASES = /* GraphQL */ `
  mutation BulkAssignCases($caseIds: [ID!]!, $assigneeId: ID!, $idempotencyKey: String) {
    bulkAssignCases(caseIds: $caseIds, assigneeId: $assigneeId, idempotencyKey: $idempotencyKey) {
      succeededIds
      failed { caseId code message }
    }
  }
`;
export interface BulkAssignCasesResult {
  bulkAssignCases: { succeededIds: string[]; failed: { caseId: string; code: string; message: string }[] };
}

/* ---- Tier 4b: case ops (lifecycle, comments/timeline, export, catalog) ---- */

/** The caseView fields every lifecycle mutation returns — one shared shape so
 * the detail cache can be patched from any transition response. */
const CASE_LIFECYCLE_FIELDS = /* GraphQL */ `
  id urn caseNumber title status severity dueDate createdAt
  description dispositionId resolutionNote resolvedAt closedAt caseVersion reassignCount
  assignee { id email fullName }
`;

export const ASSIGN_CASE = /* GraphQL */ `
  mutation AssignCase($id: ID!, $assigneeId: ID!, $idempotencyKey: String) {
    assignCase(id: $id, assigneeId: $assigneeId, idempotencyKey: $idempotencyKey) { ${CASE_LIFECYCLE_FIELDS} }
  }
`;
export interface AssignCaseResult {
  assignCase: Case;
}

export const UNASSIGN_CASE = /* GraphQL */ `
  mutation UnassignCase($id: ID!) {
    unassignCase(id: $id) { ${CASE_LIFECYCLE_FIELDS} }
  }
`;
export interface UnassignCaseResult {
  unassignCase: Case;
}

export const START_CASE = /* GraphQL */ `
  mutation StartCase($id: ID!) {
    startCase(id: $id) { ${CASE_LIFECYCLE_FIELDS} }
  }
`;
export interface StartCaseResult {
  startCase: Case;
}

export const RESOLVE_CASE = /* GraphQL */ `
  mutation ResolveCase($id: ID!, $dispositionId: ID!, $resolutionNote: String, $idempotencyKey: String) {
    resolveCase(id: $id, dispositionId: $dispositionId, resolutionNote: $resolutionNote, idempotencyKey: $idempotencyKey) {
      ${CASE_LIFECYCLE_FIELDS}
    }
  }
`;
export interface ResolveCaseResult {
  resolveCase: Case;
}

export const REOPEN_CASE = /* GraphQL */ `
  mutation ReopenCase($id: ID!) {
    reopenCase(id: $id) { ${CASE_LIFECYCLE_FIELDS} }
  }
`;
export interface ReopenCaseResult {
  reopenCase: Case;
}

export const CLOSE_CASE = /* GraphQL */ `
  mutation CloseCase($id: ID!) {
    closeCase(id: $id) { ${CASE_LIFECYCLE_FIELDS} }
  }
`;
export interface CloseCaseResult {
  closeCase: Case;
}

export const ESCALATE_CASE = /* GraphQL */ `
  mutation EscalateCase($id: ID!, $to: String, $reason: String) {
    escalateCase(id: $id, to: $to, reason: $reason) { ${CASE_LIFECYCLE_FIELDS} }
  }
`;
export interface EscalateCaseResult {
  escalateCase: Case;
}

export const ADD_CASE_COMMENT = /* GraphQL */ `
  mutation AddCaseComment($caseId: ID!, $body: String!, $idempotencyKey: String) {
    addCaseComment(caseId: $caseId, body: $body, idempotencyKey: $idempotencyKey) {
      id caseId authorId body editedAt createdAt
    }
  }
`;
export interface AddCaseCommentResult {
  addCaseComment: CaseComment;
}

export const UPDATE_CASE_COMMENT = /* GraphQL */ `
  mutation UpdateCaseComment($id: ID!, $body: String!) {
    updateCaseComment(id: $id, body: $body) { id body }
  }
`;
export interface UpdateCaseCommentResult {
  updateCaseComment: CaseComment;
}

export const DELETE_CASE_COMMENT = /* GraphQL */ `
  mutation DeleteCaseComment($id: ID!) {
    deleteCaseComment(id: $id)
  }
`;
export interface DeleteCaseCommentResult {
  deleteCaseComment: boolean;
}

export const CASE_TIMELINE = /* GraphQL */ `
  query CaseTimeline($caseId: ID!, $first: Int, $after: String) {
    caseTimeline(caseId: $caseId, first: $first, after: $after) {
      nodes {
        id caseId eventType actorType actorId
        actor { id email fullName }
        viaAgent proposalUrn oldValue newValue occurredAt
      }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface CaseTimelineResult {
  caseTimeline: Connection<CaseActivity>;
}

export const EXPORT_CASES = /* GraphQL */ `
  mutation ExportCases($filter: JSON, $format: String) {
    exportCases(filter: $filter, format: $format) {
      id kind status succeeded failed total rowCount downloadUrl expiresAt error
    }
  }
`;
export interface ExportCasesResult {
  exportCases: CaseOperation;
}

export const CASE_OPERATION = /* GraphQL */ `
  query CaseOperation($id: ID!) {
    caseOperation(id: $id) {
      id kind status succeeded failed total rowCount downloadUrl expiresAt error
    }
  }
`;
export interface CaseOperationResult {
  caseOperation: CaseOperation | null;
}

const DISPOSITION_FIELDS = /* GraphQL */ `
  id urn workspaceId code label category requiresNote active createdAt updatedAt
`;

export const DISPOSITIONS = /* GraphQL */ `
  query Dispositions {
    dispositions { ${DISPOSITION_FIELDS} }
  }
`;
export interface DispositionsResult {
  dispositions: Disposition[];
}

export const LEARNING_LOOP = /* GraphQL */ `
  query LearningLoop {
    learningLoop {
      transcriptsCaptured correctionsCaptured datasetCount
      latestDatasetAgentKey latestDatasetVersion latestDatasetExamples latestDatasetAt
      capped
    }
  }
`;
export interface LearningLoopResult {
  learningLoop: {
    transcriptsCaptured: number;
    correctionsCaptured: number;
    datasetCount: number;
    latestDatasetAgentKey?: string | null;
    latestDatasetVersion?: number | null;
    latestDatasetExamples?: number | null;
    latestDatasetAt?: string | null;
    capped: boolean;
  };
}

export const CREATE_DISPOSITION = /* GraphQL */ `
  mutation CreateDisposition($input: CreateDispositionInput!, $idempotencyKey: String) {
    createDisposition(input: $input, idempotencyKey: $idempotencyKey) { ${DISPOSITION_FIELDS} }
  }
`;
export interface CreateDispositionResult {
  createDisposition: Disposition;
}

export const UPDATE_DISPOSITION = /* GraphQL */ `
  mutation UpdateDisposition($id: ID!, $input: UpdateDispositionInput!) {
    updateDisposition(id: $id, input: $input) { ${DISPOSITION_FIELDS} }
  }
`;
export interface UpdateDispositionResult {
  updateDisposition: Disposition;
}

const CASE_FIELD_FIELDS = /* GraphQL */ `
  id urn workspaceId queryUrn name dataType purpose fieldMeta createdAt updatedAt
`;

export const CASE_FIELDS = /* GraphQL */ `
  query CaseFields($queryUrn: String) {
    caseFields(queryUrn: $queryUrn) { ${CASE_FIELD_FIELDS} }
  }
`;
export interface CaseFieldsResult {
  caseFields: CaseField[];
}

export const CREATE_CASE_FIELD = /* GraphQL */ `
  mutation CreateCaseField($input: CreateCaseFieldInput!, $idempotencyKey: String) {
    createCaseField(input: $input, idempotencyKey: $idempotencyKey) { ${CASE_FIELD_FIELDS} }
  }
`;
export interface CreateCaseFieldResult {
  createCaseField: CaseField;
}

export const UPDATE_CASE_FIELD = /* GraphQL */ `
  mutation UpdateCaseField($input: UpdateCaseFieldInput!) {
    updateCaseField(input: $input) { ${CASE_FIELD_FIELDS} }
  }
`;
export interface UpdateCaseFieldResult {
  updateCaseField: CaseField;
}

export const DELETE_CASE_FIELD = /* GraphQL */ `
  mutation DeleteCaseField($id: ID!, $orphan: Boolean) {
    deleteCaseField(id: $id, orphan: $orphan)
  }
`;
export interface DeleteCaseFieldResult {
  deleteCaseField: boolean;
}

export const PUT_CASE_SLA_POLICY = /* GraphQL */ `
  mutation PutCaseSlaPolicy($input: CaseSlaPolicyInput!) {
    putCaseSlaPolicy(input: $input) {
      workspaceId warnBeforeSeconds onBreach maxReassignCount
    }
  }
`;
export interface PutCaseSlaPolicyResult {
  putCaseSlaPolicy: CaseSlaPolicy;
}

export const PROPOSALS_INBOX = /* GraphQL */ `
  query ProposalsInbox($status: ProposalStatus, $agentKey: String, $first: Int, $after: String) {
    proposalsInbox(status: $status, agentKey: $agentKey, first: $first, after: $after) {
      nodes {
        id urn agentKey tool riskTier rationale predictedEffect status affectedUrns argsDiff createdAt
      }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ProposalsInboxResult {
  proposalsInbox: Connection<Proposal>;
}

export const PROPOSAL = /* GraphQL */ `
  query ProposalDetail($id: ID!) {
    proposal(id: $id) {
      id urn agentKey tool riskTier rationale predictedEffect status affectedUrns argsDiff decision createdAt
    }
  }
`;
export interface ProposalResult {
  proposal: Proposal | null;
}

export const DECIDE_PROPOSAL = /* GraphQL */ `
  mutation DecideProposal($id: ID!, $decision: DecisionInput!, $idempotencyKey: String!) {
    decideProposal(id: $id, decision: $decision, idempotencyKey: $idempotencyKey) {
      id urn status decision
    }
  }
`;
export interface DecideProposalResult {
  decideProposal: Proposal;
}

export const AGENT_RUN = /* GraphQL */ `
  query AgentRunDetail($id: ID!) {
    agentRun(id: $id) {
      id urn agentKey status costUsd
      tokenUsage { inputTokens outputTokens }
      trace
      tokenStream { hubUrl topics }
    }
  }
`;
export interface AgentRunResult {
  agentRun: AgentRun | null;
}

export const EXPERIMENTS = /* GraphQL */ `
  query Experiments($first: Int, $after: String) {
    experiments(first: $first, after: $after) {
      nodes { id urn name description }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ExperimentsResult {
  experiments: Connection<Experiment>;
}

export const ARCHIVED_EXPERIMENTS = /* GraphQL */ `
  query ArchivedExperiments($first: Int, $after: String, $workspaceId: String) {
    archivedExperiments(first: $first, after: $after, workspaceId: $workspaceId) {
      nodes { id urn name description archived }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ArchivedExperimentsResult {
  archivedExperiments: Connection<Experiment>;
}

export const ARCHIVE_EXPERIMENT = /* GraphQL */ `
  mutation ArchiveExperiment($id: ID!) {
    archiveExperiment(id: $id) { id urn name description archived }
  }
`;
export interface ArchiveExperimentResult {
  archiveExperiment: Experiment;
}

export const RESTORE_EXPERIMENT = /* GraphQL */ `
  mutation RestoreExperiment($id: ID!) {
    restoreExperiment(id: $id) { id urn name description archived }
  }
`;
export interface RestoreExperimentResult {
  restoreExperiment: Experiment;
}

// NB: the runs-LIST payload (experiment-service GET /runs) does not carry
// metrics/params — only the run DETAIL does. Selecting them here would render
// permanently empty columns, so the list sticks to fields the backend serves;
// /ml/runs/:id (RUN below) hydrates metrics/params for one run.
export const EXPERIMENT = /* GraphQL */ `
  query ExperimentDetail($id: ID!) {
    experiment(id: $id) {
      id urn name description
      runs {
        nodes { id urn name status model { id name stage } }
        pageInfo { nextCursor hasMore }
      }
    }
  }
`;
export interface ExperimentResult {
  experiment: Experiment | null;
}

export const RUN = /* GraphQL */ `
  query RunDetail($id: ID!) {
    run(id: $id) {
      id urn name status metrics params experimentId model { id urn name stage }
    }
  }
`;
export interface RunResult {
  run: Run | null;
}

/* ---------- ml: model registry (list + detail with versions/stages) ---------- */
export const MODELS = /* GraphQL */ `
  query Models($first: Int, $after: String, $stage: String) {
    models(first: $first, after: $after, stage: $stage) {
      nodes { id urn name modelType ownerId description createdAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ModelsResult {
  models: Connection<Model>;
}

export const MODEL = /* GraphQL */ `
  query ModelDetail($id: ID!) {
    model(id: $id) {
      id urn name modelType ownerId description createdAt
      versions {
        modelId version urn stage sourceRunId flavor mlflowModelRef stageUpdatedAt
      }
    }
  }
`;
export interface ModelResult {
  model: Model | null;
}

export const PROMOTE_MODEL_VERSION = /* GraphQL */ `
  mutation PromoteModelVersion(
    $modelId: ID!, $version: Int!, $targetStage: String!, $rationale: String, $idempotencyKey: String
  ) {
    promoteModelVersion(
      modelId: $modelId, version: $version, targetStage: $targetStage,
      rationale: $rationale, idempotencyKey: $idempotencyKey
    ) {
      promotionId status operationId
    }
  }
`;
export interface PromoteModelVersionResult {
  promoteModelVersion: PromotionRequest;
}

export const DECIDE_PROMOTION = /* GraphQL */ `
  mutation DecidePromotion($promotionId: ID!, $decision: String!, $message: String) {
    decidePromotion(promotionId: $promotionId, decision: $decision, message: $message)
  }
`;
export interface DecidePromotionResult {
  decidePromotion: JSONValue;
}

export const PROMOTIONS = /* GraphQL */ `
  query Promotions($modelId: ID!, $version: Int!, $first: Int, $after: String) {
    promotions(modelId: $modelId, version: $version, first: $first, after: $after) {
      nodes { id urn modelVersionId targetStage fromStage status rationale requestedBy viaAgent decision createdAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface PromotionsResult {
  promotions: Connection<Promotion>;
}

/* ---------- ml: batch inference jobs ---------- */
const INFERENCE_JOB_FIELDS = /* GraphQL */ `
  id urn name description status
  model { urn name version stageAtSubmit }
  inputDataset { urn version }
  outputDataset { urn version }
  rowCount error pipelineRunUrn scheduleId
  createdAt submittedAt startedAt finishedAt
`;

export const INFERENCE_JOBS = /* GraphQL */ `
  query InferenceJobs($first: Int, $after: String, $status: String) {
    inferenceJobs(first: $first, after: $after, status: $status) {
      nodes { ${INFERENCE_JOB_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface InferenceJobsResult {
  inferenceJobs: Connection<InferenceJob>;
}

export const INFERENCE_JOB = /* GraphQL */ `
  query InferenceJobDetail($id: ID!) {
    inferenceJob(id: $id) { ${INFERENCE_JOB_FIELDS} }
  }
`;
export interface InferenceJobResult {
  inferenceJob: InferenceJob | null;
}

export const CREATE_INFERENCE_JOB = /* GraphQL */ `
  mutation CreateInferenceJob($input: CreateInferenceJobInput!, $idempotencyKey: String) {
    createInferenceJob(input: $input, idempotencyKey: $idempotencyKey) { ${INFERENCE_JOB_FIELDS} }
  }
`;
export interface CreateInferenceJobResult {
  createInferenceJob: InferenceJob;
}

export const CREATE_EXPERIMENT = /* GraphQL */ `
  mutation CreateExperiment($input: CreateExperimentInput!, $idempotencyKey: String) {
    createExperiment(input: $input, idempotencyKey: $idempotencyKey) {
      id urn name description
    }
  }
`;
export interface CreateExperimentResult {
  createExperiment: Experiment;
}
export type {
  Model,
  ModelVersion,
  InferenceJob,
  PromotionRequest,
  CreateExperimentInput,
  CreateInferenceJobInput,
};

/* ---- Tier 4b: ml ops (experiment-service run tooling: register / best-run /
 * compare / notes / artifacts / metric history / model cards) ---- */

export const REGISTER_RUN_AS_MODEL = /* GraphQL */ `
  mutation RegisterRunAsModel($experimentId: ID!, $runId: ID!, $input: RegisterRunInput!, $idempotencyKey: String) {
    registerRunAsModel(experimentId: $experimentId, runId: $runId, input: $input, idempotencyKey: $idempotencyKey) {
      modelId version stage modelCreated
    }
  }
`;
export interface RegisterRunAsModelResult {
  registerRunAsModel: RegisterModelResult;
}

export const UPDATE_EXPERIMENT = /* GraphQL */ `
  mutation UpdateExperiment($id: ID!, $input: UpdateExperimentInput!) {
    updateExperiment(id: $id, input: $input) { id urn name description archived }
  }
`;
export interface UpdateExperimentResult {
  updateExperiment: Experiment;
}

/** direction is max|min; the bff folds the payload's {metric: float} map into
 * the Run's own metrics field. 404 (no run carries the metric) → null. */
export const BEST_RUN = /* GraphQL */ `
  query BestRun($experimentId: ID!, $metric: String!, $direction: String, $status: String) {
    bestRun(experimentId: $experimentId, metric: $metric, direction: $direction, status: $status) {
      id urn name status metrics
    }
  }
`;
export interface BestRunResult {
  bestRun: Run | null;
}

export const COMPARE_RUNS = /* GraphQL */ `
  query CompareRuns($runIds: [ID!]!, $metrics: [String!], $params: [String!], $includeAll: Boolean) {
    compareRuns(runIds: $runIds, metrics: $metrics, params: $params, includeAll: $includeAll) {
      runIds metrics params
    }
  }
`;
export interface CompareRunsResult {
  compareRuns: RunComparison;
}

export const RUN_NOTE = /* GraphQL */ `
  query RunNote($runId: ID!) {
    runNote(runId: $runId) { runId description }
  }
`;
export interface RunNoteResult {
  runNote: RunNote | null;
}

export const UPSERT_RUN_NOTE = /* GraphQL */ `
  mutation UpsertRunNote($runId: ID!, $description: String!) {
    upsertRunNote(runId: $runId, description: $description) { runId description }
  }
`;
export interface UpsertRunNoteResult {
  upsertRunNote: RunNote;
}

export const DELETE_RUN_NOTE = /* GraphQL */ `
  mutation DeleteRunNote($runId: ID!) {
    deleteRunNote(runId: $runId)
  }
`;
export interface DeleteRunNoteResult {
  deleteRunNote: boolean;
}

export const RUN_ARTIFACTS = /* GraphQL */ `
  query RunArtifacts($runId: ID!) {
    runArtifacts(runId: $runId) { path sizeBytes contentType }
  }
`;
export interface RunArtifactsResult {
  runArtifacts: RunArtifact[];
}

/** A short-lived REAL signed url, fetched per click (never cached). */
export const RUN_ARTIFACT_URL = /* GraphQL */ `
  query RunArtifactUrl($runId: ID!, $path: String!) {
    runArtifactUrl(runId: $runId, path: $path)
  }
`;
export interface RunArtifactUrlResult {
  runArtifactUrl: string;
}

/** Rows pass through verbatim: [{key, step, value, logged_at}]. */
export const RUN_METRIC_HISTORY = /* GraphQL */ `
  query RunMetricHistory($runId: ID!, $keys: [String!]) {
    runMetricHistory(runId: $runId, keys: $keys)
  }
`;
export interface RunMetricHistoryResult {
  runMetricHistory: JSONValue;
}

/** The MERGED card (auto fields + overlay) verbatim as JSON; 404 → null. */
export const MODEL_CARD = /* GraphQL */ `
  query ModelCard($modelId: ID!, $version: Int!) {
    modelCard(modelId: $modelId, version: $version)
  }
`;
export interface ModelCardResult {
  modelCard: JSONValue | null;
}

export const UPDATE_MODEL_CARD = /* GraphQL */ `
  mutation UpdateModelCard($modelId: ID!, $version: Int!, $input: ModelCardOverlayInput!) {
    updateModelCard(modelId: $modelId, version: $version, input: $input)
  }
`;
export interface UpdateModelCardResult {
  updateModelCard: JSONValue;
}

/* ---- Tier 4b: ml ops (inference-service job lifecycle + validate +
 * scoring schedules) ---- */

export const CANCEL_INFERENCE_JOB = /* GraphQL */ `
  mutation CancelInferenceJob($id: ID!) {
    cancelInferenceJob(id: $id) { ${INFERENCE_JOB_FIELDS} }
  }
`;
export interface CancelInferenceJobResult {
  cancelInferenceJob: InferenceJob;
}

/** Returns the NEW job (retriedFromJobId points back at the source). */
export const RETRY_INFERENCE_JOB = /* GraphQL */ `
  mutation RetryInferenceJob($id: ID!, $idempotencyKey: String) {
    retryInferenceJob(id: $id, idempotencyKey: $idempotencyKey) { ${INFERENCE_JOB_FIELDS} }
  }
`;
export interface RetryInferenceJobResult {
  retryInferenceJob: InferenceJob;
}

export const DELETE_INFERENCE_JOB = /* GraphQL */ `
  mutation DeleteInferenceJob($id: ID!) {
    deleteInferenceJob(id: $id)
  }
`;
export interface DeleteInferenceJobResult {
  deleteInferenceJob: boolean;
}

export const VALIDATE_INFERENCE = /* GraphQL */ `
  mutation ValidateInference($input: ValidateInferenceInput!) {
    validateInference(input: $input) {
      compatible modelStage stageError rowCount warnings
      columns { name requiredType actualType verdict }
    }
  }
`;
export interface ValidateInferenceResult {
  validateInference: InferenceCompatibilityReport;
}

const INFERENCE_SCHEDULE_FIELDS = /* GraphQL */ `
  id urn name enabled pausedReason
  modelVersionUrn modelUrn stageSelector
  inputSelector output
  cron intervalSeconds timezone overlapPolicy
  consecutiveFailures notifyOnFailure nextFireAt
`;

export const INFERENCE_SCHEDULES = /* GraphQL */ `
  query InferenceSchedules($first: Int, $after: String) {
    inferenceSchedules(first: $first, after: $after) {
      nodes { ${INFERENCE_SCHEDULE_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface InferenceSchedulesResult {
  inferenceSchedules: Connection<InferenceSchedule>;
}

export const INFERENCE_SCHEDULE_FIRES = /* GraphQL */ `
  query InferenceScheduleFires($scheduleId: ID!, $first: Int, $after: String) {
    inferenceScheduleFires(scheduleId: $scheduleId, first: $first, after: $after) {
      nodes { ${INFERENCE_JOB_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface InferenceScheduleFiresResult {
  inferenceScheduleFires: Connection<InferenceJob>;
}

export const CREATE_INFERENCE_SCHEDULE = /* GraphQL */ `
  mutation CreateInferenceSchedule($input: CreateInferenceScheduleInput!) {
    createInferenceSchedule(input: $input) { ${INFERENCE_SCHEDULE_FIELDS} }
  }
`;
export interface CreateInferenceScheduleResult {
  createInferenceSchedule: InferenceSchedule;
}

export const UPDATE_INFERENCE_SCHEDULE = /* GraphQL */ `
  mutation UpdateInferenceSchedule($id: ID!, $input: UpdateInferenceScheduleInput!) {
    updateInferenceSchedule(id: $id, input: $input) { ${INFERENCE_SCHEDULE_FIELDS} }
  }
`;
export interface UpdateInferenceScheduleResult {
  updateInferenceSchedule: InferenceSchedule;
}

export const DELETE_INFERENCE_SCHEDULE = /* GraphQL */ `
  mutation DeleteInferenceSchedule($id: ID!) {
    deleteInferenceSchedule(id: $id)
  }
`;
export interface DeleteInferenceScheduleResult {
  deleteInferenceSchedule: boolean;
}

export const PAUSE_INFERENCE_SCHEDULE = /* GraphQL */ `
  mutation PauseInferenceSchedule($id: ID!) {
    pauseInferenceSchedule(id: $id) { ${INFERENCE_SCHEDULE_FIELDS} }
  }
`;
export interface PauseInferenceScheduleResult {
  pauseInferenceSchedule: InferenceSchedule;
}

export const RESUME_INFERENCE_SCHEDULE = /* GraphQL */ `
  mutation ResumeInferenceSchedule($id: ID!) {
    resumeInferenceSchedule(id: $id) { ${INFERENCE_SCHEDULE_FIELDS} }
  }
`;
export interface ResumeInferenceScheduleResult {
  resumeInferenceSchedule: InferenceSchedule;
}

/** The real fire result verbatim: {fired: true, job_id, status} |
 * {fired: false, reason, error?}. */
export const TRIGGER_INFERENCE_SCHEDULE = /* GraphQL */ `
  mutation TriggerInferenceSchedule($id: ID!) {
    triggerInferenceSchedule(id: $id)
  }
`;
export interface TriggerInferenceScheduleResult {
  triggerInferenceSchedule: JSONValue;
}

export type {
  RegisterModelResult,
  RegisterRunInput,
  UpdateExperimentInput,
  RunNote,
  RunArtifact,
  RunComparison,
  ModelCardOverlayInput,
  InferenceCompatibilityReport,
  ValidateInferenceInput,
  InferenceSchedule,
  CreateInferenceScheduleInput,
  UpdateInferenceScheduleInput,
};

export const DASHBOARDS = /* GraphQL */ `
  query Dashboards($workspaceId: ID!, $first: Int, $after: String) {
    dashboards(workspaceId: $workspaceId, first: $first, after: $after) {
      nodes { id urn title module }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface DashboardsResult {
  dashboards: Connection<Dashboard>;
}

export const ARCHIVED_DASHBOARDS = /* GraphQL */ `
  query ArchivedDashboards($workspaceId: ID!, $first: Int, $after: String) {
    archivedDashboards(workspaceId: $workspaceId, first: $first, after: $after) {
      nodes { id urn title module archived }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ArchivedDashboardsResult {
  archivedDashboards: Connection<Dashboard>;
}

export const ARCHIVE_DASHBOARD = /* GraphQL */ `
  mutation ArchiveDashboard($id: ID!) {
    archiveDashboard(id: $id) { id urn title module archived }
  }
`;
export interface ArchiveDashboardResult {
  archiveDashboard: Dashboard;
}

export const RESTORE_DASHBOARD = /* GraphQL */ `
  mutation RestoreDashboard($id: ID!) {
    restoreDashboard(id: $id) { id urn title module archived }
  }
`;
export interface RestoreDashboardResult {
  restoreDashboard: Dashboard;
}

export const DASHBOARD = /* GraphQL */ `
  query DashboardDetail($id: ID!, $filters: [ChartFilterInput!]) {
    dashboard(id: $id) {
      id urn title module
      charts(filters: $filters) {
        id urn name chartType spec config displayMeta sources provenance
        data { rows columns artifact meta }
      }
    }
  }
`;
export interface DashboardResult {
  dashboard: Dashboard | null;
}

/* ---------- charts: type catalog, semantic models, authoring (no-code editor) ---------- */
export const CHART_TYPES = /* GraphQL */ `
  query ChartTypes {
    chartTypes { name family dataClass requiredFields configSchema }
  }
`;
export interface ChartTypesResult {
  chartTypes: ChartType[];
}

export const SEMANTIC_MODELS = /* GraphQL */ `
  query SemanticModels($workspaceId: ID) {
    semanticModels(workspaceId: $workspaceId) { id urn name }
  }
`;
export interface SemanticModelsResult {
  semanticModels: SemanticModel[];
}

export const SEMANTIC_MODEL = /* GraphQL */ `
  query SemanticModelByName($name: String!) {
    semanticModel(name: $name) {
      id urn name
      dimensions { name entity dimType }
      measures { name agg entity }
    }
  }
`;
export interface SemanticModelResult {
  semanticModel: SemanticModel | null;
}

const CHART_FIELDS = /* GraphQL */ `
  id urn name chartType spec config displayMeta sources provenance
  data { rows columns artifact meta }
`;

export const CHART_PREVIEW = /* GraphQL */ `
  query ChartPreview($input: CreateChartInput!) {
    chartPreview(input: $input) {
      chartId chartType columns rows artifact rowCount truncated
    }
  }
`;
export interface ChartPreviewResult {
  chartPreview: ChartShapedData;
}

export const CREATE_DASHBOARD = /* GraphQL */ `
  mutation CreateDashboard($input: CreateDashboardInput!, $idempotencyKey: String) {
    createDashboard(input: $input, idempotencyKey: $idempotencyKey) {
      id urn title module
      charts { ${CHART_FIELDS} }
    }
  }
`;
export interface CreateDashboardResult {
  createDashboard: Dashboard;
}

export const UPDATE_DASHBOARD = /* GraphQL */ `
  mutation UpdateDashboard($id: ID!, $input: UpdateDashboardInput!, $idempotencyKey: String) {
    updateDashboard(id: $id, input: $input, idempotencyKey: $idempotencyKey) {
      id urn title module
      charts { ${CHART_FIELDS} }
    }
  }
`;
export interface UpdateDashboardResult {
  updateDashboard: Dashboard;
}

export const DELETE_DASHBOARD = /* GraphQL */ `
  mutation DeleteDashboard($id: ID!) {
    deleteDashboard(id: $id)
  }
`;
export interface DeleteDashboardResult {
  deleteDashboard: boolean;
}

/* ---------- scheduled dashboard report subscriptions (notification-service) ---------- */
const REPORT_SUBSCRIPTION_FIELDS = /* GraphQL */ `
  id urn dashboardId workspaceId name recipients cadence sendHour sendWeekday
  timezone format enabled lastSentAt lastStatus lastError createdBy createdAt updatedAt
`;

export const REPORT_SUBSCRIPTIONS = /* GraphQL */ `
  query ReportSubscriptions($dashboardId: ID, $first: Int, $after: String) {
    reportSubscriptions(dashboardId: $dashboardId, first: $first, after: $after) {
      nodes { ${REPORT_SUBSCRIPTION_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ReportSubscriptionsResult {
  reportSubscriptions: Connection<ReportSubscription>;
}

export const CREATE_REPORT_SUBSCRIPTION = /* GraphQL */ `
  mutation CreateReportSubscription($input: CreateReportSubscriptionInput!, $idempotencyKey: String) {
    createReportSubscription(input: $input, idempotencyKey: $idempotencyKey) { ${REPORT_SUBSCRIPTION_FIELDS} }
  }
`;
export interface CreateReportSubscriptionResult {
  createReportSubscription: ReportSubscription;
}

export const UPDATE_REPORT_SUBSCRIPTION = /* GraphQL */ `
  mutation UpdateReportSubscription($id: ID!, $input: UpdateReportSubscriptionInput!, $idempotencyKey: String) {
    updateReportSubscription(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${REPORT_SUBSCRIPTION_FIELDS} }
  }
`;
export interface UpdateReportSubscriptionResult {
  updateReportSubscription: ReportSubscription;
}

export const DELETE_REPORT_SUBSCRIPTION = /* GraphQL */ `
  mutation DeleteReportSubscription($id: ID!) {
    deleteReportSubscription(id: $id)
  }
`;
export interface DeleteReportSubscriptionResult {
  deleteReportSubscription: boolean;
}

export const PAUSE_REPORT_SUBSCRIPTION = /* GraphQL */ `
  mutation PauseReportSubscription($id: ID!, $paused: Boolean!) {
    pauseReportSubscription(id: $id, paused: $paused) { ${REPORT_SUBSCRIPTION_FIELDS} }
  }
`;
export interface PauseReportSubscriptionResult {
  pauseReportSubscription: ReportSubscription;
}

export const TRIGGER_REPORT_SUBSCRIPTION = /* GraphQL */ `
  mutation TriggerReportSubscription($id: ID!) {
    triggerReportSubscription(id: $id)
  }
`;
export interface TriggerReportSubscriptionResult {
  triggerReportSubscription: boolean;
}

export const CREATE_CHART = /* GraphQL */ `
  mutation CreateChart($input: CreateChartInput!, $idempotencyKey: String) {
    createChart(input: $input, idempotencyKey: $idempotencyKey) { ${CHART_FIELDS} }
  }
`;
export interface CreateChartResult {
  createChart: Chart;
}

export const UPDATE_CHART = /* GraphQL */ `
  mutation UpdateChart($id: ID!, $input: UpdateChartInput!, $idempotencyKey: String) {
    updateChart(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${CHART_FIELDS} }
  }
`;
export interface UpdateChartResult {
  updateChart: Chart;
}

export const DELETE_CHART = /* GraphQL */ `
  mutation DeleteChart($id: ID!) {
    deleteChart(id: $id)
  }
`;
export interface DeleteChartResult {
  deleteChart: boolean;
}
export type {
  ChartType,
  SemanticModel,
  ChartShapedData,
  CreateChartInput,
  UpdateChartInput,
  CreateDashboardInput,
  UpdateDashboardInput,
};

export const WORKSPACE_COST_PANEL = /* GraphQL */ `
  query WorkspaceCostPanel($workspaceId: ID!, $from: Date!, $to: Date!) {
    workspaceCostPanel(workspaceId: $workspaceId, from: $from, to: $to) {
      rows { dimensions meterKey quantity costUsd }
      budgetStates { scope consumed limit lastThreshold exhaustedAt }
    }
  }
`;
export interface WorkspaceCostPanelResult {
  workspaceCostPanel: CostPanel;
}

const BUDGET_FIELDS = /* GraphQL */ `id urn scope meterKey window limitUsd thresholds actionAt100 status createdAt updatedAt`;

export const BUDGETS = /* GraphQL */ `
  query Budgets($first: Int, $after: String) {
    budgets(first: $first, after: $after) {
      nodes { ${BUDGET_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface BudgetsResult {
  budgets: Connection<Budget>;
}

export const CREATE_BUDGET = /* GraphQL */ `
  mutation CreateBudget($input: CreateBudgetInput!, $idempotencyKey: String) {
    createBudget(input: $input, idempotencyKey: $idempotencyKey) { ${BUDGET_FIELDS} }
  }
`;
export interface CreateBudgetResult {
  createBudget: Budget;
}
export type { CreateBudgetInput };

export const UPDATE_BUDGET = /* GraphQL */ `
  mutation UpdateBudget($id: ID!, $input: UpdateBudgetInput!, $idempotencyKey: String) {
    updateBudget(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${BUDGET_FIELDS} }
  }
`;
export interface UpdateBudgetResult {
  updateBudget: Budget;
}
export type { UpdateBudgetInput };

export const DELETE_BUDGET = /* GraphQL */ `
  mutation DeleteBudget($id: ID!) {
    deleteBudget(id: $id)
  }
`;
export interface DeleteBudgetResult {
  deleteBudget: boolean;
}

const RATE_CARD_FIELDS = /* GraphQL */ `id urn version effectiveFrom status items createdAt`;

export const RATE_CARDS = /* GraphQL */ `
  query RateCards($first: Int, $after: String) {
    rateCards(first: $first, after: $after) {
      nodes { ${RATE_CARD_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface RateCardsResult {
  rateCards: Connection<RateCard>;
}

export const CREATE_RATE_CARD = /* GraphQL */ `
  mutation CreateRateCard($input: CreateRateCardInput!, $idempotencyKey: String) {
    createRateCard(input: $input, idempotencyKey: $idempotencyKey) { ${RATE_CARD_FIELDS} }
  }
`;
export interface CreateRateCardResult {
  createRateCard: RateCard;
}
export type { CreateRateCardInput };

export const ACTIVATE_RATE_CARD = /* GraphQL */ `
  mutation ActivateRateCard($id: ID!) {
    activateRateCard(id: $id) { ${RATE_CARD_FIELDS} }
  }
`;
export interface ActivateRateCardResult {
  activateRateCard: RateCard;
}

const ANOMALY_FIELDS = /* GraphQL */ `id urn meterKey day observed mean stddev z status dismissedBy suppressedReason createdAt`;

export const ANOMALIES = /* GraphQL */ `
  query Anomalies($status: String) {
    anomalies(status: $status) { ${ANOMALY_FIELDS} }
  }
`;
export interface AnomaliesResult {
  anomalies: Anomaly[];
}

export const DISMISS_ANOMALY = /* GraphQL */ `
  mutation DismissAnomaly($id: ID!) {
    dismissAnomaly(id: $id) { ${ANOMALY_FIELDS} }
  }
`;
export interface DismissAnomalyResult {
  dismissAnomaly: Anomaly;
}

export const USER = /* GraphQL */ `
  query UserById($id: ID!) {
    user(id: $id) { id urn email fullName status lastLoginAt createdAt }
  }
`;
export interface UserResult {
  user: User | null;
}

/* ---------- admin: users, workspaces, groups, service accounts, tenant, audit ---------- */
export const USERS = /* GraphQL */ `
  query Users($first: Int, $after: String) {
    users(first: $first, after: $after) {
      nodes { id urn email fullName status lastLoginAt createdAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface UsersResult {
  users: Connection<User>;
}

/** Member-safe active-user directory for assignee/mention pickers — no admin
 * scope (unlike USERS). Only id/email/fullName are populated. */
export const ASSIGNABLE_USERS = /* GraphQL */ `
  query AssignableUsers($first: Int, $after: String) {
    assignableUsers(first: $first, after: $after) {
      nodes { id urn email fullName }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface AssignableUsersResult {
  assignableUsers: Connection<User>;
}

export const INVITE_USER = /* GraphQL */ `
  mutation InviteUser($input: InviteUserInput!, $idempotencyKey: String) {
    inviteUser(input: $input, idempotencyKey: $idempotencyKey) {
      id urn email fullName status createdAt
    }
  }
`;
export interface InviteUserResult {
  inviteUser: User;
}
export type { InviteUserInput };

const WORKSPACE_FIELDS = /* GraphQL */ `
  id urn name description public archived archivedAt createdBy createdAt updatedAt
`;

export const WORKSPACES = /* GraphQL */ `
  query Workspaces($first: Int, $after: String, $archived: String) {
    workspaces(first: $first, after: $after, archived: $archived) {
      nodes { ${WORKSPACE_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface WorkspacesResult {
  workspaces: Connection<Workspace>;
}

export const CREATE_WORKSPACE = /* GraphQL */ `
  mutation CreateWorkspace($input: CreateWorkspaceInput!, $idempotencyKey: String) {
    createWorkspace(input: $input, idempotencyKey: $idempotencyKey) { ${WORKSPACE_FIELDS} }
  }
`;
export interface CreateWorkspaceResult {
  createWorkspace: Workspace;
}
export type { CreateWorkspaceInput };

export const GROUPS = /* GraphQL */ `
  query Groups($first: Int, $after: String, $type: String) {
    groups(first: $first, after: $after, type: $type) {
      nodes { id urn name description groupType system autoGenerated createdAt updatedAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface GroupsResult {
  groups: Connection<Group>;
}

export const GROUP_MEMBERS = /* GraphQL */ `
  query GroupMembers($groupId: ID!, $first: Int, $after: String) {
    groupMembers(groupId: $groupId, first: $first, after: $after) {
      userId expiresAt createdAt
    }
  }
`;
export interface GroupMembersResult {
  groupMembers: GroupMember[];
}

export const GROUP_ROLES = /* GraphQL */ `
  query GroupRoles($groupId: ID!, $first: Int, $after: String) {
    groupRoles(groupId: $groupId, first: $first, after: $after) {
      id name system version actions createdAt updatedAt
    }
  }
`;
export interface GroupRolesResult {
  groupRoles: Role[];
}

export const USER_GROUPS = /* GraphQL */ `
  query UserGroups($userId: ID!, $first: Int, $after: String) {
    userGroups(userId: $userId, first: $first, after: $after) {
      id urn name description groupType system autoGenerated createdAt updatedAt
    }
  }
`;
export interface UserGroupsResult {
  userGroups: Group[];
}

export const ADD_GROUP_MEMBER = /* GraphQL */ `
  mutation AddGroupMember($groupId: ID!, $userId: ID!, $idempotencyKey: String) {
    addGroupMember(groupId: $groupId, userId: $userId, idempotencyKey: $idempotencyKey)
  }
`;
export interface AddGroupMemberResult {
  addGroupMember: boolean;
}

export const REMOVE_GROUP_MEMBER = /* GraphQL */ `
  mutation RemoveGroupMember($groupId: ID!, $userId: ID!) {
    removeGroupMember(groupId: $groupId, userId: $userId)
  }
`;
export interface RemoveGroupMemberResult {
  removeGroupMember: boolean;
}

const TEAM_FIELDS = /* GraphQL */ `id urn name description groupType system autoGenerated createdAt updatedAt`;

export const CREATE_TEAM = /* GraphQL */ `
  mutation CreateTeam($input: CreateTeamInput!, $idempotencyKey: String) {
    createTeam(input: $input, idempotencyKey: $idempotencyKey) { ${TEAM_FIELDS} }
  }
`;
export interface CreateTeamResult {
  createTeam: Group;
}
export type { CreateTeamInput };

export const UPDATE_TEAM = /* GraphQL */ `
  mutation UpdateTeam($id: ID!, $input: UpdateTeamInput!, $idempotencyKey: String) {
    updateTeam(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${TEAM_FIELDS} }
  }
`;
export interface UpdateTeamResult {
  updateTeam: Group;
}
export type { UpdateTeamInput };

export const DELETE_TEAM = /* GraphQL */ `
  mutation DeleteTeam($id: ID!) {
    deleteTeam(id: $id)
  }
`;
export interface DeleteTeamResult {
  deleteTeam: boolean;
}

export const ASSIGN_TEAM_ROLE = /* GraphQL */ `
  mutation AssignTeamRole($groupId: ID!, $roleId: ID!) {
    assignTeamRole(groupId: $groupId, roleId: $roleId)
  }
`;
export interface AssignTeamRoleResult {
  assignTeamRole: boolean;
}

export const UNASSIGN_TEAM_ROLE = /* GraphQL */ `
  mutation UnassignTeamRole($groupId: ID!, $roleId: ID!) {
    unassignTeamRole(groupId: $groupId, roleId: $roleId)
  }
`;
export interface UnassignTeamRoleResult {
  unassignTeamRole: boolean;
}

export const ROLES = /* GraphQL */ `
  query Roles($first: Int, $after: String) {
    roles(first: $first, after: $after) {
      nodes { id name system version actions createdAt updatedAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface RolesResult {
  roles: Connection<Role>;
}

export const SERVICE_ACCOUNTS = /* GraphQL */ `
  query ServiceAccounts($first: Int, $after: String) {
    serviceAccounts(first: $first, after: $after) {
      nodes { id urn name scopes expiresAt lastUsedAt revokedAt createdAt updatedAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ServiceAccountsResult {
  serviceAccounts: Connection<ServiceAccount>;
}

/* ---- Tier 4b: identity/rbac admin (user + SA lifecycle, workspace lifecycle,
 * content groups, custom roles, content grants, bulk membership) ------------ */
const USER_FIELDS = /* GraphQL */ `id urn email fullName status lastLoginAt createdAt`;

export const UPDATE_USER = /* GraphQL */ `
  mutation UpdateUser($id: ID!, $fullName: String!, $idempotencyKey: String) {
    updateUser(id: $id, fullName: $fullName, idempotencyKey: $idempotencyKey) { ${USER_FIELDS} }
  }
`;
export interface UpdateUserResult {
  updateUser: User;
}

export const DEACTIVATE_USER = /* GraphQL */ `
  mutation DeactivateUser($id: ID!, $overrideLastAdmin: Boolean, $idempotencyKey: String) {
    deactivateUser(id: $id, overrideLastAdmin: $overrideLastAdmin, idempotencyKey: $idempotencyKey) { ${USER_FIELDS} }
  }
`;
export interface DeactivateUserResult {
  deactivateUser: User;
}

export const RESEND_USER_INVITE = /* GraphQL */ `
  mutation ResendUserInvite($id: ID!, $idempotencyKey: String) {
    resendUserInvite(id: $id, idempotencyKey: $idempotencyKey) { ${USER_FIELDS} }
  }
`;
export interface ResendUserInviteResult {
  resendUserInvite: User;
}

export const DELETE_USER = /* GraphQL */ `
  mutation DeleteUser($id: ID!) {
    deleteUser(id: $id)
  }
`;
export interface DeleteUserResult {
  deleteUser: boolean;
}

const SERVICE_ACCOUNT_FIELDS = /* GraphQL */ `id urn name scopes expiresAt lastUsedAt revokedAt createdAt updatedAt`;

export const CREATE_SERVICE_ACCOUNT = /* GraphQL */ `
  mutation CreateServiceAccount($input: CreateServiceAccountInput!, $idempotencyKey: String) {
    createServiceAccount(input: $input, idempotencyKey: $idempotencyKey) {
      serviceAccount { ${SERVICE_ACCOUNT_FIELDS} }
      apiKey
    }
  }
`;
export interface CreateServiceAccountResult {
  createServiceAccount: CreatedServiceAccount;
}
export type { CreateServiceAccountInput };

export const ROTATE_SERVICE_ACCOUNT = /* GraphQL */ `
  mutation RotateServiceAccount($id: ID!, $idempotencyKey: String) {
    rotateServiceAccount(id: $id, idempotencyKey: $idempotencyKey) {
      serviceAccount { ${SERVICE_ACCOUNT_FIELDS} }
      apiKey
    }
  }
`;
export interface RotateServiceAccountResult {
  rotateServiceAccount: CreatedServiceAccount;
}

export const REVOKE_SERVICE_ACCOUNT = /* GraphQL */ `
  mutation RevokeServiceAccount($id: ID!) {
    revokeServiceAccount(id: $id)
  }
`;
export interface RevokeServiceAccountResult {
  revokeServiceAccount: boolean;
}

export const UPDATE_WORKSPACE = /* GraphQL */ `
  mutation UpdateWorkspace($id: ID!, $input: UpdateWorkspaceInput!, $idempotencyKey: String) {
    updateWorkspace(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${WORKSPACE_FIELDS} }
  }
`;
export interface UpdateWorkspaceResult {
  updateWorkspace: Workspace;
}
export type { UpdateWorkspaceInput };

export const ARCHIVE_WORKSPACE = /* GraphQL */ `
  mutation ArchiveWorkspace($id: ID!, $idempotencyKey: String) {
    archiveWorkspace(id: $id, idempotencyKey: $idempotencyKey) { ${WORKSPACE_FIELDS} }
  }
`;
export interface ArchiveWorkspaceResult {
  archiveWorkspace: Workspace;
}

export const RESTORE_WORKSPACE = /* GraphQL */ `
  mutation RestoreWorkspace($id: ID!, $idempotencyKey: String) {
    restoreWorkspace(id: $id, idempotencyKey: $idempotencyKey) { ${WORKSPACE_FIELDS} }
  }
`;
export interface RestoreWorkspaceResult {
  restoreWorkspace: Workspace;
}

export const LINK_WORKSPACE_CONTENT_GROUP = /* GraphQL */ `
  mutation LinkWorkspaceContentGroup($workspaceId: ID!, $groupId: ID!) {
    linkWorkspaceContentGroup(workspaceId: $workspaceId, groupId: $groupId)
  }
`;
export interface LinkWorkspaceContentGroupResult {
  linkWorkspaceContentGroup: boolean;
}

export const UNLINK_WORKSPACE_CONTENT_GROUP = /* GraphQL */ `
  mutation UnlinkWorkspaceContentGroup($workspaceId: ID!, $groupId: ID!) {
    unlinkWorkspaceContentGroup(workspaceId: $workspaceId, groupId: $groupId)
  }
`;
export interface UnlinkWorkspaceContentGroupResult {
  unlinkWorkspaceContentGroup: boolean;
}

export const CREATE_GROUP = /* GraphQL */ `
  mutation CreateGroup($input: CreateGroupInput!, $idempotencyKey: String) {
    createGroup(input: $input, idempotencyKey: $idempotencyKey) { ${TEAM_FIELDS} }
  }
`;
export interface CreateGroupResult {
  createGroup: Group;
}
export type { CreateGroupInput };

export const UPDATE_GROUP = /* GraphQL */ `
  mutation UpdateGroup($input: UpdateGroupInput!, $idempotencyKey: String) {
    updateGroup(input: $input, idempotencyKey: $idempotencyKey) { ${TEAM_FIELDS} }
  }
`;
export interface UpdateGroupResult {
  updateGroup: Group;
}
export type { UpdateGroupInput };

export const BULK_GROUP_MEMBERSHIP = /* GraphQL */ `
  mutation BulkGroupMembership($groupId: ID!, $operations: [GroupMemberOpInput!]!, $idempotencyKey: String) {
    bulkGroupMembership(groupId: $groupId, operations: $operations, idempotencyKey: $idempotencyKey) {
      succeeded
      failed
      results { userId op ok code }
    }
  }
`;
export interface BulkGroupMembershipResultData {
  bulkGroupMembership: BulkGroupMembershipResult;
}
export type { GroupMemberOpInput };

const ROLE_FIELDS = /* GraphQL */ `id name system version actions createdAt updatedAt`;

export const CREATE_ROLE = /* GraphQL */ `
  mutation CreateRole($input: CreateRoleInput!, $idempotencyKey: String) {
    createRole(input: $input, idempotencyKey: $idempotencyKey) { ${ROLE_FIELDS} }
  }
`;
export interface CreateRoleResult {
  createRole: Role;
}
export type { CreateRoleInput };

export const RENAME_ROLE = /* GraphQL */ `
  mutation RenameRole($id: ID!, $name: String!, $idempotencyKey: String) {
    renameRole(id: $id, name: $name, idempotencyKey: $idempotencyKey) { ${ROLE_FIELDS} }
  }
`;
export interface RenameRoleResult {
  renameRole: Role;
}

export const UPDATE_ROLE = /* GraphQL */ `
  mutation UpdateRole($id: ID!, $input: UpdateRoleInput!, $idempotencyKey: String) {
    updateRole(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${ROLE_FIELDS} }
  }
`;
export interface UpdateRoleResult {
  updateRole: Role;
}
export type { UpdateRoleInput };

export const SET_ROLE_ACTIONS = /* GraphQL */ `
  mutation SetRoleActions($id: ID!, $actions: [String!]!, $idempotencyKey: String) {
    setRoleActions(id: $id, actions: $actions, idempotencyKey: $idempotencyKey) { ${ROLE_FIELDS} }
  }
`;
export interface SetRoleActionsResult {
  setRoleActions: Role;
}

export const DELETE_ROLE = /* GraphQL */ `
  mutation DeleteRole($id: ID!) {
    deleteRole(id: $id)
  }
`;
export interface DeleteRoleResult {
  deleteRole: boolean;
}

export const CONTENT_GRANTS = /* GraphQL */ `
  query ContentGrants($resourceUrn: String!) {
    contentGrants(resourceUrn: $resourceUrn) {
      subjectType subjectId level provenance via grantId workspaceId
    }
  }
`;
export interface ContentGrantsResult {
  contentGrants: EffectiveAccessEntry[];
}

export const CREATE_CONTENT_GRANT = /* GraphQL */ `
  mutation CreateContentGrant($input: CreateContentGrantInput!, $idempotencyKey: String) {
    createContentGrant(input: $input, idempotencyKey: $idempotencyKey) {
      id workspaceId resourceUrn subjectType subjectId level implicit createdAt
    }
  }
`;
export interface CreateContentGrantResult {
  createContentGrant: ContentGrant;
}
export type { CreateContentGrantInput };

export const DELETE_CONTENT_GRANT = /* GraphQL */ `
  mutation DeleteContentGrant($id: ID!) {
    deleteContentGrant(id: $id)
  }
`;
export interface DeleteContentGrantResult {
  deleteContentGrant: boolean;
}

export const TENANT = /* GraphQL */ `
  query Tenant($id: ID!) {
    tenant(id: $id) {
      id urn name displayName ownerEmail tier cloud status subdomain
      platformVersion autoUpgrade modules createdAt updatedAt
      quotas { cpu memory processingCpu processingMemory }
      embedConfig { configured allowedOrigins updatedAt }
    }
  }
`;
export interface TenantResult {
  tenant: Tenant | null;
}

/** All tenants (platform-admin only; identity requireSuperAdmin enforces). */
export const TENANTS = /* GraphQL */ `
  query Tenants($limit: Int) {
    tenants(limit: $limit) {
      id urn name displayName ownerEmail status tier cloud subdomain createdAt
    }
  }
`;
export interface TenantsResult {
  tenants: Tenant[];
}

export const SET_EMBED_CONFIG = /* GraphQL */ `
  mutation SetEmbedConfig($tenantId: ID!, $allowedOrigins: [String!]!, $idempotencyKey: String) {
    setEmbedConfig(tenantId: $tenantId, allowedOrigins: $allowedOrigins, idempotencyKey: $idempotencyKey) {
      embedSecret allowedOrigins
    }
  }
`;
export interface SetEmbedConfigResultWrapper {
  setEmbedConfig: SetEmbedConfigResult;
}

// ---- BYO-P4: per-tenant OIDC IdP config ------------------------------------
const IDP_FIELDS = `configured issuer clientId discoveryUrl enabled updatedAt`;

export const TENANT_IDP = /* GraphQL */ `
  query TenantIdp { tenantIdp { ${IDP_FIELDS} } }
`;
export interface TenantIdpResult { tenantIdp: TenantIdpConfig }

export const SET_TENANT_IDP = /* GraphQL */ `
  mutation SetTenantIdp($input: SetTenantIdpInput!, $idempotencyKey: String) {
    setTenantIdp(input: $input, idempotencyKey: $idempotencyKey) { ${IDP_FIELDS} }
  }
`;
export interface SetTenantIdpResult { setTenantIdp: TenantIdpConfig }

export const DELETE_TENANT_IDP = /* GraphQL */ `
  mutation DeleteTenantIdp { deleteTenantIdp }
`;
export interface DeleteTenantIdpResult { deleteTenantIdp: boolean }

export const AUDIT_EVENTS = /* GraphQL */ `
  query AuditEvents(
    $from: DateTime, $to: DateTime, $eventType: String, $action: String,
    $actorId: String, $actorType: String, $resourceUrn: String,
    $first: Int, $after: String
  ) {
    auditEvents(
      from: $from, to: $to, eventType: $eventType, action: $action,
      actorId: $actorId, actorType: $actorType, resourceUrn: $resourceUrn,
      first: $first, after: $after
    ) {
      nodes {
        eventId urn eventType tenantId actorType actorId viaAgentId viaAgentVersion
        action resourceUrn occurredAt ingestedAt traceId payloadDigest bodyWithheld chainSeq
      }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface AuditEventsResult {
  auditEvents: Connection<AuditEvent>;
}

export type {
  User,
  Workspace,
  Group,
  GroupMember,
  Role,
  ServiceAccount,
  Tenant,
  AuditEvent,
};

/* ------------------------- semantic model authoring ------------------------- */
export const DATASET_SCHEMA = /* GraphQL */ `
  query DatasetSchema($datasetId: ID!, $version: Int) {
    datasetSchema(datasetId: $datasetId, version: $version) { name type nullable tags inferred }
  }
`;
export interface DatasetSchemaResult {
  datasetSchema: DatasetColumn[];
}

const SEMANTIC_MODEL_SUMMARY_FIELDS = /* GraphQL */ `
  id urn workspaceId name description publishedVersionNo draftVersionNo
  healthStatus createdBy createdAt updatedAt
`;

export const SEMANTIC_MODEL_LIST = /* GraphQL */ `
  query SemanticModelList($workspaceId: ID, $first: Int, $after: String) {
    semanticModelList(workspaceId: $workspaceId, first: $first, after: $after) {
      nodes { ${SEMANTIC_MODEL_SUMMARY_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface SemanticModelListResult {
  semanticModelList: Connection<SemanticModelSummary>;
}

export const SEMANTIC_MODEL_DETAIL = /* GraphQL */ `
  query SemanticModelDetail($id: ID!) {
    semanticModelDetail(id: $id) { ${SEMANTIC_MODEL_SUMMARY_FIELDS} }
  }
`;
export interface SemanticModelDetailResult {
  semanticModelDetail: SemanticModelSummary | null;
}

const SEMANTIC_VERSION_HEADER_FIELDS = /* GraphQL */ `
  id urn modelId versionNo status submittedBy approvedBy decisionNote publishedAt createdAt
`;

export const SEMANTIC_MODEL_VERSIONS = /* GraphQL */ `
  query SemanticModelVersions($modelId: ID!, $first: Int, $after: String) {
    semanticModelVersions(modelId: $modelId, first: $first, after: $after) {
      nodes { ${SEMANTIC_VERSION_HEADER_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface SemanticModelVersionsResult {
  semanticModelVersions: Connection<SemanticModelVersion>;
}

const SEMANTIC_DEFINITION_FIELDS = /* GraphQL */ `
  entities { name datasetUrn table primaryKey datasetVersionPolicy description }
  dimensions { name entity column expr dimType timeGrains synonyms description deprecated successor }
  measures { name entity agg expr exprMetric filters format synonyms description deprecated successor }
  joinPaths { name fromEntity toEntity joinType on { fromColumn toColumn } cardinality }
`;

export const SEMANTIC_MODEL_VERSION = /* GraphQL */ `
  query SemanticModelVersion($modelId: ID!, $versionNo: Int!) {
    semanticModelVersion(modelId: $modelId, versionNo: $versionNo) {
      ${SEMANTIC_VERSION_HEADER_FIELDS}
      definitionJson
      definition { ${SEMANTIC_DEFINITION_FIELDS} }
    }
  }
`;
export interface SemanticModelVersionResult {
  semanticModelVersion: SemanticModelVersion | null;
}

export const CREATE_SEMANTIC_MODEL = /* GraphQL */ `
  mutation CreateSemanticModel($input: CreateSemanticModelInput!, $idempotencyKey: String) {
    createSemanticModel(input: $input, idempotencyKey: $idempotencyKey) { ${SEMANTIC_MODEL_SUMMARY_FIELDS} }
  }
`;
export interface CreateSemanticModelResult {
  createSemanticModel: SemanticModelSummary;
}
export type { CreateSemanticModelInput };

export const UPDATE_SEMANTIC_MODEL = /* GraphQL */ `
  mutation UpdateSemanticModel($id: ID!, $input: UpdateSemanticModelInput!) {
    updateSemanticModel(id: $id, input: $input) { ${SEMANTIC_MODEL_SUMMARY_FIELDS} }
  }
`;
export interface UpdateSemanticModelResult {
  updateSemanticModel: SemanticModelSummary;
}
export type { UpdateSemanticModelInput };

export const DELETE_SEMANTIC_MODEL = /* GraphQL */ `
  mutation DeleteSemanticModel($id: ID!) {
    deleteSemanticModel(id: $id)
  }
`;
export interface DeleteSemanticModelResult {
  deleteSemanticModel: boolean;
}

export const CREATE_SEMANTIC_MODEL_VERSION = /* GraphQL */ `
  mutation CreateSemanticModelVersion($modelId: ID!, $idempotencyKey: String) {
    createSemanticModelVersion(modelId: $modelId, idempotencyKey: $idempotencyKey) {
      ${SEMANTIC_VERSION_HEADER_FIELDS}
      definitionJson
      definition { ${SEMANTIC_DEFINITION_FIELDS} }
    }
  }
`;
export interface CreateSemanticModelVersionResult {
  createSemanticModelVersion: SemanticModelVersion;
}

export const UPDATE_SEMANTIC_MODEL_DRAFT = /* GraphQL */ `
  mutation UpdateSemanticModelDraft($modelId: ID!, $versionNo: Int!, $definition: JSON!) {
    updateSemanticModelDraft(modelId: $modelId, versionNo: $versionNo, definition: $definition) {
      ${SEMANTIC_VERSION_HEADER_FIELDS}
      definitionJson
    }
  }
`;
export interface UpdateSemanticModelDraftResult {
  updateSemanticModelDraft: SemanticModelVersion;
}

export const SUBMIT_SEMANTIC_MODEL_VERSION = /* GraphQL */ `
  mutation SubmitSemanticModelVersion($modelId: ID!, $versionNo: Int!) {
    submitSemanticModelVersion(modelId: $modelId, versionNo: $versionNo) { ${SEMANTIC_VERSION_HEADER_FIELDS} }
  }
`;
export interface SubmitSemanticModelVersionResult {
  submitSemanticModelVersion: SemanticModelVersion;
}

export const APPROVE_SEMANTIC_MODEL_VERSION = /* GraphQL */ `
  mutation ApproveSemanticModelVersion($modelId: ID!, $versionNo: Int!, $note: String) {
    approveSemanticModelVersion(modelId: $modelId, versionNo: $versionNo, note: $note) { ${SEMANTIC_VERSION_HEADER_FIELDS} }
  }
`;
export interface ApproveSemanticModelVersionResult {
  approveSemanticModelVersion: SemanticModelVersion;
}

export const REJECT_SEMANTIC_MODEL_VERSION = /* GraphQL */ `
  mutation RejectSemanticModelVersion($modelId: ID!, $versionNo: Int!, $note: String!) {
    rejectSemanticModelVersion(modelId: $modelId, versionNo: $versionNo, note: $note) { ${SEMANTIC_VERSION_HEADER_FIELDS} }
  }
`;
export interface RejectSemanticModelVersionResult {
  rejectSemanticModelVersion: SemanticModelVersion;
}

export const COMPILE_SEMANTIC_MODEL = /* GraphQL */ `
  query CompileSemanticModel($input: CompileSemanticModelInput!) {
    compileSemanticModel(input: $input) {
      sql engineDialect warnings provenance
      outputSchema { name type role }
      validationAvailable validationValid validationMessage
    }
  }
`;
export interface CompileSemanticModelResult {
  compileSemanticModel: SemanticCompileResult;
}
export type { CompileSemanticModelInput };

/* ---------- verified NL↔SQL pairs (semantic-service, four-eyes) ---------- */
const VERIFIED_QUERY_FIELDS = /* GraphQL */ `
  id urn workspaceId modelId nlText sqlText variables status tags provenance
  healthNote submittedBy approvedBy decidedAt createdAt updatedAt
`;

export const VERIFIED_QUERIES = /* GraphQL */ `
  query VerifiedQueries($workspaceId: ID, $status: String, $first: Int, $after: String) {
    verifiedQueries(workspaceId: $workspaceId, status: $status, first: $first, after: $after) {
      nodes { ${VERIFIED_QUERY_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface VerifiedQueriesResult {
  verifiedQueries: Connection<VerifiedQuery>;
}

export const VERIFIED_QUERY_SEARCH = /* GraphQL */ `
  query VerifiedQuerySearch($query: String!, $workspaceId: ID!, $topK: Int) {
    verifiedQuerySearch(query: $query, workspaceId: $workspaceId, topK: $topK) {
      id nlText sqlText variables tags modelId score
    }
  }
`;
export interface VerifiedQuerySearchResult {
  verifiedQuerySearch: VerifiedQuerySearchHit[];
}

export const CREATE_VERIFIED_QUERY = /* GraphQL */ `
  mutation CreateVerifiedQuery($input: CreateVerifiedQueryInput!, $idempotencyKey: String) {
    createVerifiedQuery(input: $input, idempotencyKey: $idempotencyKey) { ${VERIFIED_QUERY_FIELDS} }
  }
`;
export interface CreateVerifiedQueryResult {
  createVerifiedQuery: VerifiedQuery;
}
export type { CreateVerifiedQueryInput };

export const UPDATE_VERIFIED_QUERY = /* GraphQL */ `
  mutation UpdateVerifiedQuery($id: ID!, $input: UpdateVerifiedQueryInput!) {
    updateVerifiedQuery(id: $id, input: $input) { ${VERIFIED_QUERY_FIELDS} }
  }
`;
export interface UpdateVerifiedQueryResult {
  updateVerifiedQuery: VerifiedQuery;
}
export type { UpdateVerifiedQueryInput };

export const SUBMIT_VERIFIED_QUERY = /* GraphQL */ `
  mutation SubmitVerifiedQuery($id: ID!) {
    submitVerifiedQuery(id: $id) { ${VERIFIED_QUERY_FIELDS} }
  }
`;
export interface SubmitVerifiedQueryResult {
  submitVerifiedQuery: VerifiedQuery;
}

export const APPROVE_VERIFIED_QUERY = /* GraphQL */ `
  mutation ApproveVerifiedQuery($id: ID!) {
    approveVerifiedQuery(id: $id) { ${VERIFIED_QUERY_FIELDS} }
  }
`;
export interface ApproveVerifiedQueryResult {
  approveVerifiedQuery: VerifiedQuery;
}

export const REJECT_VERIFIED_QUERY = /* GraphQL */ `
  mutation RejectVerifiedQuery($id: ID!, $note: String) {
    rejectVerifiedQuery(id: $id, note: $note) { ${VERIFIED_QUERY_FIELDS} }
  }
`;
export interface RejectVerifiedQueryResult {
  rejectVerifiedQuery: VerifiedQuery;
}

export const ARCHIVE_VERIFIED_QUERY = /* GraphQL */ `
  mutation ArchiveVerifiedQuery($id: ID!) {
    archiveVerifiedQuery(id: $id) { ${VERIFIED_QUERY_FIELDS} }
  }
`;
export interface ArchiveVerifiedQueryResult {
  archiveVerifiedQuery: VerifiedQuery;
}

/* ---------- semantic bootstrap-from-dataset (202 async + polling) ---------- */
const SEMANTIC_OPERATION_FIELDS = /* GraphQL */ `operationId kind status report createdAt finishedAt`;

export const BOOTSTRAP_SEMANTIC_MODEL = /* GraphQL */ `
  mutation BootstrapSemanticModel($modelId: ID!, $sources: JSON, $idempotencyKey: String) {
    bootstrapSemanticModel(modelId: $modelId, sources: $sources, idempotencyKey: $idempotencyKey) {
      ${SEMANTIC_OPERATION_FIELDS}
    }
  }
`;
export interface BootstrapSemanticModelResult {
  bootstrapSemanticModel: SemanticOperation;
}

export const SEMANTIC_OPERATION = /* GraphQL */ `
  query SemanticOperation($id: ID!) {
    semanticOperation(id: $id) { ${SEMANTIC_OPERATION_FIELDS} }
  }
`;
export interface SemanticOperationResult {
  semanticOperation: SemanticOperation | null;
}

// ---- kill switches (agent-runtime + tool-plane, ART-FR-073 / TPL-FR-052) ----
const KILL_SWITCH_FIELDS = /* GraphQL */ `id target scope agentKey toolId version tenantId active reason setBy createdAt`;

export const AGENT_KILL_SWITCHES = /* GraphQL */ `
  query AgentKillSwitches {
    agentKillSwitches { ${KILL_SWITCH_FIELDS} }
  }
`;
export interface AgentKillSwitchesResult {
  agentKillSwitches: KillSwitch[];
}

export const TOOL_KILL_SWITCHES = /* GraphQL */ `
  query ToolKillSwitches {
    toolKillSwitches { ${KILL_SWITCH_FIELDS} }
  }
`;
export interface ToolKillSwitchesResult {
  toolKillSwitches: KillSwitch[];
}

export const CREATE_AGENT_KILL_SWITCH = /* GraphQL */ `
  mutation CreateAgentKillSwitch($agentKey: String!, $scope: String, $version: Int, $tenantId: String, $reason: String!, $idempotencyKey: String) {
    createAgentKillSwitch(agentKey: $agentKey, scope: $scope, version: $version, tenantId: $tenantId, reason: $reason, idempotencyKey: $idempotencyKey) { ${KILL_SWITCH_FIELDS} }
  }
`;
export interface CreateAgentKillSwitchResult {
  createAgentKillSwitch: KillSwitch;
}

export const DELETE_AGENT_KILL_SWITCH = /* GraphQL */ `
  mutation DeleteAgentKillSwitch($killId: ID!) {
    deleteAgentKillSwitch(killId: $killId) { id active }
  }
`;
export interface DeleteAgentKillSwitchResult {
  deleteAgentKillSwitch: KillSwitchLiftResult;
}

export const CREATE_TOOL_KILL_SWITCH = /* GraphQL */ `
  mutation CreateToolKillSwitch($toolId: String!, $scope: String!, $version: String, $tenantId: String, $reason: String!, $idempotencyKey: String) {
    createToolKillSwitch(toolId: $toolId, scope: $scope, version: $version, tenantId: $tenantId, reason: $reason, idempotencyKey: $idempotencyKey) { ${KILL_SWITCH_FIELDS} }
  }
`;
export interface CreateToolKillSwitchResult {
  createToolKillSwitch: KillSwitch;
}

export const DELETE_TOOL_KILL_SWITCH = /* GraphQL */ `
  mutation DeleteToolKillSwitch($id: ID!) {
    deleteToolKillSwitch(id: $id) { id active }
  }
`;
export interface DeleteToolKillSwitchResult {
  deleteToolKillSwitch: KillSwitchLiftResult;
}

// ---- memory (memory-service) ------------------------------------------------
const MEMORY_RECORD_FIELDS = /* GraphQL */ `
  id urn scope scopeRef content confidence status tags retrievalCount classifierScore ttlExpiresAt
`;

export const MEMORIES = /* GraphQL */ `
  query Memories($scope: String, $scopeRef: String, $status: String, $tags: [String!], $first: Int, $after: String) {
    memories(scope: $scope, scopeRef: $scopeRef, status: $status, tags: $tags, first: $first, after: $after) {
      nodes { ${MEMORY_RECORD_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface MemoriesResult {
  memories: Connection<MemoryRecord>;
}

export const MEMORY = /* GraphQL */ `
  query Memory($id: ID!) {
    memory(id: $id) { ${MEMORY_RECORD_FIELDS} provenance mergedFrom revalidateAt }
  }
`;
export interface MemoryResult {
  memory: MemoryRecord | null;
}

export const MEMORY_STATS = /* GraphQL */ `
  query MemoryStats {
    memoryStats
  }
`;
export interface MemoryStatsResult {
  memoryStats: JSONValue;
}

const ERASURE_FIELDS = /* GraphQL */ `operationId status report completedAt`;

export const ERASURE = /* GraphQL */ `
  query Erasure($id: ID!) {
    erasure(id: $id) { ${ERASURE_FIELDS} }
  }
`;
export interface ErasureResult {
  erasure: ErasureRequest | null;
}

export const REQUEST_MEMORY_ERASURE = /* GraphQL */ `
  mutation RequestMemoryErasure($subjectId: String!, $subjectType: String) {
    requestMemoryErasure(subjectId: $subjectId, subjectType: $subjectType) { ${ERASURE_FIELDS} }
  }
`;
export interface RequestMemoryErasureResult {
  requestMemoryErasure: ErasureRequest;
}

// ---- rbac authz explain (debug) ---------------------------------------------
export const EXPLAIN_AUTHZ = /* GraphQL */ `
  query ExplainAuthz($input: ExplainAuthzInput!) {
    explainAuthz(input: $input) {
      allowed
      reason
      chain { type group groupType role action workspaceScoped viaGroup workspace level subject admin detail }
    }
  }
`;
export interface ExplainAuthzResult {
  explainAuthz: AuthzExplanation;
}

// ---- audit compliance packs + chain-integrity verify ------------------------
const COMPLIANCE_JOB_FIELDS = /* GraphQL */ `operationId status resultUrl error`;

export const VERIFY_CHAIN_INTEGRITY = /* GraphQL */ `
  mutation VerifyChainIntegrity($date: String!, $tenantId: String) {
    verifyChainIntegrity(date: $date, tenantId: $tenantId) {
      valid eventsChecked chainHead manifestMatch firstMismatchSeq sealed
    }
  }
`;
export interface VerifyChainIntegrityResult {
  verifyChainIntegrity: ChainVerifyResult;
}

export const GENERATE_SOC2_PACK = /* GraphQL */ `
  mutation GenerateSoc2Pack($from: DateTime!, $to: DateTime!) {
    generateSoc2Pack(from: $from, to: $to) { ${COMPLIANCE_JOB_FIELDS} }
  }
`;
export interface GenerateSoc2PackResult {
  generateSoc2Pack: ComplianceJob;
}

export const GENERATE_AI_DECISION_LOG = /* GraphQL */ `
  mutation GenerateAiDecisionLog($from: DateTime!, $to: DateTime!, $agentId: String) {
    generateAiDecisionLog(from: $from, to: $to, agentId: $agentId) { ${COMPLIANCE_JOB_FIELDS} }
  }
`;
export interface GenerateAiDecisionLogResult {
  generateAiDecisionLog: ComplianceJob;
}

export const COMPLIANCE_OPERATION = /* GraphQL */ `
  query ComplianceOperation($id: ID!) {
    complianceOperation(id: $id) { ${COMPLIANCE_JOB_FIELDS} }
  }
`;
export interface ComplianceOperationResult {
  complianceOperation: ComplianceJob | null;
}

// ===========================================================================
// Tier 2a: eval (eval-service) — eval flywheel: suites/runs/gates/canaries.
// ===========================================================================
const EVAL_SUITE_FIELDS = /* GraphQL */ `
  id urn suiteId agentKey version datasets scorers gateRule baselineVersion judgeLadderPin minCases createdAt
`;

export const EVAL_SUITE = /* GraphQL */ `
  query EvalSuite($suiteId: String!, $version: Int) {
    evalSuite(suiteId: $suiteId, version: $version) { ${EVAL_SUITE_FIELDS} }
  }
`;
export interface EvalSuiteResult {
  evalSuite: EvalSuite | null;
}

export const CREATE_EVAL_SUITE = /* GraphQL */ `
  mutation CreateEvalSuite($input: CreateEvalSuiteInput!) {
    createEvalSuite(input: $input) { ${EVAL_SUITE_FIELDS} }
  }
`;
export interface CreateEvalSuiteResult {
  createEvalSuite: EvalSuite;
}
export type { CreateEvalSuiteInput };

export const UPDATE_EVAL_SUITE = /* GraphQL */ `
  mutation UpdateEvalSuite($input: UpdateEvalSuiteInput!) {
    updateEvalSuite(input: $input) { ${EVAL_SUITE_FIELDS} }
  }
`;
export interface UpdateEvalSuiteResult {
  updateEvalSuite: EvalSuite;
}
export type { UpdateEvalSuiteInput };

const EVAL_RUN_LIST_FIELDS = /* GraphQL */ `
  id urn trigger agentKey candidate status totals costUsd costCapUsd startedBy createdAt updatedAt
`;

export const EVAL_RUNS = /* GraphQL */ `
  query EvalRuns($agentKey: String, $trigger: String, $first: Int, $after: String) {
    evalRuns(agentKey: $agentKey, trigger: $trigger, first: $first, after: $after) {
      nodes { ${EVAL_RUN_LIST_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface EvalRunsResult {
  evalRuns: Connection<EvalRun>;
}

export const EVAL_RUN = /* GraphQL */ `
  query EvalRunDetail($id: ID!) {
    evalRun(id: $id) {
      id urn trigger agentKey candidate baseline suitePins memorySnapshotVer status totals
      costUsd costCapUsd startedBy createdAt updatedAt
      cases { id caseId scorerKey scorerVersion score passed details traceRef latencyMs costUsd weight }
      suite { ${EVAL_SUITE_FIELDS} }
      gate { id urn gateRunId runId agentKey contentDigest suiteId suiteVersion datasetVersion gatePassed verdicts failedCasesSample reportUrl createdAt }
    }
  }
`;
export interface EvalRunResult {
  evalRun: EvalRun | null;
}

export const CREATE_EVAL_RUN = /* GraphQL */ `
  mutation CreateEvalRun($input: CreateEvalRunInput!) {
    createEvalRun(input: $input) { ${EVAL_RUN_LIST_FIELDS} }
  }
`;
export interface CreateEvalRunResult {
  createEvalRun: EvalRun;
}
export type { CreateEvalRunInput };

export const CANCEL_EVAL_RUN = /* GraphQL */ `
  mutation CancelEvalRun($id: ID!) {
    cancelEvalRun(id: $id) { ${EVAL_RUN_LIST_FIELDS} }
  }
`;
export interface CancelEvalRunResult {
  cancelEvalRun: EvalRun;
}

const EVAL_DATASET_FIELDS = /* GraphQL */ `
  id urn datasetKey agentKey version status description caseCount provenanceSummary
  frozenBy frozenAt createdBy createdAt updatedAt
`;

export const EVAL_DATASETS = /* GraphQL */ `
  query EvalDatasets($agentKey: String, $first: Int, $after: String) {
    evalDatasets(agentKey: $agentKey, first: $first, after: $after) {
      nodes { ${EVAL_DATASET_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface EvalDatasetsResult {
  evalDatasets: Connection<EvalDataset>;
}

export const CREATE_EVAL_DATASET = /* GraphQL */ `
  mutation CreateEvalDataset($input: CreateEvalDatasetInput!) {
    createEvalDataset(input: $input) { ${EVAL_DATASET_FIELDS} }
  }
`;
export interface CreateEvalDatasetResult {
  createEvalDataset: EvalDataset;
}
export type { CreateEvalDatasetInput };

export const FREEZE_EVAL_DATASET = /* GraphQL */ `
  mutation FreezeEvalDataset($datasetKey: String!, $version: Int!) {
    freezeEvalDataset(datasetKey: $datasetKey, version: $version) { ${EVAL_DATASET_FIELDS} }
  }
`;
export interface FreezeEvalDatasetResult {
  freezeEvalDataset: EvalDataset;
}

const EVAL_CASE_FIELDS = /* GraphQL */ `
  id urn datasetKey datasetVersion input expected source sourceRef tags weight status
  anonymizationAttestedBy createdAt updatedAt
`;

export const EVAL_CASES = /* GraphQL */ `
  query EvalCases($datasetKey: String, $datasetVersion: Int, $status: String, $source: String, $first: Int, $after: String) {
    evalCases(datasetKey: $datasetKey, datasetVersion: $datasetVersion, status: $status, source: $source, first: $first, after: $after) {
      nodes { ${EVAL_CASE_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface EvalCasesResult {
  evalCases: Connection<EvalCase>;
}

export const CREATE_EVAL_CASE = /* GraphQL */ `
  mutation CreateEvalCase($input: CreateEvalCaseInput!) {
    createEvalCase(input: $input) { ${EVAL_CASE_FIELDS} }
  }
`;
export interface CreateEvalCaseResult {
  createEvalCase: EvalCase;
}
export type { CreateEvalCaseInput };

export const PROMOTE_EVAL_CASE = /* GraphQL */ `
  mutation PromoteEvalCase($id: ID!) { promoteEvalCase(id: $id) { ${EVAL_CASE_FIELDS} } }
`;
export interface PromoteEvalCaseResult {
  promoteEvalCase: EvalCase;
}

export const ATTEST_EVAL_CASE = /* GraphQL */ `
  mutation AttestEvalCase($id: ID!, $attestedBy: String!) {
    attestEvalCase(id: $id, attestedBy: $attestedBy) { ${EVAL_CASE_FIELDS} }
  }
`;
export interface AttestEvalCaseResult {
  attestEvalCase: EvalCase;
}

export const REJECT_EVAL_CASE = /* GraphQL */ `
  mutation RejectEvalCase($id: ID!) { rejectEvalCase(id: $id) { ${EVAL_CASE_FIELDS} } }
`;
export interface RejectEvalCaseResult {
  rejectEvalCase: EvalCase;
}

export const RETIRE_EVAL_CASE = /* GraphQL */ `
  mutation RetireEvalCase($id: ID!) { retireEvalCase(id: $id) { ${EVAL_CASE_FIELDS} } }
`;
export interface RetireEvalCaseResult {
  retireEvalCase: EvalCase;
}

export const UPDATE_EVAL_CASE = /* GraphQL */ `
  mutation UpdateEvalCase($id: ID!, $patch: EvalCasePatchInput!) {
    updateEvalCase(id: $id, patch: $patch) { ${EVAL_CASE_FIELDS} }
  }
`;
export interface UpdateEvalCaseResult {
  updateEvalCase: EvalCase;
}
export type { EvalCasePatchInput };

const EVAL_SCORER_FIELDS = /* GraphQL */ `
  id urn scorerKey version kind gateEligible configSchema applicableExpectedKinds
  imageRef judgePromptRef judgePromptVer judgeAgreement status createdAt
`;

export const EVAL_SCORERS = /* GraphQL */ `
  query EvalScorers($first: Int, $after: String) {
    evalScorers(first: $first, after: $after) {
      nodes { ${EVAL_SCORER_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface EvalScorersResult {
  evalScorers: Connection<EvalScorer>;
}

export const CREATE_EVAL_SCORER = /* GraphQL */ `
  mutation CreateEvalScorer($input: CreateEvalScorerInput!) {
    createEvalScorer(input: $input) { ${EVAL_SCORER_FIELDS} }
  }
`;
export interface CreateEvalScorerResult {
  createEvalScorer: EvalScorer;
}
export type { CreateEvalScorerInput };

export const UPDATE_EVAL_SCORER = /* GraphQL */ `
  mutation UpdateEvalScorer($input: UpdateEvalScorerInput!) {
    updateEvalScorer(input: $input) { ${EVAL_SCORER_FIELDS} }
  }
`;
export interface UpdateEvalScorerResult {
  updateEvalScorer: EvalScorer;
}
export type { UpdateEvalScorerInput };

export const ACTIVATE_EVAL_SCORER = /* GraphQL */ `
  mutation ActivateEvalScorer($scorerKey: String!, $version: Int!) {
    activateEvalScorer(scorerKey: $scorerKey, version: $version) { ${EVAL_SCORER_FIELDS} }
  }
`;
export interface ActivateEvalScorerResult {
  activateEvalScorer: EvalScorer;
}

const EVAL_CANARY_FIELDS = /* GraphQL */ `
  id urn comparisonId agentKey candidateVersion baselineVersion sampleSpec mode status report samples createdAt updatedAt
`;

export const EVAL_CANARY = /* GraphQL */ `
  query EvalCanary($comparisonId: String!) {
    evalCanary(comparisonId: $comparisonId) { ${EVAL_CANARY_FIELDS} }
  }
`;
export interface EvalCanaryResult {
  evalCanary: EvalCanary | null;
}

export const CREATE_EVAL_CANARY = /* GraphQL */ `
  mutation CreateEvalCanary($input: CreateEvalCanaryInput!) {
    createEvalCanary(input: $input) { ${EVAL_CANARY_FIELDS} }
  }
`;
export interface CreateEvalCanaryResult {
  createEvalCanary: EvalCanary;
}
export type { CreateEvalCanaryInput };

export const INGEST_EVAL_CANARY_SAMPLES = /* GraphQL */ `
  mutation IngestEvalCanarySamples($comparisonId: String!, $pairedScores: JSON!) {
    ingestEvalCanarySamples(comparisonId: $comparisonId, pairedScores: $pairedScores) { ${EVAL_CANARY_FIELDS} }
  }
`;
export interface IngestEvalCanarySamplesResult {
  ingestEvalCanarySamples: EvalCanary;
}

export const STOP_EVAL_CANARY = /* GraphQL */ `
  mutation StopEvalCanary($comparisonId: String!) {
    stopEvalCanary(comparisonId: $comparisonId) { ${EVAL_CANARY_FIELDS} }
  }
`;
export interface StopEvalCanaryResult {
  stopEvalCanary: EvalCanary;
}

export const EVAL_TRENDS = /* GraphQL */ `
  query EvalTrends($agentKey: String!, $scorer: String, $window: String) {
    evalTrends(agentKey: $agentKey, scorer: $scorer, window: $window) { runId agentVersion scorer mean passRate at }
  }
`;
export interface EvalTrendsResult {
  evalTrends: EvalTrendPoint[];
}

export const EVAL_SLOS = /* GraphQL */ `
  query EvalSlos($agentKey: String!, $window: String) {
    evalSlos(agentKey: $agentKey, window: $window) { agentKey agentVersion tenantId window windowStart metrics targets sampleN }
  }
`;
export interface EvalSlosResult {
  evalSlos: EvalSloRow[];
}

export const SET_EVAL_SLO_TARGETS = /* GraphQL */ `
  mutation SetEvalSloTargets($agentKey: String!, $agentVersion: String, $targets: JSON!) {
    setEvalSloTargets(agentKey: $agentKey, agentVersion: $agentVersion, targets: $targets)
  }
`;
export interface SetEvalSloTargetsResult {
  setEvalSloTargets: boolean;
}

// ===========================================================================
// Tier 2a: ai-gateway admin — provider catalog, routing ladders, ai-gateway's
// OWN LLM-spend budgets, virtual keys, guardrail policy.
// ===========================================================================
const AI_PROVIDER_FIELDS = /* GraphQL */ `
  id provider modelFamily deploymentName region cloud endpointVaultRef tpmLimit rpmLimit
  priority status circuitState healthy createdAt updatedAt
`;

export const AI_PROVIDERS = /* GraphQL */ `
  query AiProviders($first: Int, $after: String) {
    aiProviders(first: $first, after: $after) {
      nodes { ${AI_PROVIDER_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface AiProvidersResult {
  aiProviders: Connection<AiProviderDeployment>;
}

export const CREATE_AI_PROVIDER = /* GraphQL */ `
  mutation CreateAiProvider($input: CreateAiProviderInput!, $idempotencyKey: String) {
    createAiProvider(input: $input, idempotencyKey: $idempotencyKey) { ${AI_PROVIDER_FIELDS} }
  }
`;
export interface CreateAiProviderResult {
  createAiProvider: AiProviderDeployment;
}
export type { CreateAiProviderInput };

export const PATCH_AI_PROVIDER = /* GraphQL */ `
  mutation PatchAiProvider($deploymentId: ID!, $input: PatchAiProviderInput!, $force: Boolean) {
    patchAiProvider(deploymentId: $deploymentId, input: $input, force: $force) { ${AI_PROVIDER_FIELDS} }
  }
`;
export interface PatchAiProviderResult {
  patchAiProvider: AiProviderDeployment;
}
export type { PatchAiProviderInput };

export const DRAIN_AI_PROVIDER = /* GraphQL */ `
  mutation DrainAiProvider($deploymentId: ID!, $force: Boolean) {
    drainAiProvider(deploymentId: $deploymentId, force: $force) { ${AI_PROVIDER_FIELDS} }
  }
`;
export interface DrainAiProviderResult {
  drainAiProvider: AiProviderDeployment;
}

export const AI_LADDER = /* GraphQL */ `
  query AiLadder($requestClass: String!) {
    aiLadder(requestClass: $requestClass) { id requestClass scope rungs version maxRung }
  }
`;
export interface AiLadderResult {
  aiLadder: AiModelLadder | null;
}

export const PUT_AI_LADDER = /* GraphQL */ `
  mutation PutAiLadder($requestClass: String!, $rungs: JSON!, $maxRung: Int, $scope: String) {
    putAiLadder(requestClass: $requestClass, rungs: $rungs, maxRung: $maxRung, scope: $scope) {
      id requestClass scope rungs version maxRung
    }
  }
`;
export interface PutAiLadderResult {
  putAiLadder: AiModelLadder;
}

const AI_BUDGET_FIELDS = /* GraphQL */ `id urn scopeType scopeRef window limitUsd degradePct status createdAt updatedAt`;

export const AI_BUDGETS = /* GraphQL */ `
  query AiBudgets($scopeType: String, $first: Int, $after: String) {
    aiBudgets(scopeType: $scopeType, first: $first, after: $after) {
      nodes { ${AI_BUDGET_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface AiBudgetsResult {
  aiBudgets: Connection<AiBudget>;
}

export const CREATE_AI_BUDGET = /* GraphQL */ `
  mutation CreateAiBudget($input: CreateAiBudgetInput!, $idempotencyKey: String) {
    createAiBudget(input: $input, idempotencyKey: $idempotencyKey) { ${AI_BUDGET_FIELDS} }
  }
`;
export interface CreateAiBudgetResult {
  createAiBudget: AiBudget;
}
export type { CreateAiBudgetInput };

export const UPDATE_AI_BUDGET = /* GraphQL */ `
  mutation UpdateAiBudget($id: ID!, $input: PatchAiBudgetInput!) {
    updateAiBudget(id: $id, input: $input) { ${AI_BUDGET_FIELDS} }
  }
`;
export interface UpdateAiBudgetResult {
  updateAiBudget: AiBudget;
}
export type { PatchAiBudgetInput };

export const DELETE_AI_BUDGET = /* GraphQL */ `
  mutation DeleteAiBudget($id: ID!) { deleteAiBudget(id: $id) { ${AI_BUDGET_FIELDS} } }
`;
export interface DeleteAiBudgetResult {
  deleteAiBudget: AiBudget;
}

export const AI_SPEND = /* GraphQL */ `
  query AiSpend($scopeType: String!, $scopeRef: String!, $window: String) {
    aiSpend(scopeType: $scopeType, scopeRef: $scopeRef, window: $window) {
      budgetId scopeType scopeRef window windowStart limitUsd spendUsd reservedUsd resetAt
    }
  }
`;
export interface AiSpendResult {
  aiSpend: AiSpendRow[];
}

// ADDED (provider-agnostic + cost-detail): real per-provider/model breakdown.
const AI_COST_ROLLUP_FIELDS = /* GraphQL */ `provider model modelAlias requestClass requests inputTokens outputTokens costUsd`;
export const AI_COST_BREAKDOWN = /* GraphQL */ `
  query AiCostBreakdown($windowHours: Int) {
    aiCostBreakdown(windowHours: $windowHours) {
      window { since hours priceVersion }
      totals { requests inputTokens outputTokens costUsd }
      byProvider { ${AI_COST_ROLLUP_FIELDS} }
      byModel { ${AI_COST_ROLLUP_FIELDS} }
      byRequestClass { ${AI_COST_ROLLUP_FIELDS} }
      detail { ${AI_COST_ROLLUP_FIELDS} }
    }
  }
`;
export interface AiCostBreakdownResult {
  aiCostBreakdown: AiCostBreakdown;
}

const AI_KEY_FIELDS = /* GraphQL */ `id urn principalType principalId allowedRequestClasses maxRung expiresAt status createdAt`;

export const AI_KEYS = /* GraphQL */ `
  query AiKeys($first: Int, $after: String) {
    aiKeys(first: $first, after: $after) {
      nodes { ${AI_KEY_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface AiKeysResult {
  aiKeys: Connection<AiVirtualKey>;
}

export const CREATE_AI_VIRTUAL_KEY = /* GraphQL */ `
  mutation CreateAiVirtualKey($input: CreateAiVirtualKeyInput!, $idempotencyKey: String) {
    createAiVirtualKey(input: $input, idempotencyKey: $idempotencyKey) { ${AI_KEY_FIELDS} secret }
  }
`;
export interface CreateAiVirtualKeyResult {
  createAiVirtualKey: AiVirtualKey;
}
export type { CreateAiVirtualKeyInput };

export const REVOKE_AI_VIRTUAL_KEY = /* GraphQL */ `
  mutation RevokeAiVirtualKey($id: ID!) { revokeAiVirtualKey(id: $id) { ${AI_KEY_FIELDS} } }
`;
export interface RevokeAiVirtualKeyResult {
  revokeAiVirtualKey: AiVirtualKey;
}

export const ROTATE_AI_VIRTUAL_KEY = /* GraphQL */ `
  mutation RotateAiVirtualKey($id: ID!) { rotateAiVirtualKey(id: $id) { ${AI_KEY_FIELDS} secret } }
`;
export interface RotateAiVirtualKeyResult {
  rotateAiVirtualKey: AiVirtualKey;
}

export const AI_GUARDRAIL_POLICY = /* GraphQL */ `
  query AiGuardrailPolicy { aiGuardrailPolicy { policy version } }
`;
export interface AiGuardrailPolicyResult {
  aiGuardrailPolicy: AiGuardrailPolicy;
}

export const PUT_AI_GUARDRAIL_POLICY = /* GraphQL */ `
  mutation PutAiGuardrailPolicy($policy: JSON!) {
    putAiGuardrailPolicy(policy: $policy) { policy version }
  }
`;
export interface PutAiGuardrailPolicyResult {
  putAiGuardrailPolicy: AiGuardrailPolicy;
}
export type {
  EvalSuite, EvalRun, EvalDataset, EvalCase, EvalScorer, EvalGateResult, EvalCanary,
  EvalTrendPoint, EvalSloRow,
  AiProviderDeployment, AiModelLadder, AiBudget, AiSpendRow, AiVirtualKey, AiGuardrailPolicy,
};

// ============================================================================
// Tier 2b: notification-service — inbox, preferences, rules, webhooks,
// templates, admin ops.
// ============================================================================
const NOTIFICATION_FIELDS = /* GraphQL */ `id urn eventType severityClass title body resourceUrn deepLink readAt createdAt`;

export const NOTIFICATIONS = /* GraphQL */ `
  query Notifications($unread: Boolean, $first: Int, $after: String) {
    notifications(unread: $unread, first: $first, after: $after) {
      nodes { ${NOTIFICATION_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface NotificationsResult {
  notifications: Connection<Notification>;
}

export const NOTIFICATION_UNREAD_COUNT = /* GraphQL */ `
  query NotificationUnreadCount {
    notificationUnreadCount
  }
`;
export interface NotificationUnreadCountResult {
  notificationUnreadCount: number;
}

export const MARK_NOTIFICATION_READ = /* GraphQL */ `
  mutation MarkNotificationRead($id: ID!) {
    markNotificationRead(id: $id)
  }
`;
export interface MarkNotificationReadResult {
  markNotificationRead: boolean;
}

export const MARK_NOTIFICATION_UNREAD = /* GraphQL */ `
  mutation MarkNotificationUnread($id: ID!) {
    markNotificationUnread(id: $id)
  }
`;
export interface MarkNotificationUnreadResult {
  markNotificationUnread: boolean;
}

export const MARK_ALL_NOTIFICATIONS_READ = /* GraphQL */ `
  mutation MarkAllNotificationsRead {
    markAllNotificationsRead
  }
`;
export interface MarkAllNotificationsReadResult {
  markAllNotificationsRead: number;
}

const NOTIFICATION_PREFERENCES_FIELDS = /* GraphQL */ `channelOverrides mutes quietHours digestConfig updatedAt`;

export const NOTIFICATION_PREFERENCES = /* GraphQL */ `
  query NotificationPreferences {
    notificationPreferences { ${NOTIFICATION_PREFERENCES_FIELDS} }
  }
`;
export interface NotificationPreferencesResult {
  notificationPreferences: NotificationPreferences;
}

export const UPDATE_NOTIFICATION_PREFERENCES = /* GraphQL */ `
  mutation UpdateNotificationPreferences($input: NotificationPreferencesInput!, $idempotencyKey: String) {
    updateNotificationPreferences(input: $input, idempotencyKey: $idempotencyKey) { ${NOTIFICATION_PREFERENCES_FIELDS} }
  }
`;
export interface UpdateNotificationPreferencesResult {
  updateNotificationPreferences: NotificationPreferences;
}

const NOTIFICATION_RULE_FIELDS = /* GraphQL */ `id scope subjectType subjectId eventTypes resourceFilter channels digestEnabled digestWindow active createdBy createdAt updatedAt`;

export const NOTIFICATION_RULES = /* GraphQL */ `
  query NotificationRules($first: Int, $after: String) {
    notificationRules(first: $first, after: $after) {
      nodes { ${NOTIFICATION_RULE_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface NotificationRulesResult {
  notificationRules: Connection<NotificationRule>;
}

export const CREATE_NOTIFICATION_RULE = /* GraphQL */ `
  mutation CreateNotificationRule($input: NotificationRuleInput!, $idempotencyKey: String) {
    createNotificationRule(input: $input, idempotencyKey: $idempotencyKey) { ${NOTIFICATION_RULE_FIELDS} }
  }
`;
export interface CreateNotificationRuleResult {
  createNotificationRule: NotificationRule;
}

export const UPDATE_NOTIFICATION_RULE = /* GraphQL */ `
  mutation UpdateNotificationRule($id: ID!, $input: NotificationRuleInput!, $idempotencyKey: String) {
    updateNotificationRule(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${NOTIFICATION_RULE_FIELDS} }
  }
`;
export interface UpdateNotificationRuleResult {
  updateNotificationRule: NotificationRule;
}

export const DELETE_NOTIFICATION_RULE = /* GraphQL */ `
  mutation DeleteNotificationRule($id: ID!) {
    deleteNotificationRule(id: $id)
  }
`;
export interface DeleteNotificationRuleResult {
  deleteNotificationRule: boolean;
}

const WEBHOOK_FIELDS = /* GraphQL */ `id url eventTypes active verifiedAt circuitState consecutiveFailures createdBy createdAt updatedAt`;
const WEBHOOK_FIELDS_WITH_SECRETS = /* GraphQL */ `${WEBHOOK_FIELDS} secrets { version secret createdAt expiresAt }`;

export const NOTIFICATION_WEBHOOKS = /* GraphQL */ `
  query NotificationWebhooks($first: Int, $after: String) {
    notificationWebhooks(first: $first, after: $after) {
      nodes { ${WEBHOOK_FIELDS} secrets { version createdAt expiresAt } }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface NotificationWebhooksResult {
  notificationWebhooks: Connection<WebhookEndpoint>;
}

export const CREATE_NOTIFICATION_WEBHOOK = /* GraphQL */ `
  mutation CreateNotificationWebhook($input: CreateWebhookInput!, $idempotencyKey: String) {
    createNotificationWebhook(input: $input, idempotencyKey: $idempotencyKey) { ${WEBHOOK_FIELDS_WITH_SECRETS} }
  }
`;
export interface CreateNotificationWebhookResult {
  createNotificationWebhook: WebhookEndpoint;
}

export const UPDATE_NOTIFICATION_WEBHOOK = /* GraphQL */ `
  mutation UpdateNotificationWebhook($id: ID!, $input: UpdateWebhookInput!, $idempotencyKey: String) {
    updateNotificationWebhook(id: $id, input: $input, idempotencyKey: $idempotencyKey) { ${WEBHOOK_FIELDS} secrets { version createdAt expiresAt } }
  }
`;
export interface UpdateNotificationWebhookResult {
  updateNotificationWebhook: WebhookEndpoint;
}

export const DELETE_NOTIFICATION_WEBHOOK = /* GraphQL */ `
  mutation DeleteNotificationWebhook($id: ID!) {
    deleteNotificationWebhook(id: $id)
  }
`;
export interface DeleteNotificationWebhookResult {
  deleteNotificationWebhook: boolean;
}

export const ROTATE_NOTIFICATION_WEBHOOK_SECRET = /* GraphQL */ `
  mutation RotateNotificationWebhookSecret($id: ID!, $idempotencyKey: String) {
    rotateNotificationWebhookSecret(id: $id, idempotencyKey: $idempotencyKey) { ${WEBHOOK_FIELDS_WITH_SECRETS} }
  }
`;
export interface RotateNotificationWebhookSecretResult {
  rotateNotificationWebhookSecret: WebhookEndpoint;
}

export const NOTIFICATION_WEBHOOK_DELIVERIES = /* GraphQL */ `
  query NotificationWebhookDeliveries($webhookId: ID!, $first: Int, $after: String) {
    notificationWebhookDeliveries(webhookId: $webhookId, first: $first, after: $after) {
      nodes { id eventId status attempts lastError providerMsgId nextRetryAt createdAt updatedAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface NotificationWebhookDeliveriesResult {
  notificationWebhookDeliveries: Connection<WebhookDelivery>;
}

export const REDELIVER_NOTIFICATION_WEBHOOK_DELIVERY = /* GraphQL */ `
  mutation RedeliverNotificationWebhookDelivery($webhookId: ID!, $deliveryId: ID!, $idempotencyKey: String) {
    redeliverNotificationWebhookDelivery(webhookId: $webhookId, deliveryId: $deliveryId, idempotencyKey: $idempotencyKey)
  }
`;
export interface RedeliverNotificationWebhookDeliveryResult {
  redeliverNotificationWebhookDelivery: boolean;
}

const NOTIFICATION_TEMPLATE_FIELDS = /* GraphQL */ `id key channel locale version subjectTpl bodyHtmlTpl bodyTextTpl status publishedAt createdBy createdAt`;

export const NOTIFICATION_TEMPLATES = /* GraphQL */ `
  query NotificationTemplates($key: String!) {
    notificationTemplates(key: $key) { ${NOTIFICATION_TEMPLATE_FIELDS} }
  }
`;
export interface NotificationTemplatesResult {
  notificationTemplates: NotificationTemplate[];
}

export const CREATE_NOTIFICATION_TEMPLATE = /* GraphQL */ `
  mutation CreateNotificationTemplate($input: CreateNotificationTemplateInput!, $idempotencyKey: String) {
    createNotificationTemplate(input: $input, idempotencyKey: $idempotencyKey) { ${NOTIFICATION_TEMPLATE_FIELDS} }
  }
`;
export interface CreateNotificationTemplateResult {
  createNotificationTemplate: NotificationTemplate;
}

export const PUBLISH_NOTIFICATION_TEMPLATE = /* GraphQL */ `
  mutation PublishNotificationTemplate($key: String!, $templateId: ID!, $idempotencyKey: String) {
    publishNotificationTemplate(key: $key, templateId: $templateId, idempotencyKey: $idempotencyKey) { ${NOTIFICATION_TEMPLATE_FIELDS} }
  }
`;
export interface PublishNotificationTemplateResult {
  publishNotificationTemplate: NotificationTemplate;
}

export const PREVIEW_NOTIFICATION_TEMPLATE = /* GraphQL */ `
  mutation PreviewNotificationTemplate($key: String!, $channel: String, $locale: String, $sampleEvent: JSON) {
    previewNotificationTemplate(key: $key, channel: $channel, locale: $locale, sampleEvent: $sampleEvent) {
      subject html text
    }
  }
`;
export interface PreviewNotificationTemplateResult {
  previewNotificationTemplate: NotificationTemplatePreview;
}

export const NOTIFICATION_DELIVERY_STATS = /* GraphQL */ `
  query NotificationDeliveryStats($window: String) {
    notificationDeliveryStats(window: $window) { window byChannel }
  }
`;
export interface NotificationDeliveryStatsResult {
  notificationDeliveryStats: NotificationDeliveryStats;
}

export const EMAIL_SUPPRESSIONS = /* GraphQL */ `
  query EmailSuppressions {
    emailSuppressions { id emailHash reason createdAt clearedAt }
  }
`;
export interface EmailSuppressionsResult {
  emailSuppressions: EmailSuppression[];
}

export const CLEAR_EMAIL_SUPPRESSION = /* GraphQL */ `
  mutation ClearEmailSuppression($emailHash: String!) {
    clearEmailSuppression(emailHash: $emailHash)
  }
`;
export interface ClearEmailSuppressionResult {
  clearEmailSuppression: boolean;
}

// ============================================================================
// Tier 2b: tool-plane registry admin — catalog, lifecycle, enablement, BYO.
// ============================================================================
const TOOL_FIELDS = /* GraphQL */ `toolId displayName ownerService ownerTeam enabledByDefault sideEffects tags createdAt updatedAt`;
const TOOL_VERSION_FIELDS = /* GraphQL */ `toolId version status semanticDescription permissionTier costWeight sideEffects declaredSla deprecationEndsAt publishedAt`;

export const TOOLS = /* GraphQL */ `
  query Tools($first: Int, $after: String, $ownerService: String) {
    tools(first: $first, after: $after, ownerService: $ownerService) {
      nodes { ${TOOL_FIELDS} }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface ToolsResult {
  tools: Connection<Tool>;
}

export const TOOL_HEALTH = /* GraphQL */ `
  query ToolHealth($toolId: ID!) {
    toolHealth(toolId: $toolId) {
      toolId
      versions { version status declaredSla health }
    }
  }
`;
export interface ToolHealthResult {
  toolHealth: ToolHealth | null;
}

export const TOOL_SCHEMA = /* GraphQL */ `
  query ToolSchema($toolId: ID!, $version: String) {
    toolSchema(toolId: $toolId, version: $version) { toolId version inputSchema outputSchema }
  }
`;
export interface ToolSchemaResult {
  toolSchema: ToolSchema | null;
}

export const REGISTER_TOOL = /* GraphQL */ `
  mutation RegisterTool($input: RegisterToolInput!, $idempotencyKey: String) {
    registerTool(input: $input, idempotencyKey: $idempotencyKey) { ${TOOL_FIELDS} }
  }
`;
export interface RegisterToolResult {
  registerTool: Tool;
}

export const ADD_TOOL_VERSION = /* GraphQL */ `
  mutation AddToolVersion($toolId: ID!, $input: AddToolVersionInput!, $idempotencyKey: String) {
    addToolVersion(toolId: $toolId, input: $input, idempotencyKey: $idempotencyKey) { ${TOOL_VERSION_FIELDS} }
  }
`;
export interface AddToolVersionResult {
  addToolVersion: ToolVersion;
}

export const PUBLISH_TOOL_VERSION = /* GraphQL */ `
  mutation PublishToolVersion($toolId: ID!, $version: String!, $idempotencyKey: String) {
    publishToolVersion(toolId: $toolId, version: $version, idempotencyKey: $idempotencyKey) { ${TOOL_VERSION_FIELDS} }
  }
`;
export interface PublishToolVersionResult {
  publishToolVersion: ToolVersion;
}

export const DEPRECATE_TOOL_VERSION = /* GraphQL */ `
  mutation DeprecateToolVersion($toolId: ID!, $version: String!, $deprecationEndsAt: DateTime, $idempotencyKey: String) {
    deprecateToolVersion(toolId: $toolId, version: $version, deprecationEndsAt: $deprecationEndsAt, idempotencyKey: $idempotencyKey) {
      status deprecationEndsAt
    }
  }
`;
export interface DeprecateToolVersionResult {
  deprecateToolVersion: ToolVersionLifecycleResult;
}

export const RETIRE_TOOL_VERSION = /* GraphQL */ `
  mutation RetireToolVersion($toolId: ID!, $version: String!, $force: Boolean, $reason: String, $idempotencyKey: String) {
    retireToolVersion(toolId: $toolId, version: $version, force: $force, reason: $reason, idempotencyKey: $idempotencyKey) {
      status deprecationEndsAt
    }
  }
`;
export interface RetireToolVersionResult {
  retireToolVersion: ToolVersionLifecycleResult;
}

export const SET_TOOL_ENABLEMENT = /* GraphQL */ `
  mutation SetToolEnablement($toolId: ID!, $input: SetToolEnablementInput!, $idempotencyKey: String) {
    setToolEnablement(toolId: $toolId, input: $input, idempotencyKey: $idempotencyKey) {
      toolId enabled maxTierOverride argumentConstraints rateLimitOverride updatedAt
    }
  }
`;
export interface SetToolEnablementResult {
  setToolEnablement: TenantToolSettings;
}

const BYO_FIELDS = /* GraphQL */ `id manifest endpointUrl authMethod requestedTier egressDescription status decidedBy decisionMessage createdAt`;

export const BYO_SUBMISSIONS = /* GraphQL */ `
  query ByoSubmissions($status: String) {
    byoSubmissions(status: $status) { ${BYO_FIELDS} }
  }
`;
export interface ByoSubmissionsResult {
  byoSubmissions: ByoSubmission[];
}

export const SUBMIT_BYO_TOOL = /* GraphQL */ `
  mutation SubmitByoTool($input: SubmitByoToolInput!, $idempotencyKey: String) {
    submitByoTool(input: $input, idempotencyKey: $idempotencyKey) { ${BYO_FIELDS} }
  }
`;
export interface SubmitByoToolResult {
  submitByoTool: ByoSubmission;
}

export const APPROVE_BYO_TOOL = /* GraphQL */ `
  mutation ApproveByoTool($id: ID!, $message: String, $idempotencyKey: String) {
    approveByoTool(id: $id, message: $message, idempotencyKey: $idempotencyKey) { id status decidedBy }
  }
`;
export interface ApproveByoToolResult {
  approveByoTool: ByoDecision;
}

export const REJECT_BYO_TOOL = /* GraphQL */ `
  mutation RejectByoTool($id: ID!, $message: String, $idempotencyKey: String) {
    rejectByoTool(id: $id, message: $message, idempotencyKey: $idempotencyKey) { id status decidedBy }
  }
`;
export interface RejectByoToolResult {
  rejectByoTool: ByoDecision;
}

// ============================================================================
// Tier 2b: agent-runtime catalog/registry — definitions, versions, publish,
// tenant config, run history.
// ============================================================================
export const AGENT_DEFINITIONS = /* GraphQL */ `
  query AgentDefinitions {
    agentDefinitions {
      agentKey displayName description ownerTeam defaultWriteMode status latestPublishedVersion
    }
  }
`;
export interface AgentDefinitionsResult {
  agentDefinitions: AgentDefinition[];
}

export const AGENT_VERSIONS = /* GraphQL */ `
  query AgentVersions($agentKey: String!) {
    agentVersions(agentKey: $agentKey) {
      agentKey version status graphRef graphDigest guardrailProfile evalGateResultId toolset modelConfig
    }
  }
`;
export interface AgentVersionsResult {
  agentVersions: AgentVersionInfo[];
}

export const PUBLISH_AGENT_VERSION = /* GraphQL */ `
  mutation PublishAgentVersion($agentKey: String!, $version: Int!, $force: Boolean, $reason: String, $idempotencyKey: String) {
    publishAgentVersion(agentKey: $agentKey, version: $version, force: $force, reason: $reason, idempotencyKey: $idempotencyKey) {
      agentKey version status
    }
  }
`;
export interface PublishAgentVersionResult {
  publishAgentVersion: AgentVersionPublishResult;
}

const TENANT_AGENT_CONFIG_FIELDS = /* GraphQL */ `agentKey configured enabled pinnedVersion promptParams autoExecutePolicy selfApproval`;

export const TENANT_AGENT_CONFIG = /* GraphQL */ `
  query TenantAgentConfig($agentKey: String!) {
    tenantAgentConfig(agentKey: $agentKey) { ${TENANT_AGENT_CONFIG_FIELDS} }
  }
`;
export interface TenantAgentConfigResult {
  tenantAgentConfig: TenantAgentConfig | null;
}

export const PUT_TENANT_AGENT_CONFIG = /* GraphQL */ `
  mutation PutTenantAgentConfig($agentKey: String!, $input: TenantAgentConfigInput!, $idempotencyKey: String) {
    putTenantAgentConfig(agentKey: $agentKey, input: $input, idempotencyKey: $idempotencyKey) { ${TENANT_AGENT_CONFIG_FIELDS} }
  }
`;
export interface PutTenantAgentConfigResult {
  putTenantAgentConfig: TenantAgentConfig;
}

// BRD 53 inc2b: author a tenant custom agent + guardrail envelope (inc2).
export interface CreateCustomAgentInput {
  displayName: string;
  persona: string;
  systemPrompt?: string;
  allowedTools: string[];
  proposeTool?: string | null;
  dataScopeWorkspaces?: string[];
  budgetMaxTokensPerSession?: number;
  blockPiiEgress?: boolean;
  redactPii?: boolean;
}
export interface CustomAgentResult {
  agentKey: string;
  status: string;
  graphRef: string;
  allowedTools: string[];
  persona: string;
  ownerTenant: string;
  guardrailPolicy?: Record<string, unknown>;
}
export const CREATE_CUSTOM_AGENT = /* GraphQL */ `
  mutation CreateCustomAgent($input: CreateCustomAgentInput!) {
    createCustomAgent(input: $input) {
      agentKey status graphRef allowedTools persona ownerTenant guardrailPolicy
    }
  }
`;
export interface CreateCustomAgentResult {
  createCustomAgent: CustomAgentResult;
}

// BRD 53 inc3: persona auto-binding + operator ceilings.
export interface PersonaBinding { role: string; agentKey: string }
export interface AutobindResult { created: PersonaBinding[]; skipped: PersonaBinding[] }
export const AUTOBIND_PERSONA_COPILOTS = /* GraphQL */ `
  mutation AutobindPersonaCopilots($roles: [String!]!, $proposeTool: String) {
    autobindPersonaCopilots(roles: $roles, proposeTool: $proposeTool) {
      created { role agentKey }
      skipped { role agentKey }
    }
  }
`;
export interface AutobindPersonaCopilotsResult { autobindPersonaCopilots: AutobindResult }

export interface AgentCeilings {
  maxBudgetTokens: number;
  maxTier: string;
  updatedAt?: string | null;
  updatedBy?: string | null;
}
export const AGENT_CEILINGS = /* GraphQL */ `
  query AgentCeilings { agentCeilings { maxBudgetTokens maxTier updatedAt updatedBy } }
`;
export interface AgentCeilingsResult { agentCeilings: AgentCeilings }
export const SET_AGENT_CEILINGS = /* GraphQL */ `
  mutation SetAgentCeilings($maxBudgetTokens: Int!, $maxTier: String!) {
    setAgentCeilings(maxBudgetTokens: $maxBudgetTokens, maxTier: $maxTier) {
      maxBudgetTokens maxTier updatedAt updatedBy
    }
  }
`;
export interface SetAgentCeilingsResult { setAgentCeilings: AgentCeilings }

export const AGENT_RUNS = /* GraphQL */ `
  query AgentRuns($agentKey: String, $first: Int) {
    agentRuns(agentKey: $agentKey, first: $first) {
      nodes { id urn sessionId agentKey agentVersion status principalType usage createdAt }
      pageInfo { nextCursor hasMore }
    }
  }
`;
export interface AgentRunsResult {
  agentRuns: Connection<AgentRunListItem>;
}

// ---- inc11: domain ontology (governed entity-TYPE registry) -----------------
export interface OntologyAttribute {
  name: string;
  dataType: string | null;
}
export interface OntologyRelationship {
  name: string;
  target: string;
  cardinality: string | null;
}
export interface OntologyEntity {
  id: string;
  entityKey: string;
  workspaceId: string;
  name: string;
  description: string;
  createdAt: string | null;
  attributes: OntologyAttribute[];
  relationships: OntologyRelationship[];
}
export interface OntologyEntitiesResult {
  ontologyEntities: OntologyEntity[];
}
export interface CreateOntologyEntityResult {
  createOntologyEntity: { id: string; entityKey: string; name: string };
}
export interface DeleteOntologyEntityResult {
  deleteOntologyEntity: boolean;
}

export const ONTOLOGY_ENTITIES = /* GraphQL */ `
  query OntologyEntities($workspaceId: ID) {
    ontologyEntities(workspaceId: $workspaceId) {
      id
      entityKey
      workspaceId
      name
      description
      createdAt
      attributes { name dataType }
      relationships { name target cardinality }
    }
  }
`;

export const CREATE_ONTOLOGY_ENTITY = /* GraphQL */ `
  mutation CreateOntologyEntity($input: CreateOntologyEntityInput!) {
    createOntologyEntity(input: $input) { id entityKey name }
  }
`;

export const DELETE_ONTOLOGY_ENTITY = /* GraphQL */ `
  mutation DeleteOntologyEntity($entityKey: ID!, $workspaceId: ID!) {
    deleteOntologyEntity(entityKey: $entityKey, workspaceId: $workspaceId)
  }
`;
