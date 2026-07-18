"use client";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";
import { graphqlRequest } from "./client";
import { qk } from "./keys";
import * as ops from "./operations";
import type { CrossFilterVar } from "@/lib/charts/crossfilter";
import type {
  RowFilterInput,
  DatasetRowsResult,
  DatasetAggregateResult,
  CreateCasesInput,
  CreateCasesResult,
  AuditEventsFilter,
  CaseFilter,
  CasePatchInput,
  Connection,
  ConnectionTestResult,
  CreateChartInput,
  CreateConnectionInput,
  CreateWritebackInput,
  CreateDecisionModelInput,
  ResolveEntitiesInput,
  ProposeEntityMergeInput,
  MaterializeResolvedInput,
  Writeback,
  CreateDashboardInput,
  CreateExperimentInput,
  CreateInferenceJobInput,
  CreatePipelineInput,
  UpdatePipelineInput,
  CreatePipelineScheduleInput,
  CreateWorkspaceInput,
  PipelineRun,
  CreateTeamInput,
  UpdateTeamInput,
  // Tier 4b: identity/rbac admin (lifecycle, roles, grants, bulk membership).
  UpdateWorkspaceInput,
  CreateGroupInput,
  UpdateGroupInput,
  GroupMemberOpInput,
  CreateRoleInput,
  UpdateRoleInput,
  CreateServiceAccountInput,
  CreateContentGrantInput,
  CreateBudgetInput,
  UpdateBudgetInput,
  CreateRateCardInput,
  CreateIngestionInput,
  CreateUploadInput,
  CompleteUploadInput,
  // Tier 4a: data-plane secondary CRUD/lifecycle.
  SavedQueryInput,
  UpdateConnectionInput,
  ConnectionPreviewInput,
  CreateIngestionScheduleInput,
  UpdateIngestionScheduleInput,
  CreateVerifiedQueryInput,
  UpdateVerifiedQueryInput,
  DatasetFilter,
  DecisionInput,
  InviteUserInput,
  JSONValue,
  PipelineDefinition,
  Proposal,
  CreateReportSubscriptionInput,
  UpdateReportSubscriptionInput,
  UpdateChartInput,
  UpdateDashboardInput,
  CreateSemanticModelInput,
  CompileSemanticModelInput,
  ErasureRequest,
  ExplainAuthzInput,
  ComplianceJob,
  CreateEvalSuiteInput,
  UpdateEvalSuiteInput,
  CreateEvalRunInput,
  CreateEvalDatasetInput,
  CreateEvalCaseInput,
  EvalCasePatchInput,
  CreateEvalScorerInput,
  UpdateEvalScorerInput,
  CreateEvalCanaryInput,
  CreateAiProviderInput,
  PatchAiProviderInput,
  CreateAiBudgetInput,
  PatchAiBudgetInput,
  CreateAiVirtualKeyInput,
  // Tier 4b: case ops.
  Case,
  CaseOperation,
  CreateDispositionInput,
  UpdateDispositionInput,
  CreateCaseFieldInput,
  UpdateCaseFieldInput,
  CaseSlaPolicyInput,
  // Tier 4b: ml ops (register/notes/cards + inference lifecycle/schedules).
  RegisterRunInput,
  UpdateExperimentInput,
  ModelCardOverlayInput,
  ValidateInferenceInput,
  CreateInferenceScheduleInput,
  UpdateInferenceScheduleInput,
} from "./types";

const PAGE = 50;

function flatten<T>(pages: { nodes: T[]; pageInfo: { nextCursor: string | null; hasMore: boolean } }[]): T[] {
  return pages.flatMap((p) => p.nodes);
}

/* ------- platform ------- */
export function useMe() {
  return useQuery({
    queryKey: qk.me(),
    queryFn: () => graphqlRequest<ops.MeResult>(ops.ME),
    staleTime: 5 * 60_000,
  });
}

/* ------- data ------- */
export function useDatasets(vars: { q?: string; filter?: DatasetFilter } = {}) {
  return useInfiniteQuery({
    queryKey: qk.datasets(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.DatasetsResult>(ops.DATASETS, { first: PAGE, after: pageParam, ...vars }).then(
        (r) => r.datasets,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useDataset(id: string) {
  return useQuery({
    queryKey: qk.dataset(id),
    queryFn: () => graphqlRequest<ops.DatasetResult>(ops.DATASET, { id }),
    enabled: !!id,
  });
}

/** Server-paged/sorted/filtered dataset row browse (the data grid backend).
 * keepPreviousData keeps the current page visible while the next loads. */
export function useDatasetRows(
  datasetId: string,
  vars: {
    offset: number;
    limit: number;
    sort?: string | null;
    dir?: string | null;
    filters?: RowFilterInput[];
  },
  opts?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: qk.datasetRows(datasetId, vars),
    queryFn: () =>
      graphqlRequest<DatasetRowsResult>(ops.DATASET_ROWS, {
        datasetId,
        offset: vars.offset,
        limit: vars.limit,
        sort: vars.sort ?? null,
        dir: vars.dir ?? null,
        filters: vars.filters ?? [],
      }),
    enabled: (opts?.enabled ?? true) && !!datasetId,
    placeholderData: (prev) => prev,
  });
}

/** Resolve a dashboard chart selection to its backing dataset + physical column
 * (drill-through → dataset browse → create cases). Enabled only when a chart +
 * dimension are set (i.e. a segment/cross-filter was chosen). */
export function useChartDrillTarget(
  chartId: string | null,
  dimension: string | null,
  opts?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: qk.chartDrillTarget(chartId ?? "", dimension ?? ""),
    queryFn: () =>
      graphqlRequest<ops.ChartDrillTargetResult>(ops.CHART_DRILL_TARGET, {
        chartId,
        dimension,
      }),
    enabled: (opts?.enabled ?? true) && !!chartId && !!dimension,
  });
}

/** Quick-chart aggregation over a raw dataset (warehouse GROUP BY via the BFF).
 * Enabled only once a dimension is chosen. */
export function useDatasetAggregate(
  datasetId: string,
  vars: { dimension: string; measure?: string | null; agg: string; limit?: number },
  opts?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: qk.datasetAggregate(datasetId, vars),
    queryFn: () =>
      graphqlRequest<DatasetAggregateResult>(ops.DATASET_AGGREGATE, {
        datasetId,
        dimension: vars.dimension,
        measure: vars.measure ?? null,
        agg: vars.agg,
        limit: vars.limit ?? 50,
      }),
    enabled: (opts?.enabled ?? true) && !!datasetId && !!vars.dimension,
    placeholderData: (prev) => prev,
  });
}

/** Create a worklist of cases from selected query/dataset rows (dedup-aware on
 * the backend). Invalidates case lists on success so the new cases appear. */
export function useCreateCases() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { input: CreateCasesInput; idempotencyKey?: string }) =>
      graphqlRequest<CreateCasesResult>(ops.CREATE_CASES, {
        input: vars.input,
        idempotencyKey: vars.idempotencyKey,
      }).then((r) => r.createCases),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
}

/** dataset-service exposes no archived-only list read — archive/restore are
 * write-only by id here (e.g. an id copied from the audit trail). */
export function useArchiveDataset() {
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      graphqlRequest<ops.ArchiveDatasetResult>(ops.ARCHIVE_DATASET, { id, force }),
  });
}

export function useRestoreDataset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.RestoreDatasetResult>(ops.RESTORE_DATASET, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "datasets"] }),
  });
}

/** Rename / edit-description of a dataset (dataset-service PATCH /datasets/{id},
 * needs dataset.dataset.update). Datasets are created via ingestion, so this is
 * the only tenant-facing edit path for the catalog name/description. */
export function useUpdateDataset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name, description }: { id: string; name?: string; description?: string }) =>
      graphqlRequest<ops.UpdateDatasetResult>(ops.UPDATE_DATASET, {
        id,
        input: { name, description },
      }).then((r) => r.updateDataset),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: qk.dataset(vars.id) });
      client.invalidateQueries({ queryKey: ["data", "datasets"] });
    },
  });
}

/* ------- data-source connections (ingestion) ------- */
export function useConnectorTypes() {
  return useQuery({
    queryKey: qk.connectorTypes(),
    queryFn: () => graphqlRequest<ops.ConnectorTypesResult>(ops.CONNECTOR_TYPES).then((r) => r.connectorTypes),
    staleTime: 30 * 60_000, // catalog is effectively static per deploy
  });
}

export function useConnections(vars: { q?: string; connectorType?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.connections(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ConnectionsResult>(ops.CONNECTIONS, {
        first: PAGE,
        after: pageParam,
        q: vars.q,
        connectorType: vars.connectorType,
      }).then((r) => r.connections),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useConnection(id: string) {
  return useQuery({
    queryKey: qk.connection(id),
    queryFn: () => graphqlRequest<ops.ConnectionDetailResult>(ops.CONNECTION, { id }),
    enabled: !!id,
  });
}

export function useCreateConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateConnectionInput) =>
      graphqlRequest<ops.CreateConnectionResult>(ops.CREATE_CONNECTION, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => {
      // New row -> refresh every connections list (all filter variants).
      client.invalidateQueries({ queryKey: ["data", "connections"] });
    },
  });
}

// ---- decision write-back / SoR sync (INS-FR-061) --------------------------
// Newest-first, bounded list — no cursor pagination (ingestion-service's own
// GET /writebacks doesn't paginate; this is an ops/admin surface, not an
// infinite-scroll one).
export function useWritebacks(vars: { status?: string; workspaceId?: string } = {}) {
  return useQuery({
    queryKey: qk.writebacks(vars),
    queryFn: () =>
      graphqlRequest<ops.WritebacksResult>(ops.WRITEBACKS, {
        status: vars.status, workspaceId: vars.workspaceId, first: 100,
      }).then((r) => r.writebacks),
  });
}

export function useWriteback(id: string) {
  return useQuery({
    queryKey: qk.writeback(id),
    queryFn: () => graphqlRequest<ops.WritebackResult>(ops.WRITEBACK, { id }).then((r) => r.writeback),
    enabled: !!id,
  });
}

export function useCreateWriteback() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateWritebackInput) =>
      graphqlRequest<ops.CreateWritebackResult>(ops.CREATE_WRITEBACK, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createWriteback),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "writebacks"] }),
  });
}

function useWritebackTransition<TResult>(doc: string, pick: (r: TResult) => Writeback) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<TResult>(doc, { id }).then(pick),
    onSuccess: (wb) => {
      client.invalidateQueries({ queryKey: ["data", "writebacks"] });
      client.invalidateQueries({ queryKey: qk.writeback(wb.id) });
    },
  });
}

// ---- BRD 54 inc2: governed decision tables ---------------------------------
export function useDecisionModels() {
  return useQuery({
    queryKey: qk.decisionModels(),
    queryFn: () => graphqlRequest<ops.DecisionModelsResult>(ops.DECISION_MODELS).then((r) => r.decisionModels),
  });
}

export function useDecisionModel(id: string) {
  return useQuery({
    queryKey: qk.decisionModel(id),
    queryFn: () => graphqlRequest<ops.DecisionModelResult>(ops.DECISION_MODEL, { id }).then((r) => r.decisionModel),
    enabled: !!id,
  });
}

export function useCreateDecisionModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDecisionModelInput) =>
      graphqlRequest<ops.CreateDecisionModelResult>(ops.CREATE_DECISION_MODEL, {
        input, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createDecisionModel),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.decisionModels() }),
  });
}

export function useDecisionModelVersions(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.decisionModelVersions(id),
    queryFn: () => graphqlRequest<ops.DecisionModelVersionsResult>(
      ops.DECISION_MODEL_VERSIONS, { id }).then((r) => r.decisionModelVersions),
    enabled: enabled && !!id,
  });
}

export function useApproveDecisionModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ApproveDecisionModelResult>(ops.APPROVE_DECISION_MODEL, {
        id, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.approveDecisionModel),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.decisionModels() }),
  });
}

export function useNewDecisionModelVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; input: CreateDecisionModelInput }) =>
      graphqlRequest<ops.NewDecisionModelVersionResult>(ops.NEW_DECISION_MODEL_VERSION, {
        id: vars.id, input: vars.input, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.newDecisionModelVersion),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.decisionModels() }),
  });
}

// ---- BRD 56: entity resolution (steward surface) ---------------------------

/** Prior resolution runs for a dataset (newest first). */
export function useResolutionRuns(datasetId: string, enabled = true) {
  return useQuery({
    queryKey: qk.resolutionRuns(datasetId),
    queryFn: () => graphqlRequest<ops.ResolutionRunsResult>(
      ops.RESOLUTION_RUNS, { datasetId, limit: 50 }).then((r) => r.resolutionRuns),
    enabled: enabled && !!datasetId,
  });
}

/** One run's resolved clusters + member lineage. */
export function useResolutionRun(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.resolutionRun(id),
    queryFn: () => graphqlRequest<ops.ResolutionRunResult>(
      ops.RESOLUTION_RUN, { id }).then((r) => r.resolutionRun),
    enabled: enabled && !!id,
  });
}

/** The below-auto merge candidates a steward reviews for a run. */
export function useMergeCandidates(runId: string, enabled = true) {
  return useQuery({
    queryKey: qk.mergeCandidates(runId),
    queryFn: () => graphqlRequest<ops.MergeCandidatesResult>(
      ops.MERGE_CANDIDATES, { runId }).then((r) => r.mergeCandidates),
    enabled: enabled && !!runId,
  });
}

/** Run + persist an entity-resolution run over a dataset. Link layer only. */
export function useResolveEntities() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { datasetId: string; input: ResolveEntitiesInput }) =>
      graphqlRequest<ops.ResolveEntitiesResultData>(ops.RESOLVE_ENTITIES, {
        datasetId: vars.datasetId, input: vars.input, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.resolveEntities),
    onSuccess: (_d, vars) =>
      client.invalidateQueries({ queryKey: qk.resolutionRuns(vars.datasetId) }),
  });
}

/** Confirm a reviewed merge candidate by opening a four-eyes proposal. A
 * DIFFERENT user approves it in the proposals inbox. */
export function useProposeEntityMerge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { input: ProposeEntityMergeInput }) =>
      graphqlRequest<ops.ProposeEntityMergeResultData>(ops.PROPOSE_ENTITY_MERGE, {
        input: vars.input, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.proposeEntityMerge),
    onSuccess: (_d, vars) =>
      client.invalidateQueries({ queryKey: qk.mergeCandidates(vars.input.runId) }),
  });
}

/** Materialize a run's resolved entities into a governed golden-record dataset. */
export function useMaterializeResolvedEntities() {
  return useMutation({
    mutationFn: (vars: { runId: string; input: MaterializeResolvedInput }) =>
      graphqlRequest<ops.MaterializeResolvedResultData>(ops.MATERIALIZE_RESOLVED, {
        runId: vars.runId, input: vars.input, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.materializeResolvedEntities),
  });
}

// ---- BRD 23: capability packs (pack-service) -------------------------------

export function usePacks() {
  return useQuery({
    queryKey: qk.packs(),
    queryFn: () => graphqlRequest<ops.PacksResult>(ops.PACKS).then((r) => r.packs),
  });
}

export function usePack(name: string, enabled = true) {
  return useQuery({
    queryKey: qk.pack(name),
    queryFn: () => graphqlRequest<ops.PackResult>(ops.PACK, { name }).then((r) => r.pack),
    enabled: enabled && !!name,
  });
}

export function usePackInstalls(workspaceId: string) {
  return useQuery({
    queryKey: qk.packInstalls(workspaceId),
    queryFn: () =>
      graphqlRequest<ops.PackInstallsResult>(ops.PACK_INSTALLS, { workspaceId }).then(
        (r) => r.packInstalls,
      ),
    enabled: !!workspaceId,
  });
}

export function usePackInstall(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.packInstall(id),
    queryFn: () =>
      graphqlRequest<ops.PackInstallResult>(ops.PACK_INSTALL, { id }).then((r) => r.packInstall),
    enabled: enabled && !!id,
  });
}

/** Dry-run: compute the install plan (create | exists | deferred), no side effects. */
export function usePlanPackInstall() {
  return useMutation({
    mutationFn: (vars: { pack: string; workspaceId: string; version?: string }) =>
      graphqlRequest<ops.PlanPackInstallResult>(ops.PLAN_PACK_INSTALL, vars).then(
        (r) => r.planPackInstall,
      ),
  });
}

/** Execute an install; materializes AS the caller + records the ledger. */
export function useInstallPack() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { pack: string; workspaceId: string; version?: string }) =>
      graphqlRequest<ops.InstallPackResult>(ops.INSTALL_PACK, {
        ...vars, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.installPack),
    onSuccess: (_d, vars) =>
      client.invalidateQueries({ queryKey: qk.packInstalls(vars.workspaceId) }),
  });
}

/** Reverse an install (reversible deletes + honest tombstones). */
export function useUninstallPack(workspaceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (installId: string) =>
      graphqlRequest<ops.UninstallPackResult>(ops.UNINSTALL_PACK, {
        installId, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.uninstallPack),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.packInstalls(workspaceId) }),
  });
}

/** Phase 2: after the semantic model is approved, materialize the dashboards. */
export function useCompletePackInstall(workspaceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (installId: string) =>
      graphqlRequest<ops.CompletePackInstallResult>(ops.COMPLETE_PACK_INSTALL, {
        installId, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.completePackInstall),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.packInstalls(workspaceId) }),
  });
}

/** Batch-run a decision table over a worklist. propose=false previews (dry-run,
 * no side effect); propose=true mints one governed proposal per matched case. */
export function useBatchEvaluateDecisionModel() {
  return useMutation({
    mutationFn: (vars: { id: string; workspaceId?: string; caseIds?: string[]; limit?: number; propose: boolean }) =>
      graphqlRequest<ops.BatchEvaluateResultData>(ops.BATCH_EVALUATE_DECISION_MODEL, {
        id: vars.id,
        input: { workspaceId: vars.workspaceId, caseIds: vars.caseIds, limit: vars.limit },
        propose: vars.propose,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.batchEvaluateDecisionModel),
  });
}

export function useApproveWriteback() {
  return useWritebackTransition<ops.ApproveWritebackResult>(ops.APPROVE_WRITEBACK, (r) => r.approveWriteback);
}

export function useRejectWriteback() {
  return useWritebackTransition<ops.RejectWritebackResult>(ops.REJECT_WRITEBACK, (r) => r.rejectWriteback);
}

export function useRetryWriteback() {
  return useWritebackTransition<ops.RetryWritebackResult>(ops.RETRY_WRITEBACK, (r) => r.retryWriteback);
}

/** Probe a connection: adhoc (type+config+secrets) during create, or by id when saved.
 * Returns the raw ConnectionTestResult so the caller can surface OK vs AUTH_FAILED. */
export function useTestConnection() {
  return useMutation<
    ConnectionTestResult,
    Error,
    { id?: string; type?: string; config?: JSONValue; secrets?: JSONValue }
  >({
    mutationFn: (vars) =>
      graphqlRequest<ops.TestConnectionResult>(ops.TEST_CONNECTION, vars).then((r) => r.testConnection),
  });
}

export function useDeleteConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteConnectionResult>(ops.DELETE_CONNECTION, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "connections"] }),
  });
}

/** Edit a saved connection. Secrets are write-only and MERGE server-side —
 * send only the keys the user actually re-entered. */
export function useUpdateConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateConnectionInput }) =>
      graphqlRequest<ops.UpdateConnectionResult>(ops.UPDATE_CONNECTION, { id, input }).then(
        (r) => r.updateConnection,
      ),
    onSuccess: (d) => {
      client.invalidateQueries({ queryKey: ["data", "connections"] });
      client.invalidateQueries({ queryKey: qk.connection(d.id) });
    },
  });
}

/** On-demand sample-rows preview from a SAVED connection (read-only downstream;
 * mutation-shaped here because it runs on click, like useCompileSemanticModel). */
export function useConnectionPreview() {
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: ConnectionPreviewInput }) =>
      graphqlRequest<ops.ConnectionPreviewResult>(ops.CONNECTION_PREVIEW, { id, input }).then(
        (r) => r.connectionPreview,
      ),
  });
}

/* ------- ingestion runs ------- */
export function useIngestions(vars: { status?: string; mode?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.ingestions(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.IngestionsResult>(ops.INGESTIONS, {
        first: PAGE,
        after: pageParam,
        status: vars.status,
        mode: vars.mode,
      }).then((r) => r.ingestions),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    // Ingestion runs advance server-side; a short refetch keeps the list fresh
    // as a fallback to the ingestion.* realtime topics.
    refetchInterval: 5_000,
  });
}

export function useCreateIngestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateIngestionInput) =>
      graphqlRequest<ops.CreateIngestionResult>(ops.CREATE_INGESTION, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestions"] }),
  });
}

/** A single ingestion by id. Used by the upload wizard's review step to poll a
 * just-created file_upload run to its terminal status (completed/failed). Polls
 * only while `enabled` and the run is non-terminal. */
export function useIngestion(id: string | null, opts: { enabled?: boolean } = {}) {
  const enabled = (opts.enabled ?? true) && !!id;
  return useQuery({
    queryKey: qk.ingestion(id ?? ""),
    queryFn: () =>
      graphqlRequest<ops.IngestionResult>(ops.INGESTION, { id }).then((r) => r.ingestion),
    enabled,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      const terminal = s === "completed" || s === "failed" || s === "cancelled" || s === "expired";
      return terminal ? false : 2_000;
    },
  });
}

/* ------- ingestion lifecycle: cancel / retry / reingest ------- */
export function useCancelIngestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.CancelIngestionResult>(ops.CANCEL_INGESTION, { id }).then((r) => r.cancelIngestion),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestions"] }),
  });
}

export function useRetryIngestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RetryIngestionResult>(ops.RETRY_INGESTION, { id }).then((r) => r.retryIngestion),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestions"] }),
  });
}

export function useReingestIngestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ReingestIngestionResult>(ops.REINGEST_INGESTION, { id }).then(
        (r) => r.reingestIngestion,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestions"] }),
  });
}

/* ------- recurring ingestion schedules ------- */
export function useIngestionSchedules() {
  return useInfiniteQuery({
    queryKey: qk.ingestionSchedules(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.IngestionSchedulesResult>(ops.INGESTION_SCHEDULES, {
        first: PAGE,
        after: pageParam,
      }).then((r) => r.ingestionSchedules),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateIngestionSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateIngestionScheduleInput) =>
      graphqlRequest<ops.CreateIngestionScheduleResult>(ops.CREATE_INGESTION_SCHEDULE, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createIngestionSchedule),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestionSchedules"] }),
  });
}

export function useUpdateIngestionSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateIngestionScheduleInput }) =>
      graphqlRequest<ops.UpdateIngestionScheduleResult>(ops.UPDATE_INGESTION_SCHEDULE, { id, input }).then(
        (r) => r.updateIngestionSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestionSchedules"] }),
  });
}

export function useDeleteIngestionSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteIngestionScheduleResult>(ops.DELETE_INGESTION_SCHEDULE, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestionSchedules"] }),
  });
}

export function usePauseIngestionSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.PauseIngestionScheduleResult>(ops.PAUSE_INGESTION_SCHEDULE, { id }).then(
        (r) => r.pauseIngestionSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestionSchedules"] }),
  });
}

export function useResumeIngestionSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ResumeIngestionScheduleResult>(ops.RESUME_INGESTION_SCHEDULE, { id }).then(
        (r) => r.resumeIngestionSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestionSchedules"] }),
  });
}

export function useRunIngestionScheduleNow() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RunIngestionScheduleNowResult>(ops.RUN_INGESTION_SCHEDULE_NOW, { id }).then(
        (r) => r.runIngestionScheduleNow,
      ),
    onSuccess: () => {
      // A fire creates a real ingestion run and bumps the schedule's last_fired_at.
      client.invalidateQueries({ queryKey: ["data", "ingestionSchedules"] });
      client.invalidateQueries({ queryKey: ["data", "ingestions"] });
    },
  });
}

/* ------- resumable uploads (session lifecycle only; chunk PUTs bypass GraphQL,
 * see services/ui-web/src/app/api/uploads/[uploadId]/parts/[n]/route.ts) ------- */
export function useCreateUpload() {
  return useMutation({
    mutationFn: (input: CreateUploadInput) =>
      graphqlRequest<ops.CreateUploadResult>(ops.CREATE_UPLOAD, { input, idempotencyKey: crypto.randomUUID() }),
  });
}

export function useUploadStatus(id: string | null) {
  return useQuery({
    queryKey: qk.upload(id ?? ""),
    queryFn: () => graphqlRequest<ops.UploadResult>(ops.UPLOAD, { id }).then((r) => r.upload),
    enabled: !!id,
  });
}

export function useCompleteUpload() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ uploadId, input }: { uploadId: string; input: CompleteUploadInput }) =>
      graphqlRequest<ops.CompleteUploadResult>(ops.COMPLETE_UPLOAD, { uploadId, input }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "ingestions"] }),
  });
}

/* ------- dataset lineage ------- */
export function useDatasetLineage(urn: string, direction = "both") {
  return useQuery({
    queryKey: qk.datasetLineage(urn, direction),
    queryFn: () =>
      graphqlRequest<ops.DatasetLineageResult>(ops.DATASET_LINEAGE, { urn, direction }).then(
        (r) => r.datasetLineage,
      ),
    enabled: !!urn,
  });
}

/* ------- dataset consumers / versions / similarity / re-profile ------- */
export function useDatasetConsumers(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.datasetConsumers(id),
    queryFn: () =>
      graphqlRequest<ops.DatasetConsumersResult>(ops.DATASET_CONSUMERS, { id }).then(
        (r) => r.datasetConsumers,
      ),
    enabled: !!id && enabled,
  });
}

export function useDatasetVersions(datasetId: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: qk.datasetVersions(datasetId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.DatasetVersionsResult>(ops.DATASET_VERSIONS, {
        datasetId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.datasetVersions),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!datasetId && enabled,
  });
}

export function useSimilarDatasets(datasetId: string, enabled = true) {
  return useQuery({
    queryKey: qk.similarDatasets(datasetId),
    queryFn: () =>
      graphqlRequest<ops.SimilarDatasetsResult>(ops.SIMILAR_DATASETS, { datasetId }).then(
        (r) => r.similarDatasets,
      ),
    enabled: !!datasetId && enabled,
  });
}

/** Manual re-profile trigger (202 async — the profile panel/versions refresh
 * as the job completes). */
export function useReprofileDataset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, versionNo }: { id: string; versionNo?: number }) =>
      graphqlRequest<ops.ReprofileDatasetResult>(ops.REPROFILE_DATASET, {
        id,
        versionNo,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.reprofileDataset),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: qk.dataset(vars.id) });
      client.invalidateQueries({ queryKey: qk.datasetVersions(vars.id) });
    },
  });
}

/* ------- queries (saved + ad-hoc SQL) ------- */
export function useSavedQueries() {
  return useInfiniteQuery({
    queryKey: qk.savedQueries({}),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.SavedQueriesResult>(ops.SAVED_QUERIES, { first: PAGE, after: pageParam }).then(
        (r) => r.savedQueries,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useSavedQuery(id: string) {
  return useQuery({
    queryKey: qk.savedQuery(id),
    queryFn: () => graphqlRequest<ops.SavedQueryResult>(ops.SAVED_QUERY, { id }),
    enabled: !!id,
  });
}

/** Run ad-hoc SQL; resolves to the first results page (columns + rows). */
export function useRunSql() {
  return useMutation({
    mutationFn: (input: ops.RunSqlInput) =>
      graphqlRequest<ops.RunSqlResult>(ops.RUN_SQL, { input }).then((r) => r.runSql),
  });
}

/** Run a saved query by id; resolves to the first results page. */
export function useRunSavedQuery() {
  return useMutation({
    mutationFn: (vars: { id: string; limit?: number }) =>
      graphqlRequest<ops.RunSavedQueryResult>(ops.RUN_SAVED_QUERY, vars).then((r) => r.runSavedQuery),
  });
}

/* ------- saved-query authoring + versions + execution history ------- */
export function useCreateSavedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: SavedQueryInput) =>
      graphqlRequest<ops.CreateSavedQueryResult>(ops.CREATE_SAVED_QUERY, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createSavedQuery),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "savedQueries"] }),
  });
}

export function useUpdateSavedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: SavedQueryInput }) =>
      graphqlRequest<ops.UpdateSavedQueryResult>(ops.UPDATE_SAVED_QUERY, { id, input }).then(
        (r) => r.updateSavedQuery,
      ),
    onSuccess: (d) => {
      client.invalidateQueries({ queryKey: ["data", "savedQueries"] });
      client.invalidateQueries({ queryKey: qk.savedQuery(d.id) });
      client.invalidateQueries({ queryKey: qk.savedQueryVersions(d.id) });
    },
  });
}

export function useDeleteSavedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteSavedQueryResult>(ops.DELETE_SAVED_QUERY, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "savedQueries"] }),
  });
}

export function useSavedQueryVersions(queryId: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: qk.savedQueryVersions(queryId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.SavedQueryVersionsResult>(ops.SAVED_QUERY_VERSIONS, {
        queryId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.savedQueryVersions),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!queryId && enabled,
  });
}

export function useQueryExecutions(vars: { status?: string; savedQueryId?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.queryExecutions(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.QueryExecutionsResult>(ops.QUERY_EXECUTIONS, {
        first: PAGE,
        after: pageParam,
        status: vars.status,
        savedQueryId: vars.savedQueryId,
      }).then((r) => r.queryExecutions),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCancelQueryExecution() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.CancelQueryExecutionResult>(ops.CANCEL_QUERY_EXECUTION, { id }).then(
        (r) => r.cancelQueryExecution,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["data", "queryExecutions"] }),
  });
}

export function useQueryStats(since?: string) {
  return useQuery({
    queryKey: qk.queryStats(since),
    queryFn: () =>
      graphqlRequest<ops.QueryStatsResult>(ops.QUERY_STATS, { since }).then((r) => r.queryStats),
  });
}

/* ------- pipelines (no-code builder) ------- */
export function usePipelineStepTypes() {
  return useQuery({
    queryKey: qk.pipelineStepTypes(),
    queryFn: () =>
      graphqlRequest<ops.PipelineStepTypesResult>(ops.PIPELINE_STEP_TYPES).then((r) => r.pipelineStepTypes),
    staleTime: 30 * 60_000, // catalog is effectively static per deploy
  });
}

export function useAlgorithmTemplates() {
  return useQuery({
    queryKey: qk.algorithmTemplates(),
    queryFn: () =>
      graphqlRequest<ops.AlgorithmTemplatesResult>(ops.ALGORITHM_TEMPLATES).then((r) => r.algorithmTemplates),
    staleTime: 30 * 60_000,
  });
}

export function usePipelineTemplates(
  vars: { q?: string; pipelineType?: string; includeArchived?: boolean } = {},
) {
  return useInfiniteQuery({
    queryKey: qk.pipelines(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.PipelineTemplatesResult>(ops.PIPELINE_TEMPLATES, {
        first: PAGE,
        after: pageParam,
        q: vars.q,
        pipelineType: vars.pipelineType,
        includeArchived: vars.includeArchived,
      }).then((r) => r.pipelineTemplates),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function usePipelineTemplate(id: string) {
  return useQuery({
    queryKey: qk.pipeline(id),
    queryFn: () => graphqlRequest<ops.PipelineTemplateResult>(ops.PIPELINE_TEMPLATE, { id }),
    enabled: !!id,
  });
}

const PIPELINE_RUN_TERMINAL = new Set(["succeeded", "failed", "cancelled", "expired", "skipped"]);

export function usePipelineRuns(vars: { templateId?: string; status?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.pipelineRuns(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.PipelineRunsResult>(ops.PIPELINE_RUNS, {
        first: PAGE,
        after: pageParam,
        templateId: vars.templateId,
        status: vars.status,
      }).then((r) => r.pipelineRuns),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    // Live status without a manual refresh: pipeline runs push via the
    // `pipeline.run.*` realtime patcher, and poll every 4s as a fallback while
    // ANY loaded run is still non-terminal (stops once all are terminal).
    refetchInterval: (query) => {
      const pages = query.state.data?.pages ?? [];
      const anyActive = pages.some((pg: { nodes: PipelineRun[] }) =>
        pg.nodes.some((n) => !PIPELINE_RUN_TERMINAL.has(String(n.status ?? "").toLowerCase())),
      );
      return anyActive ? 4_000 : false;
    },
  });
}

export function useCreatePipeline() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreatePipelineInput) =>
      graphqlRequest<ops.CreatePipelineResult>(ops.CREATE_PIPELINE, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => {
      // New template -> refresh every pipelines list (all filter variants).
      client.invalidateQueries({ queryKey: ["pipelines", "templates"] });
    },
  });
}

export function useUpdatePipeline() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; input: UpdatePipelineInput }) =>
      graphqlRequest<ops.UpdatePipelineResult>(ops.UPDATE_PIPELINE, {
        id: args.id,
        input: args.input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => {
      // Edited template -> refresh the lists AND the single-template cache (its new
      // active version + definition). The broad ["pipelines"] key covers both.
      client.invalidateQueries({ queryKey: ["pipelines"] });
    },
  });
}

/** Adhoc validation of the current canvas definition (no persistence). */
export function useValidatePipeline() {
  return useMutation({
    mutationFn: (vars: { definition: PipelineDefinition; pipelineType: string }) =>
      graphqlRequest<ops.ValidatePipelineResult>(ops.VALIDATE_PIPELINE, vars).then((r) => r.validatePipeline),
  });
}

export function useRunPipeline() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; parameters?: JSONValue }) =>
      graphqlRequest<ops.RunPipelineResult>(ops.RUN_PIPELINE, {
        id: args.id,
        input: { parameters: args.parameters },
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => {
      // A new run may change the template's list row + populates the runs list.
      client.invalidateQueries({ queryKey: ["pipelines", "templates"] });
      client.invalidateQueries({ queryKey: ["pipelines", "runs"] });
    },
  });
}

/* ------- pipeline run lifecycle: terminate / retry / manifest ------- */
export function useTerminatePipelineRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.TerminatePipelineRunResult>(ops.TERMINATE_PIPELINE_RUN, { id }).then(
        (r) => r.terminatePipelineRun,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines", "runs"] }),
  });
}

export function useRetryPipelineRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RetryPipelineRunResult>(ops.RETRY_PIPELINE_RUN, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.retryPipelineRun),
    onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines", "runs"] }),
  });
}

/** On-demand manifest fetch (mutation-shaped: runs on row-action click). */
export function usePipelineRunManifest() {
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.PipelineRunManifestResult>(ops.PIPELINE_RUN_MANIFEST, { id }).then(
        (r) => r.pipelineRunManifest,
      ),
  });
}

/* ------- pipeline template lifecycle ------- */
export function usePipelineTemplateVersions(templateId: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: qk.pipelineTemplateVersions(templateId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.PipelineTemplateVersionsResult>(ops.PIPELINE_TEMPLATE_VERSIONS, {
        templateId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.pipelineTemplateVersions),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!templateId && enabled,
  });
}

export function useClonePipelineTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ClonePipelineTemplateResult>(ops.CLONE_PIPELINE_TEMPLATE, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.clonePipelineTemplate),
    onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines", "templates"] }),
  });
}

export function useActivatePipelineTemplateVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { templateId: string; versionId: string }) =>
      graphqlRequest<ops.ActivatePipelineTemplateVersionResult>(
        ops.ACTIVATE_PIPELINE_TEMPLATE_VERSION,
        vars,
      ).then((r) => r.activatePipelineTemplateVersion),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: ["pipelines", "templates"] });
      client.invalidateQueries({ queryKey: qk.pipelineTemplateVersions(vars.templateId) });
    },
  });
}

export function useCompilePipelineTemplate() {
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.CompilePipelineTemplateResult>(ops.COMPILE_PIPELINE_TEMPLATE, { id }).then(
        (r) => r.compilePipelineTemplate,
      ),
  });
}

export function useDeletePipelineTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeletePipelineTemplateResult>(ops.DELETE_PIPELINE_TEMPLATE, { id }).then(
        (r) => r.deletePipelineTemplate,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines", "templates"] }),
  });
}

export function useRestorePipelineTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RestorePipelineTemplateResult>(ops.RESTORE_PIPELINE_TEMPLATE, { id }).then(
        (r) => r.restorePipelineTemplate,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines", "templates"] }),
  });
}

/* ------- recurring pipeline schedules (PIPE-FR-050) ------- */
export function usePipelineSchedules() {
  return useQuery({
    queryKey: qk.pipelineSchedules(),
    queryFn: () =>
      graphqlRequest<ops.PipelineSchedulesResult>(ops.PIPELINE_SCHEDULES).then((r) => r.pipelineSchedules),
  });
}

export function useCreatePipelineSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreatePipelineScheduleInput) =>
      graphqlRequest<ops.CreatePipelineScheduleResult>(ops.CREATE_PIPELINE_SCHEDULE, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createPipelineSchedule),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.pipelineSchedules() }),
  });
}

export function usePausePipelineSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.PausePipelineScheduleResult>(ops.PAUSE_PIPELINE_SCHEDULE, { id }).then(
        (r) => r.pausePipelineSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.pipelineSchedules() }),
  });
}

export function useResumePipelineSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ResumePipelineScheduleResult>(ops.RESUME_PIPELINE_SCHEDULE, { id }).then(
        (r) => r.resumePipelineSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.pipelineSchedules() }),
  });
}

export function useRunNowPipelineSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RunNowPipelineScheduleResult>(ops.RUN_NOW_PIPELINE_SCHEDULE, { id }).then(
        (r) => r.runNowPipelineSchedule,
      ),
    onSuccess: () => {
      // A fire creates a real pipeline run and bumps the schedule's lastFireAt/lastRunId.
      client.invalidateQueries({ queryKey: qk.pipelineSchedules() });
      client.invalidateQueries({ queryKey: ["pipelines", "runs"] });
    },
  });
}

export function useDeletePipelineSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeletePipelineScheduleResult>(ops.DELETE_PIPELINE_SCHEDULE, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.pipelineSchedules() }),
  });
}

/* ------- cases ------- */
export function useCaseSearch(vars: { q?: string; filter?: CaseFilter } = {}) {
  return useInfiniteQuery({
    queryKey: qk.cases(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.CaseSearchResult>(ops.CASE_SEARCH, { first: PAGE, after: pageParam, ...vars }).then(
        (r) => r.caseSearch,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCaseDetail(id: string) {
  return useQuery({
    queryKey: qk.case(id),
    queryFn: () => graphqlRequest<ops.CaseDetailResult>(ops.CASE_DETAIL, { id }),
    enabled: !!id,
  });
}

export function useUpdateCase(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: CasePatchInput) =>
      graphqlRequest<ops.UpdateCaseResult>(ops.UPDATE_CASE, {
        id,
        patch,
        idempotencyKey: crypto.randomUUID(),
      }),
    // Optimistic update with rollback (UI-FR-013, BR-6, AC-9).
    onMutate: async (patch) => {
      await client.cancelQueries({ queryKey: qk.case(id) });
      const prev = client.getQueryData<ops.CaseDetailResult>(qk.case(id));
      if (prev?.case) {
        client.setQueryData<ops.CaseDetailResult>(qk.case(id), {
          case: { ...prev.case, ...(patch.severity ? { severity: patch.severity } : {}) },
        });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) client.setQueryData(qk.case(id), ctx.prev);
    },
    onSuccess: (data) => {
      client.setQueryData<ops.CaseDetailResult>(qk.case(id), (old) =>
        old?.case ? { case: { ...old.case, ...data.updateCase } } : old,
      );
    },
  });
}

export function useBulkAssignCases() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { caseIds: string[]; assigneeId: string }) =>
      graphqlRequest<ops.BulkAssignCasesResult>(ops.BULK_ASSIGN_CASES, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cases"] }),
  });
}

/* ------- Tier 4b: case lifecycle, comments/timeline, export, catalog ------- */

/** Shared cache plumbing for the seven lifecycle transitions: patch the detail
 * cache from the returned caseView, then refresh the list AND the timeline
 * (every transition writes an activity row). */
function useCaseTransition<R>(id: string, extract: (r: R) => Case) {
  const client = useQueryClient();
  return {
    client,
    onSuccess: (data: R) => {
      client.setQueryData<ops.CaseDetailResult>(qk.case(id), (old) =>
        old?.case ? { case: { ...old.case, ...extract(data) } } : old,
      );
      void client.invalidateQueries({ queryKey: ["cases", "list"] });
      void client.invalidateQueries({ queryKey: qk.caseTimeline(id) });
    },
  };
}

export function useAssignCase(id: string) {
  const { onSuccess } = useCaseTransition<ops.AssignCaseResult>(id, (r) => r.assignCase);
  return useMutation({
    mutationFn: (assigneeId: string) =>
      graphqlRequest<ops.AssignCaseResult>(ops.ASSIGN_CASE, {
        id,
        assigneeId,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess,
  });
}

export function useUnassignCase(id: string) {
  const { onSuccess } = useCaseTransition<ops.UnassignCaseResult>(id, (r) => r.unassignCase);
  return useMutation({
    mutationFn: () => graphqlRequest<ops.UnassignCaseResult>(ops.UNASSIGN_CASE, { id }),
    onSuccess,
  });
}

export function useStartCase(id: string) {
  const { onSuccess } = useCaseTransition<ops.StartCaseResult>(id, (r) => r.startCase);
  return useMutation({
    mutationFn: () => graphqlRequest<ops.StartCaseResult>(ops.START_CASE, { id }),
    onSuccess,
  });
}

export function useResolveCase(id: string) {
  const { onSuccess } = useCaseTransition<ops.ResolveCaseResult>(id, (r) => r.resolveCase);
  return useMutation({
    mutationFn: (vars: { dispositionId: string; resolutionNote?: string }) =>
      graphqlRequest<ops.ResolveCaseResult>(ops.RESOLVE_CASE, {
        id,
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess,
  });
}

export function useReopenCase(id: string) {
  const { onSuccess } = useCaseTransition<ops.ReopenCaseResult>(id, (r) => r.reopenCase);
  return useMutation({
    mutationFn: () => graphqlRequest<ops.ReopenCaseResult>(ops.REOPEN_CASE, { id }),
    onSuccess,
  });
}

export function useCloseCase(id: string) {
  const { onSuccess } = useCaseTransition<ops.CloseCaseResult>(id, (r) => r.closeCase);
  return useMutation({
    mutationFn: () => graphqlRequest<ops.CloseCaseResult>(ops.CLOSE_CASE, { id }),
    onSuccess,
  });
}

export function useEscalateCase(id: string) {
  const { onSuccess } = useCaseTransition<ops.EscalateCaseResult>(id, (r) => r.escalateCase);
  return useMutation({
    mutationFn: (vars: { to?: string; reason?: string }) =>
      graphqlRequest<ops.EscalateCaseResult>(ops.ESCALATE_CASE, { id, ...vars }),
    onSuccess,
  });
}

/** A case's merged event+comment timeline, newest-first, cursor-paginated. */
export function useCaseTimeline(caseId: string) {
  return useInfiniteQuery({
    queryKey: qk.caseTimeline(caseId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.CaseTimelineResult>(ops.CASE_TIMELINE, {
        caseId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.caseTimeline),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!caseId,
  });
}

export function useAddCaseComment(caseId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: string) =>
      graphqlRequest<ops.AddCaseCommentResult>(ops.ADD_CASE_COMMENT, {
        caseId,
        body,
        idempotencyKey: crypto.randomUUID(),
      }),
    // The new comment surfaces as a comment.added timeline row — refetch it.
    onSuccess: () => client.invalidateQueries({ queryKey: qk.caseTimeline(caseId) }),
  });
}

export function useUpdateCaseComment(caseId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; body: string }) =>
      graphqlRequest<ops.UpdateCaseCommentResult>(ops.UPDATE_CASE_COMMENT, vars),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.caseTimeline(caseId) }),
  });
}

export function useDeleteCaseComment(caseId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteCaseCommentResult>(ops.DELETE_CASE_COMMENT, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.caseTimeline(caseId) }),
  });
}

/** Kick off an async CSV export. The result already carries the operation's
 * REAL polled status (the BFF re-reads it after the 202) — hand its id to
 * useCaseOperation to keep polling until succeeded|failed. */
export function useExportCases() {
  return useMutation({
    mutationFn: (vars: { filter?: JSONValue; format?: string }) =>
      graphqlRequest<ops.ExportCasesResult>(ops.EXPORT_CASES, {
        filter: vars.filter,
        format: vars.format ?? "csv",
      }).then((r) => r.exportCases),
  });
}

/** Poll an async case bulk/export operation until it settles. */
export function useCaseOperation(
  id: string | null,
  options: { refetchInterval?: number | false | ((query: { state: { data?: CaseOperation | null } }) => number | false) } = {},
) {
  return useQuery({
    queryKey: qk.caseOperation(id ?? ""),
    queryFn: () =>
      graphqlRequest<ops.CaseOperationResult>(ops.CASE_OPERATION, { id }).then((r) => r.caseOperation),
    enabled: !!id,
    refetchInterval: options.refetchInterval as never,
  });
}

export function useDispositions() {
  return useQuery({
    queryKey: qk.dispositions(),
    queryFn: () => graphqlRequest<ops.DispositionsResult>(ops.DISPOSITIONS).then((r) => r.dispositions),
  });
}

/** Correction->retrain loop stats (agent-runtime M1/M2) for the home widget. */
export function useLearningLoop(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.learningLoop(),
    queryFn: () => graphqlRequest<ops.LearningLoopResult>(ops.LEARNING_LOOP).then((r) => r.learningLoop),
    enabled: options.enabled ?? true,
  });
}

export function useCreateDisposition() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDispositionInput) =>
      graphqlRequest<ops.CreateDispositionResult>(ops.CREATE_DISPOSITION, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.dispositions() }),
  });
}

export function useUpdateDisposition() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; input: UpdateDispositionInput }) =>
      graphqlRequest<ops.UpdateDispositionResult>(ops.UPDATE_DISPOSITION, vars),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.dispositions() }),
  });
}

export function useCaseFields(queryUrn?: string) {
  return useQuery({
    queryKey: qk.caseFields(queryUrn),
    queryFn: () =>
      graphqlRequest<ops.CaseFieldsResult>(ops.CASE_FIELDS, { queryUrn }).then((r) => r.caseFields),
  });
}

export function useCreateCaseField() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateCaseFieldInput) =>
      graphqlRequest<ops.CreateCaseFieldResult>(ops.CREATE_CASE_FIELD, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cases", "caseFields"] }),
  });
}

export function useUpdateCaseField() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateCaseFieldInput) =>
      graphqlRequest<ops.UpdateCaseFieldResult>(ops.UPDATE_CASE_FIELD, { input }).then((r) => r.updateCaseField),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cases", "caseFields"] }),
  });
}

export function useDeleteCaseField() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; orphan?: boolean }) =>
      graphqlRequest<ops.DeleteCaseFieldResult>(ops.DELETE_CASE_FIELD, vars),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cases", "caseFields"] }),
  });
}

/** Replace the workspace SLA policy. Write-only downstream (no GET route) —
 * the returned echo is the only readback there is. */
export function usePutCaseSlaPolicy() {
  return useMutation({
    mutationFn: (input: CaseSlaPolicyInput) =>
      graphqlRequest<ops.PutCaseSlaPolicyResult>(ops.PUT_CASE_SLA_POLICY, { input }).then(
        (r) => r.putCaseSlaPolicy,
      ),
  });
}

/* ------- agentic ------- */
export function useProposalsInbox(
  vars: { status?: Proposal["status"]; agentKey?: string } = {},
  options: { enabled?: boolean } = {},
) {
  return useInfiniteQuery({
    queryKey: qk.proposals(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ProposalsInboxResult>(ops.PROPOSALS_INBOX, {
        first: PAGE,
        after: pageParam,
        status: vars.status ?? "PENDING",
        agentKey: vars.agentKey,
      }).then((r) => r.proposalsInbox),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: options.enabled ?? true,
  });
}

export function useDecideProposal() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; decision: DecisionInput }) =>
      graphqlRequest<ops.DecideProposalResult>(ops.DECIDE_PROPOSAL, {
        id: args.id,
        decision: args.decision,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: (data) => {
      // Drop decided proposal from the pending inbox (infinite cache shape) and
      // update its detail. Mirrors the SSE proposalPatcher (single source of truth).
      client.setQueriesData<{ pages: Connection<Proposal>[]; pageParams: unknown[] }>(
        { queryKey: ["agentic", "proposals"] },
        (old) =>
          old?.pages
            ? {
                ...old,
                pages: old.pages.map((pg) => ({
                  ...pg,
                  nodes: pg.nodes.filter((p) => p.id !== data.decideProposal.id),
                })),
              }
            : old,
      );
      client.setQueryData(qk.proposal(data.decideProposal.id), { proposal: data.decideProposal });
    },
  });
}

export function useAgentRun(id: string) {
  return useQuery({
    queryKey: qk.agentRun(id),
    queryFn: () => graphqlRequest<ops.AgentRunResult>(ops.AGENT_RUN, { id }),
    enabled: !!id,
    staleTime: 5 * 60_000,
  });
}

/* ------- ml ------- */
export function useExperiments() {
  return useInfiniteQuery({
    queryKey: qk.experiments({}),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ExperimentsResult>(ops.EXPERIMENTS, { first: PAGE, after: pageParam }).then(
        (r) => r.experiments,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useArchivedExperiments(vars: { workspaceId?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.archivedExperiments(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ArchivedExperimentsResult>(ops.ARCHIVED_EXPERIMENTS, {
        first: PAGE, after: pageParam, workspaceId: vars.workspaceId,
      }).then((r) => r.archivedExperiments),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useArchiveExperiment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.ArchiveExperimentResult>(ops.ARCHIVE_EXPERIMENT, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "experiments"] }),
  });
}

export function useRestoreExperiment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.RestoreExperimentResult>(ops.RESTORE_EXPERIMENT, { id }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["ml", "archivedExperiments"] });
      client.invalidateQueries({ queryKey: ["ml", "experiments"] });
    },
  });
}

export function useExperiment(id: string) {
  return useQuery({
    queryKey: qk.experiment(id),
    queryFn: () => graphqlRequest<ops.ExperimentResult>(ops.EXPERIMENT, { id }),
    enabled: !!id,
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: qk.run(id),
    queryFn: () => graphqlRequest<ops.RunResult>(ops.RUN, { id }),
    enabled: !!id,
  });
}

/* ------- ml: model registry ------- */
export function useModels(vars: { stage?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.models(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ModelsResult>(ops.MODELS, {
        first: PAGE,
        after: pageParam,
        stage: vars.stage,
      }).then((r) => r.models),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useModel(id: string) {
  return useQuery({
    queryKey: qk.model(id),
    queryFn: () => graphqlRequest<ops.ModelResult>(ops.MODEL, { id }),
    enabled: !!id,
  });
}

/** Request a model-version stage transition (opens a pending promotion; a SECOND
 * person must approve — four-eyes). Needs experiment.model.update downstream. */
export function usePromoteModelVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { modelId: string; version: number; targetStage: string; rationale?: string }) =>
      graphqlRequest<ops.PromoteModelVersionResult>(ops.PROMOTE_MODEL_VERSION, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.promoteModelVersion),
    onSuccess: (_data, vars) => {
      client.invalidateQueries({ queryKey: qk.model(vars.modelId) });
      client.invalidateQueries({ queryKey: qk.promotions(vars.modelId, vars.version) });
      client.invalidateQueries({ queryKey: ["ml", "models"] });
    },
  });
}

/** Approve/reject a pending promotion. The service forbids self-approval (four-eyes). */
export function useDecidePromotion(modelId?: string, version?: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { promotionId: string; decision: "approve" | "reject"; message?: string }) =>
      graphqlRequest<ops.DecidePromotionResult>(ops.DECIDE_PROMOTION, vars).then((r) => r.decidePromotion),
    onSuccess: () => {
      if (modelId) client.invalidateQueries({ queryKey: qk.model(modelId) });
      if (modelId && version !== undefined) client.invalidateQueries({ queryKey: qk.promotions(modelId, version) });
      client.invalidateQueries({ queryKey: ["ml", "models"] });
      client.invalidateQueries({ queryKey: ["ml", "promotions"] });
    },
  });
}

/** A model version's promotion history (the approval-queue source). Small,
 * bounded lists in practice — fetched as a plain page, not infinite-scrolled. */
export function usePromotions(modelId: string, version: number) {
  return useQuery({
    queryKey: qk.promotions(modelId, version),
    queryFn: () =>
      graphqlRequest<ops.PromotionsResult>(ops.PROMOTIONS, { modelId, version, first: 50 }).then(
        (r) => r.promotions,
      ),
    enabled: !!modelId && Number.isFinite(version),
  });
}

/* ------- ml: batch inference jobs ------- */
export function useInferenceJobs(vars: { status?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.inferenceJobs(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.InferenceJobsResult>(ops.INFERENCE_JOBS, {
        first: PAGE,
        after: pageParam,
        status: vars.status,
      }).then((r) => r.inferenceJobs),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useInferenceJob(id: string) {
  return useQuery({
    queryKey: qk.inferenceJob(id),
    queryFn: () => graphqlRequest<ops.InferenceJobResult>(ops.INFERENCE_JOB, { id }),
    enabled: !!id,
  });
}

export function useCreateInferenceJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateInferenceJobInput) =>
      graphqlRequest<ops.CreateInferenceJobResult>(ops.CREATE_INFERENCE_JOB, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createInferenceJob),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceJobs"] }),
  });
}

/* ------- Tier 4b: ml ops — run tooling (register/best/compare/notes/
 * artifacts/metric history) ------- */

/** Register a FINISHED run as a model version. RunNotFinished /
 * ModelTypeMismatch surface verbatim. Needs experiment.model.create downstream. */
export function useRegisterRunAsModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { experimentId: string; runId: string; input: RegisterRunInput }) =>
      graphqlRequest<ops.RegisterRunAsModelResult>(ops.REGISTER_RUN_AS_MODEL, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.registerRunAsModel),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: ["ml", "models"] });
      client.invalidateQueries({ queryKey: qk.run(vars.runId) });
    },
  });
}

/** Edit an experiment's name/description/note (PATCH — omitted fields unchanged). */
export function useUpdateExperiment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateExperimentInput }) =>
      graphqlRequest<ops.UpdateExperimentResult>(ops.UPDATE_EXPERIMENT, { id, input }).then(
        (r) => r.updateExperiment,
      ),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: qk.experiment(vars.id) });
      client.invalidateQueries({ queryKey: ["ml", "experiments"] });
    },
  });
}

/** The experiment's best run by one metric (fetched on demand — enabled only
 * once the user picked a metric). null when no run carries the metric. */
export function useBestRun(
  experimentId: string,
  vars: { metric: string; direction: "max" | "min" },
  enabled: boolean,
) {
  return useQuery({
    queryKey: qk.bestRun(experimentId, vars.metric, vars.direction),
    queryFn: () =>
      graphqlRequest<ops.BestRunResult>(ops.BEST_RUN, {
        experimentId,
        metric: vars.metric,
        direction: vars.direction,
      }).then((r) => r.bestRun),
    enabled: enabled && !!experimentId && !!vars.metric,
  });
}

/** Server-side run comparison (>= 2 runs; fetched on demand). */
export function useCompareRuns(runIds: string[], enabled: boolean) {
  return useQuery({
    queryKey: qk.compareRuns(runIds),
    queryFn: () =>
      graphqlRequest<ops.CompareRunsResult>(ops.COMPARE_RUNS, { runIds, includeAll: true }).then(
        (r) => r.compareRuns,
      ),
    enabled: enabled && runIds.length >= 2,
  });
}

/** The run's note (null when the run has none). */
export function useRunNote(runId: string) {
  return useQuery({
    queryKey: qk.runNote(runId),
    queryFn: () => graphqlRequest<ops.RunNoteResult>(ops.RUN_NOTE, { runId }).then((r) => r.runNote),
    enabled: !!runId,
  });
}

export function useUpsertRunNote() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { runId: string; description: string }) =>
      graphqlRequest<ops.UpsertRunNoteResult>(ops.UPSERT_RUN_NOTE, vars).then((r) => r.upsertRunNote),
    onSuccess: (_d, vars) => client.invalidateQueries({ queryKey: qk.runNote(vars.runId) }),
  });
}

export function useDeleteRunNote() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      graphqlRequest<ops.DeleteRunNoteResult>(ops.DELETE_RUN_NOTE, { runId }).then((r) => r.deleteRunNote),
    onSuccess: (_d, runId) => client.invalidateQueries({ queryKey: qk.runNote(runId) }),
  });
}

/** The run's artifact index. */
export function useRunArtifacts(runId: string) {
  return useQuery({
    queryKey: qk.runArtifacts(runId),
    queryFn: () =>
      graphqlRequest<ops.RunArtifactsResult>(ops.RUN_ARTIFACTS, { runId }).then((r) => r.runArtifacts),
    enabled: !!runId,
  });
}

/** Fetch a REAL short-lived signed url for one artifact, per click —
 * mutation-shaped on purpose so nothing caches an expiring link. */
export function useRunArtifactUrl() {
  return useMutation({
    mutationFn: (vars: { runId: string; path: string }) =>
      graphqlRequest<ops.RunArtifactUrlResult>(ops.RUN_ARTIFACT_URL, vars).then((r) => r.runArtifactUrl),
  });
}

/** Raw logged metric points for a run (verbatim rows). */
export function useRunMetricHistory(runId: string, enabled = true) {
  return useQuery({
    queryKey: qk.runMetricHistory(runId),
    queryFn: () =>
      graphqlRequest<ops.RunMetricHistoryResult>(ops.RUN_METRIC_HISTORY, { runId }).then(
        (r) => r.runMetricHistory,
      ),
    enabled: enabled && !!runId,
  });
}

/* ------- Tier 4b: ml ops — model cards ------- */

/** The MERGED model card for one version (JSON; null when none exists). */
export function useModelCard(modelId: string, version: number | null) {
  return useQuery({
    queryKey: qk.modelCard(modelId, version ?? -1),
    queryFn: () =>
      graphqlRequest<ops.ModelCardResult>(ops.MODEL_CARD, { modelId, version }).then((r) => r.modelCard),
    enabled: !!modelId && version != null,
  });
}

/** Update the human overlay; answers the full merged card. Needs
 * experiment.model_card.update downstream. */
export function useUpdateModelCard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { modelId: string; version: number; input: ModelCardOverlayInput }) =>
      graphqlRequest<ops.UpdateModelCardResult>(ops.UPDATE_MODEL_CARD, vars).then((r) => r.updateModelCard),
    onSuccess: (data, vars) =>
      // Re-render straight from the mutation's returned merged card.
      client.setQueryData(qk.modelCard(vars.modelId, vars.version), data),
  });
}

/* ------- Tier 4b: ml ops — inference job lifecycle + validate ------- */

export function useCancelInferenceJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.CancelInferenceJobResult>(ops.CANCEL_INFERENCE_JOB, { id }).then(
        (r) => r.cancelInferenceJob,
      ),
    onSuccess: (_d, id) => {
      client.invalidateQueries({ queryKey: qk.inferenceJob(id) });
      client.invalidateQueries({ queryKey: ["ml", "inferenceJobs"] });
    },
  });
}

/** Retry a terminal-failure job — the result is the NEW job (navigate to it). */
export function useRetryInferenceJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RetryInferenceJobResult>(ops.RETRY_INFERENCE_JOB, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.retryInferenceJob),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceJobs"] }),
  });
}

export function useDeleteInferenceJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteInferenceJobResult>(ops.DELETE_INFERENCE_JOB, { id }).then(
        (r) => r.deleteInferenceJob,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceJobs"] }),
  });
}

/** Standalone model×dataset compatibility preflight (read-only; the submit
 * re-validates server-side regardless). */
export function useValidateInference() {
  return useMutation({
    mutationFn: (input: ValidateInferenceInput) =>
      graphqlRequest<ops.ValidateInferenceResult>(ops.VALIDATE_INFERENCE, { input }).then(
        (r) => r.validateInference,
      ),
  });
}

/* ------- Tier 4b: ml ops — scoring schedules ------- */

export function useInferenceSchedules() {
  return useInfiniteQuery({
    queryKey: qk.inferenceSchedules(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.InferenceSchedulesResult>(ops.INFERENCE_SCHEDULES, {
        first: PAGE,
        after: pageParam,
      }).then((r) => r.inferenceSchedules),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

/** A schedule's fire history (the real jobs it submitted), fetched on demand. */
export function useInferenceScheduleFires(scheduleId: string | null) {
  return useInfiniteQuery({
    queryKey: qk.inferenceScheduleFires(scheduleId ?? ""),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.InferenceScheduleFiresResult>(ops.INFERENCE_SCHEDULE_FIRES, {
        scheduleId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.inferenceScheduleFires),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!scheduleId,
  });
}

export function useCreateInferenceSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateInferenceScheduleInput) =>
      graphqlRequest<ops.CreateInferenceScheduleResult>(ops.CREATE_INFERENCE_SCHEDULE, { input }).then(
        (r) => r.createInferenceSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceSchedules"] }),
  });
}

export function useUpdateInferenceSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateInferenceScheduleInput }) =>
      graphqlRequest<ops.UpdateInferenceScheduleResult>(ops.UPDATE_INFERENCE_SCHEDULE, { id, input }).then(
        (r) => r.updateInferenceSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceSchedules"] }),
  });
}

export function useDeleteInferenceSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteInferenceScheduleResult>(ops.DELETE_INFERENCE_SCHEDULE, { id }).then(
        (r) => r.deleteInferenceSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceSchedules"] }),
  });
}

export function usePauseInferenceSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.PauseInferenceScheduleResult>(ops.PAUSE_INFERENCE_SCHEDULE, { id }).then(
        (r) => r.pauseInferenceSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceSchedules"] }),
  });
}

export function useResumeInferenceSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ResumeInferenceScheduleResult>(ops.RESUME_INFERENCE_SCHEDULE, { id }).then(
        (r) => r.resumeInferenceSchedule,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "inferenceSchedules"] }),
  });
}

/** One forced fire — answers the real fire result verbatim
 * ({fired, job_id, status} | {fired: false, reason}). */
export function useTriggerInferenceSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.TriggerInferenceScheduleResult>(ops.TRIGGER_INFERENCE_SCHEDULE, { id }).then(
        (r) => r.triggerInferenceSchedule,
      ),
    onSuccess: (_d, id) => {
      // A fire submits a real job and bumps the schedule's next-fire preview.
      client.invalidateQueries({ queryKey: ["ml", "inferenceSchedules"] });
      client.invalidateQueries({ queryKey: ["ml", "inferenceJobs"] });
      client.invalidateQueries({ queryKey: qk.inferenceScheduleFires(id) });
    },
  });
}

/* ------- ml: experiment create ------- */
export function useCreateExperiment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateExperimentInput) =>
      graphqlRequest<ops.CreateExperimentResult>(ops.CREATE_EXPERIMENT, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createExperiment),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ml", "experiments"] }),
  });
}

/* ------- dashboards ------- */
export function useDashboards(workspaceId: string) {
  return useInfiniteQuery({
    queryKey: qk.dashboards(workspaceId, {}),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.DashboardsResult>(ops.DASHBOARDS, {
        workspaceId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.dashboards),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!workspaceId,
  });
}

export function useArchivedDashboards(workspaceId: string) {
  return useInfiniteQuery({
    queryKey: qk.archivedDashboards(workspaceId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ArchivedDashboardsResult>(ops.ARCHIVED_DASHBOARDS, {
        workspaceId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.archivedDashboards),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!workspaceId,
  });
}

export function useArchiveDashboard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.ArchiveDashboardResult>(ops.ARCHIVE_DASHBOARD, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dashboards"] }),
  });
}

export function useRestoreDashboard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.RestoreDashboardResult>(ops.RESTORE_DASHBOARD, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dashboards"] }),
  });
}

export function useDashboard(id: string, filters?: CrossFilterVar[]) {
  return useQuery({
    // Cross-filter selections are part of the key so a selection change refetches
    // the batch with the new predicates (CHART-FR-041).
    queryKey: [...qk.dashboard(id), filters ?? []],
    queryFn: () => graphqlRequest<ops.DashboardResult>(ops.DASHBOARD, { id, filters }),
    enabled: !!id,
    // Keep the board rendered while a cross-filter selection refetches, so the
    // charts update in place instead of flashing the empty/loading state.
    placeholderData: (prev) => prev,
  });
}

/* ------- chart authoring (no-code editor) ------- */
/** The chart-type catalog — effectively static per deploy. */
export function useChartTypes() {
  return useQuery({
    queryKey: qk.chartTypes(),
    queryFn: () => graphqlRequest<ops.ChartTypesResult>(ops.CHART_TYPES).then((r) => r.chartTypes),
    staleTime: 30 * 60_000,
  });
}

/** Semantic-model headers (id/name only) for the model picker. */
export function useSemanticModels(workspaceId: string) {
  return useQuery({
    queryKey: qk.semanticModels(workspaceId),
    queryFn: () =>
      graphqlRequest<ops.SemanticModelsResult>(ops.SEMANTIC_MODELS, { workspaceId }).then(
        (r) => r.semanticModels,
      ),
    enabled: !!workspaceId,
    staleTime: 5 * 60_000,
  });
}

/** A single semantic model WITH dimensions + measures (hydrated once picked). */
export function useSemanticModel(name: string) {
  return useQuery({
    queryKey: qk.semanticModel(name),
    queryFn: () =>
      graphqlRequest<ops.SemanticModelResult>(ops.SEMANTIC_MODEL, { name }).then((r) => r.semanticModel),
    enabled: !!name,
    staleTime: 5 * 60_000,
  });
}

/** Resolve an UNSAVED chart spec for the live editor preview (no persistence). */
export function useChartPreview() {
  return useMutation({
    mutationFn: (input: CreateChartInput) =>
      graphqlRequest<ops.ChartPreviewResult>(ops.CHART_PREVIEW, { input }).then((r) => r.chartPreview),
  });
}

export function useCreateDashboard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDashboardInput) =>
      graphqlRequest<ops.CreateDashboardResult>(ops.CREATE_DASHBOARD, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dashboards", "list"] }),
  });
}

export function useUpdateDashboard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; input: UpdateDashboardInput }) =>
      graphqlRequest<ops.UpdateDashboardResult>(ops.UPDATE_DASHBOARD, {
        id: args.id,
        input: args.input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ["dashboards", "list"] });
      client.invalidateQueries({ queryKey: qk.dashboard(data.updateDashboard.id) });
    },
  });
}

export function useDeleteDashboard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteDashboardResult>(ops.DELETE_DASHBOARD, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dashboards", "list"] }),
  });
}

/* ------- scheduled dashboard report subscriptions (notification-service) ------- */
export function useReportSubscriptions(dashboardId?: string) {
  return useInfiniteQuery({
    queryKey: qk.reportSubscriptions(dashboardId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ReportSubscriptionsResult>(ops.REPORT_SUBSCRIPTIONS, {
        dashboardId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.reportSubscriptions),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

function invalidateReportSubscriptions(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: ["dashboards", "reportSubscriptions"] });
}

export function useCreateReportSubscription() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateReportSubscriptionInput) =>
      graphqlRequest<ops.CreateReportSubscriptionResult>(ops.CREATE_REPORT_SUBSCRIPTION, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => invalidateReportSubscriptions(client),
  });
}

export function useUpdateReportSubscription() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; input: UpdateReportSubscriptionInput }) =>
      graphqlRequest<ops.UpdateReportSubscriptionResult>(ops.UPDATE_REPORT_SUBSCRIPTION, {
        id: args.id,
        input: args.input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => invalidateReportSubscriptions(client),
  });
}

export function useDeleteReportSubscription() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteReportSubscriptionResult>(ops.DELETE_REPORT_SUBSCRIPTION, { id }),
    onSuccess: () => invalidateReportSubscriptions(client),
  });
}

export function usePauseReportSubscription() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; paused: boolean }) =>
      graphqlRequest<ops.PauseReportSubscriptionResult>(ops.PAUSE_REPORT_SUBSCRIPTION, args),
    onSuccess: () => invalidateReportSubscriptions(client),
  });
}

export function useTriggerReportSubscription() {
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.TriggerReportSubscriptionResult>(ops.TRIGGER_REPORT_SUBSCRIPTION, { id }),
  });
}

export function useCreateChart(dashboardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateChartInput) =>
      graphqlRequest<ops.CreateChartResult>(ops.CREATE_CHART, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.dashboard(dashboardId) }),
  });
}

export function useUpdateChart(dashboardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; input: UpdateChartInput }) =>
      graphqlRequest<ops.UpdateChartResult>(ops.UPDATE_CHART, {
        id: args.id,
        input: args.input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.dashboard(dashboardId) }),
  });
}

export function useDeleteChart(dashboardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteChartResult>(ops.DELETE_CHART, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.dashboard(dashboardId) }),
  });
}

/* ------- semantic model authoring ------- */
/** Real columns of a dataset version — the entity/dimension/measure editor's
 * column picker binds to these instead of free-typed strings. */
export function useDatasetSchema(
  datasetId: string,
  version?: number,
  opts: { enabled?: boolean; poll?: boolean } = {},
) {
  return useQuery({
    queryKey: qk.datasetSchema(datasetId, version),
    queryFn: () =>
      graphqlRequest<ops.DatasetSchemaResult>(ops.DATASET_SCHEMA, { datasetId, version })
        .then((r) => r.datasetSchema)
        // Upload review step: the dataset row/schema lands a moment after the
        // ingestion finishes (dataset-service consumes the event). Until then
        // the query resolves "not found" — treat that as "not ready yet" (empty)
        // so the poll keeps ticking cleanly instead of wedging on a hard error.
        .catch((e) => {
          if (opts.poll) return [] as ops.DatasetSchemaResult["datasetSchema"];
          throw e;
        }),
    enabled: (opts.enabled ?? true) && !!datasetId,
    staleTime: opts.poll ? 0 : 60_000,
    retry: opts.poll ? false : 3,
    refetchInterval: opts.poll
      ? (query) => ((query.state.data?.length ?? 0) > 0 ? false : 2_000)
      : undefined,
    // Keep polling even when the tab isn't focused — the review step's schema
    // lands seconds after the upload, and react-query pauses interval refetches
    // in the background by default (so it would wedge on the first empty result).
    refetchIntervalInBackground: opts.poll || undefined,
  });
}

export function useSemanticModelList(workspaceId: string) {
  return useInfiniteQuery({
    queryKey: qk.semanticModelList({ workspaceId }),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.SemanticModelListResult>(ops.SEMANTIC_MODEL_LIST, {
        workspaceId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.semanticModelList),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!workspaceId,
  });
}

export function useSemanticModelDetail(id: string) {
  return useQuery({
    queryKey: qk.semanticModelDetail(id),
    queryFn: () =>
      graphqlRequest<ops.SemanticModelDetailResult>(ops.SEMANTIC_MODEL_DETAIL, { id }).then(
        (r) => r.semanticModelDetail,
      ),
    enabled: !!id,
  });
}

export function useSemanticModelVersions(modelId: string) {
  return useInfiniteQuery({
    queryKey: qk.semanticModelVersions(modelId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.SemanticModelVersionsResult>(ops.SEMANTIC_MODEL_VERSIONS, {
        modelId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.semanticModelVersions),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!modelId,
  });
}

export function useSemanticModelVersion(modelId: string, versionNo: number | null) {
  return useQuery({
    queryKey: qk.semanticModelVersion(modelId, versionNo ?? 0),
    queryFn: () =>
      graphqlRequest<ops.SemanticModelVersionResult>(ops.SEMANTIC_MODEL_VERSION, {
        modelId,
        versionNo,
      }).then((r) => r.semanticModelVersion),
    enabled: !!modelId && versionNo != null,
  });
}

export function useCreateSemanticModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSemanticModelInput) =>
      graphqlRequest<ops.CreateSemanticModelResult>(ops.CREATE_SEMANTIC_MODEL, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createSemanticModel),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "models"] }),
  });
}

export function useUpdateSemanticModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: ops.UpdateSemanticModelInput }) =>
      graphqlRequest<ops.UpdateSemanticModelResult>(ops.UPDATE_SEMANTIC_MODEL, { id, input }).then(
        (r) => r.updateSemanticModel,
      ),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: qk.semanticModelDetail(vars.id) });
      client.invalidateQueries({ queryKey: ["semantic", "models"] });
    },
  });
}

export function useDeleteSemanticModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteSemanticModelResult>(ops.DELETE_SEMANTIC_MODEL, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "models"] }),
  });
}

export function useCreateSemanticModelVersion(modelId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      graphqlRequest<ops.CreateSemanticModelVersionResult>(ops.CREATE_SEMANTIC_MODEL_VERSION, {
        modelId,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createSemanticModelVersion),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.semanticModelVersions(modelId) }),
  });
}

/** Save the draft definition — debounced by the caller as the author edits
 * (the live validate-as-you-type hook: a bad expr/name 422s immediately). */
export function useUpdateSemanticModelDraft(modelId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ versionNo, definition }: { versionNo: number; definition: JSONValue }) =>
      graphqlRequest<ops.UpdateSemanticModelDraftResult>(ops.UPDATE_SEMANTIC_MODEL_DRAFT, {
        modelId,
        versionNo,
        definition,
      }).then((r) => r.updateSemanticModelDraft),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: qk.semanticModelVersion(modelId, vars.versionNo) });
    },
  });
}

export function useSubmitSemanticModelVersion(modelId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (versionNo: number) =>
      graphqlRequest<ops.SubmitSemanticModelVersionResult>(ops.SUBMIT_SEMANTIC_MODEL_VERSION, {
        modelId,
        versionNo,
      }).then((r) => r.submitSemanticModelVersion),
    onSuccess: (_d, versionNo) => {
      client.invalidateQueries({ queryKey: qk.semanticModelVersion(modelId, versionNo) });
      client.invalidateQueries({ queryKey: qk.semanticModelVersions(modelId) });
      client.invalidateQueries({ queryKey: qk.semanticModelDetail(modelId) });
    },
  });
}

export function useApproveSemanticModelVersion(modelId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ versionNo, note }: { versionNo: number; note?: string }) =>
      graphqlRequest<ops.ApproveSemanticModelVersionResult>(ops.APPROVE_SEMANTIC_MODEL_VERSION, {
        modelId,
        versionNo,
        note,
      }).then((r) => r.approveSemanticModelVersion),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: qk.semanticModelVersion(modelId, vars.versionNo) });
      client.invalidateQueries({ queryKey: qk.semanticModelVersions(modelId) });
      client.invalidateQueries({ queryKey: qk.semanticModelDetail(modelId) });
    },
  });
}

export function useRejectSemanticModelVersion(modelId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ versionNo, note }: { versionNo: number; note: string }) =>
      graphqlRequest<ops.RejectSemanticModelVersionResult>(ops.REJECT_SEMANTIC_MODEL_VERSION, {
        modelId,
        versionNo,
        note,
      }).then((r) => r.rejectSemanticModelVersion),
    onSuccess: (_d, vars) => {
      client.invalidateQueries({ queryKey: qk.semanticModelVersion(modelId, vars.versionNo) });
      client.invalidateQueries({ queryKey: qk.semanticModelVersions(modelId) });
    },
  });
}

/** Compile + optionally dry-run the model (draft or published) — the editor's
 * real preview action. Not cached: every click should hit the live compiler. */
export function useCompileSemanticModel() {
  return useMutation({
    mutationFn: (input: CompileSemanticModelInput) =>
      graphqlRequest<ops.CompileSemanticModelResult>(ops.COMPILE_SEMANTIC_MODEL, { input }).then(
        (r) => r.compileSemanticModel,
      ),
  });
}

/* ------- verified NL↔SQL pairs (semantic-service, four-eyes) ------- */
export function useVerifiedQueries(vars: { workspaceId?: string; status?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.verifiedQueries(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.VerifiedQueriesResult>(ops.VERIFIED_QUERIES, {
        first: PAGE,
        after: pageParam,
        workspaceId: vars.workspaceId,
        status: vars.status,
      }).then((r) => r.verifiedQueries),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

/** Semantic search over APPROVED verified NL↔SQL pairs (SEM-FR-041). Runs only
 * when a workspace + a non-empty query are present (the panel debounces the
 * query text); `enabled` short-circuits the empty-search round-trip. */
export function useVerifiedQuerySearch(vars: {
  query: string;
  workspaceId?: string;
  topK?: number;
}) {
  const enabled = !!vars.workspaceId && vars.query.trim().length > 0;
  return useQuery({
    queryKey: qk.verifiedQuerySearch(vars),
    enabled,
    queryFn: () =>
      graphqlRequest<ops.VerifiedQuerySearchResult>(ops.VERIFIED_QUERY_SEARCH, {
        query: vars.query.trim(),
        workspaceId: vars.workspaceId,
        topK: vars.topK,
      }).then((r) => r.verifiedQuerySearch),
  });
}

export function useCreateVerifiedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateVerifiedQueryInput) =>
      graphqlRequest<ops.CreateVerifiedQueryResult>(ops.CREATE_VERIFIED_QUERY, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createVerifiedQuery),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "verifiedQueries"] }),
  });
}

export function useUpdateVerifiedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateVerifiedQueryInput }) =>
      graphqlRequest<ops.UpdateVerifiedQueryResult>(ops.UPDATE_VERIFIED_QUERY, { id, input }).then(
        (r) => r.updateVerifiedQuery,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "verifiedQueries"] }),
  });
}

export function useSubmitVerifiedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.SubmitVerifiedQueryResult>(ops.SUBMIT_VERIFIED_QUERY, { id }).then(
        (r) => r.submitVerifiedQuery,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "verifiedQueries"] }),
  });
}

export function useApproveVerifiedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ApproveVerifiedQueryResult>(ops.APPROVE_VERIFIED_QUERY, { id }).then(
        (r) => r.approveVerifiedQuery,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "verifiedQueries"] }),
  });
}

export function useRejectVerifiedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) =>
      graphqlRequest<ops.RejectVerifiedQueryResult>(ops.REJECT_VERIFIED_QUERY, { id, note }).then(
        (r) => r.rejectVerifiedQuery,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "verifiedQueries"] }),
  });
}

export function useArchiveVerifiedQuery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ArchiveVerifiedQueryResult>(ops.ARCHIVE_VERIFIED_QUERY, { id }).then(
        (r) => r.archiveVerifiedQuery,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["semantic", "verifiedQueries"] }),
  });
}

/* ------- semantic bootstrap-from-dataset (202 async + polling) ------- */
export function useBootstrapSemanticModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { modelId: string; sources?: JSONValue }) =>
      graphqlRequest<ops.BootstrapSemanticModelResult>(ops.BOOTSTRAP_SEMANTIC_MODEL, {
        modelId: vars.modelId,
        sources: vars.sources,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.bootstrapSemanticModel),
    onSuccess: (_d, vars) => {
      // The bootstrap rewrites the open draft's definition.
      client.invalidateQueries({ queryKey: qk.semanticModelVersions(vars.modelId) });
      client.invalidateQueries({ queryKey: ["semantic", "version", vars.modelId] });
    },
  });
}

/** Poll a semantic operation until terminal (completed/failed). */
export function useSemanticOperation(id: string | null) {
  return useQuery({
    queryKey: qk.semanticOperation(id ?? ""),
    queryFn: () =>
      graphqlRequest<ops.SemanticOperationResult>(ops.SEMANTIC_OPERATION, { id }).then(
        (r) => r.semanticOperation,
      ),
    enabled: !!id,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "completed" || s === "failed" ? false : 2_000;
    },
  });
}

/* ------- usage ------- */
export function useCostPanel(
  workspaceId: string,
  from: string,
  to: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: qk.costPanel(workspaceId, from, to),
    queryFn: () =>
      graphqlRequest<ops.WorkspaceCostPanelResult>(ops.WORKSPACE_COST_PANEL, { workspaceId, from, to }),
    enabled: !!workspaceId && (options.enabled ?? true),
  });
}

export function useBudgets() {
  return useInfiniteQuery({
    queryKey: qk.budgets(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.BudgetsResult>(ops.BUDGETS, { first: PAGE, after: pageParam }).then((r) => r.budgets),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateBudget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateBudgetInput) =>
      graphqlRequest<ops.CreateBudgetResult>(ops.CREATE_BUDGET, { input, idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.budgets() }),
  });
}

export function useUpdateBudget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateBudgetInput }) =>
      graphqlRequest<ops.UpdateBudgetResult>(ops.UPDATE_BUDGET, {
        id,
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.budgets() }),
  });
}

export function useDeleteBudget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteBudgetResult>(ops.DELETE_BUDGET, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.budgets() }),
  });
}

export function useRateCards() {
  return useInfiniteQuery({
    queryKey: qk.rateCards(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.RateCardsResult>(ops.RATE_CARDS, { first: PAGE, after: pageParam }).then((r) => r.rateCards),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateRateCard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateRateCardInput) =>
      graphqlRequest<ops.CreateRateCardResult>(ops.CREATE_RATE_CARD, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.rateCards() }),
  });
}

export function useActivateRateCard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.ActivateRateCardResult>(ops.ACTIVATE_RATE_CARD, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.rateCards() }),
  });
}

/** Detected spend anomalies (usage-service GET /anomalies). No real server-
 * side pagination — a plain list, not infinite-scrolled. */
export function useAnomalies(status?: string) {
  return useQuery({
    queryKey: qk.anomalies(status),
    queryFn: () => graphqlRequest<ops.AnomaliesResult>(ops.ANOMALIES, { status }).then((r) => r.anomalies),
  });
}

export function useDismissAnomaly() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DismissAnomalyResult>(ops.DISMISS_ANOMALY, { id }).then((r) => r.dismissAnomaly),
    onSuccess: () => client.invalidateQueries({ queryKey: ["usage", "anomalies"] }),
  });
}

/* ------- admin: users, workspaces, groups, service accounts, tenant, audit ------- */
export function useUsers() {
  return useInfiniteQuery({
    queryKey: qk.users({}),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.UsersResult>(ops.USERS, { first: PAGE, after: pageParam }).then((r) => r.users),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

/**
 * Member-safe active-user directory for assignee pickers (case assign/reassign,
 * bulk assign). Unlike useUsers() this hits the member-safe assignableUsers
 * query, so it works for any role holding case.case.assign — no
 * identity.user.admin scope. Only id/email/fullName are populated.
 */
export function useAssignableUsers() {
  return useInfiniteQuery({
    queryKey: qk.assignableUsers(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.AssignableUsersResult>(ops.ASSIGNABLE_USERS, { first: PAGE, after: pageParam }).then(
        (r) => r.assignableUsers,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useInviteUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: InviteUserInput) =>
      graphqlRequest<ops.InviteUserResult>(ops.INVITE_USER, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useWorkspaces(vars: { archived?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.workspaces(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.WorkspacesResult>(ops.WORKSPACES, {
        first: PAGE,
        after: pageParam,
        archived: vars.archived,
      }).then((r) => r.workspaces),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateWorkspace() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateWorkspaceInput) =>
      graphqlRequest<ops.CreateWorkspaceResult>(ops.CREATE_WORKSPACE, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "workspaces"] }),
  });
}

export function useGroups(vars: { type?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.groups(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.GroupsResult>(ops.GROUPS, {
        first: PAGE,
        after: pageParam,
        type: vars.type,
      }).then((r) => r.groups),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useGroupMembers(groupId: string | null) {
  return useQuery({
    queryKey: qk.groupMembers(groupId ?? ""),
    queryFn: () =>
      graphqlRequest<ops.GroupMembersResult>(ops.GROUP_MEMBERS, { groupId }).then((r) => r.groupMembers),
    enabled: !!groupId,
  });
}

export function useGroupRoles(groupId: string | null) {
  return useQuery({
    queryKey: qk.groupRoles(groupId ?? ""),
    queryFn: () =>
      graphqlRequest<ops.GroupRolesResult>(ops.GROUP_ROLES, { groupId }).then((r) => r.groupRoles),
    enabled: !!groupId,
  });
}

export function useUserGroups(userId: string | null) {
  return useQuery({
    queryKey: qk.userGroups(userId ?? ""),
    queryFn: () =>
      graphqlRequest<ops.UserGroupsResult>(ops.USER_GROUPS, { userId }).then((r) => r.userGroups),
    enabled: !!userId,
  });
}

export function useAddGroupMember(groupId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      graphqlRequest<ops.AddGroupMemberResult>(ops.ADD_GROUP_MEMBER, {
        groupId,
        userId,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.groupMembers(groupId) }),
  });
}

export function useRemoveGroupMember(groupId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      graphqlRequest<ops.RemoveGroupMemberResult>(ops.REMOVE_GROUP_MEMBER, { groupId, userId }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.groupMembers(groupId) }),
  });
}

/* ------- admin: teams (permission-type rbac groups) ------- */
export function useCreateTeam() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateTeamInput) =>
      graphqlRequest<ops.CreateTeamResult>(ops.CREATE_TEAM, { input, idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "groups"] }),
  });
}

export function useUpdateTeam() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateTeamInput }) =>
      graphqlRequest<ops.UpdateTeamResult>(ops.UPDATE_TEAM, {
        id,
        input,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "groups"] }),
  });
}

export function useDeleteTeam() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteTeamResult>(ops.DELETE_TEAM, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "groups"] }),
  });
}

export function useAssignTeamRole(groupId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (roleId: string) =>
      graphqlRequest<ops.AssignTeamRoleResult>(ops.ASSIGN_TEAM_ROLE, { groupId, roleId }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.groupRoles(groupId) }),
  });
}

export function useUnassignTeamRole(groupId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (roleId: string) =>
      graphqlRequest<ops.UnassignTeamRoleResult>(ops.UNASSIGN_TEAM_ROLE, { groupId, roleId }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.groupRoles(groupId) }),
  });
}

export function useRoles() {
  return useInfiniteQuery({
    queryKey: qk.roles(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.RolesResult>(ops.ROLES, { first: PAGE, after: pageParam }).then((r) => r.roles),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useServiceAccounts() {
  return useInfiniteQuery({
    queryKey: qk.serviceAccounts(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ServiceAccountsResult>(ops.SERVICE_ACCOUNTS, {
        first: PAGE,
        after: pageParam,
      }).then((r) => r.serviceAccounts),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

/* ------- Tier 4b: identity/rbac admin (user + SA lifecycle, workspace
 * lifecycle, content groups, custom roles, content grants, bulk ops) ------- */
export function useUpdateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, fullName }: { id: string; fullName: string }) =>
      graphqlRequest<ops.UpdateUserResult>(ops.UPDATE_USER, {
        id,
        fullName,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.updateUser),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useDeactivateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, overrideLastAdmin }: { id: string; overrideLastAdmin?: boolean }) =>
      graphqlRequest<ops.DeactivateUserResult>(ops.DEACTIVATE_USER, {
        id,
        overrideLastAdmin,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.deactivateUser),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useResendUserInvite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ResendUserInviteResult>(ops.RESEND_USER_INVITE, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.resendUserInvite),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useDeleteUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteUserResult>(ops.DELETE_USER, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

/** Create a service account. The response's apiKey is the ONLY time the secret
 * exists client-side — surfaced once via SecretBanner, never cached/persisted. */
export function useCreateServiceAccount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateServiceAccountInput) =>
      graphqlRequest<ops.CreateServiceAccountResult>(ops.CREATE_SERVICE_ACCOUNT, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createServiceAccount),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.serviceAccounts() }),
  });
}

export function useRotateServiceAccount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RotateServiceAccountResult>(ops.ROTATE_SERVICE_ACCOUNT, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.rotateServiceAccount),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.serviceAccounts() }),
  });
}

export function useRevokeServiceAccount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RevokeServiceAccountResult>(ops.REVOKE_SERVICE_ACCOUNT, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.serviceAccounts() }),
  });
}

export function useUpdateWorkspace() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateWorkspaceInput }) =>
      graphqlRequest<ops.UpdateWorkspaceResult>(ops.UPDATE_WORKSPACE, {
        id,
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.updateWorkspace),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "workspaces"] }),
  });
}

export function useArchiveWorkspace() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.ArchiveWorkspaceResult>(ops.ARCHIVE_WORKSPACE, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.archiveWorkspace),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "workspaces"] }),
  });
}

export function useRestoreWorkspace() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RestoreWorkspaceResult>(ops.RESTORE_WORKSPACE, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.restoreWorkspace),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "workspaces"] }),
  });
}

export function useLinkWorkspaceContentGroup() {
  return useMutation({
    mutationFn: ({ workspaceId, groupId }: { workspaceId: string; groupId: string }) =>
      graphqlRequest<ops.LinkWorkspaceContentGroupResult>(ops.LINK_WORKSPACE_CONTENT_GROUP, {
        workspaceId,
        groupId,
      }),
  });
}

export function useUnlinkWorkspaceContentGroup() {
  return useMutation({
    mutationFn: ({ workspaceId, groupId }: { workspaceId: string; groupId: string }) =>
      graphqlRequest<ops.UnlinkWorkspaceContentGroupResult>(ops.UNLINK_WORKSPACE_CONTENT_GROUP, {
        workspaceId,
        groupId,
      }),
  });
}

/** The general group-create path (content groups in particular); Teams keep
 * their dedicated useCreateTeam (permission-type). */
export function useCreateGroup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateGroupInput) =>
      graphqlRequest<ops.CreateGroupResult>(ops.CREATE_GROUP, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createGroup),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "groups"] }),
  });
}

export function useUpdateGroup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateGroupInput) =>
      graphqlRequest<ops.UpdateGroupResult>(ops.UPDATE_GROUP, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.updateGroup),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "groups"] }),
  });
}

export function useBulkGroupMembership(groupId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (operations: GroupMemberOpInput[]) =>
      graphqlRequest<ops.BulkGroupMembershipResultData>(ops.BULK_GROUP_MEMBERSHIP, {
        groupId,
        operations,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.bulkGroupMembership),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.groupMembers(groupId) }),
  });
}

export function useCreateRole() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateRoleInput) =>
      graphqlRequest<ops.CreateRoleResult>(ops.CREATE_ROLE, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createRole),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.roles() }),
  });
}

export function useRenameRole() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      graphqlRequest<ops.RenameRoleResult>(ops.RENAME_ROLE, {
        id,
        name,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.renameRole),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.roles() }),
  });
}

export function useSetRoleActions() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, actions }: { id: string; actions: string[] }) =>
      graphqlRequest<ops.SetRoleActionsResult>(ops.SET_ROLE_ACTIONS, {
        id,
        actions,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.setRoleActions),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.roles() }),
  });
}

/** Edit a custom role's name and/or action set in one atomic PATCH. System
 * roles reject with 409 SYSTEM_IMMUTABLE (the UI hides the control for them). */
export function useUpdateRole() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateRoleInput }) =>
      graphqlRequest<ops.UpdateRoleResult>(ops.UPDATE_ROLE, {
        id,
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.updateRole),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.roles() }),
  });
}

export function useDeleteRole() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteRoleResult>(ops.DELETE_ROLE, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.roles() }),
  });
}

/** Effective access for one resource URN (rbac GET /grants?resource_urn=). */
export function useContentGrants(resourceUrn: string | null) {
  return useQuery({
    queryKey: qk.contentGrants(resourceUrn ?? ""),
    queryFn: () =>
      graphqlRequest<ops.ContentGrantsResult>(ops.CONTENT_GRANTS, { resourceUrn }).then(
        (r) => r.contentGrants,
      ),
    enabled: !!resourceUrn,
  });
}

export function useCreateContentGrant() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateContentGrantInput) =>
      graphqlRequest<ops.CreateContentGrantResult>(ops.CREATE_CONTENT_GRANT, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createContentGrant),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "contentGrants"] }),
  });
}

export function useDeleteContentGrant() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteContentGrantResult>(ops.DELETE_CONTENT_GRANT, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "contentGrants"] }),
  });
}

export function useTenant(id: string) {
  return useQuery({
    queryKey: qk.tenant(id),
    queryFn: () => graphqlRequest<ops.TenantResult>(ops.TENANT, { id }).then((r) => r.tenant),
    enabled: !!id,
    staleTime: 5 * 60_000,
  });
}

/** (Re)generates the tenant's embed secret — the response's embedSecret is
 * shown exactly once by the caller; it cannot be re-fetched afterward. */
export function useSetEmbedConfig(tenantId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (allowedOrigins: string[]) =>
      graphqlRequest<ops.SetEmbedConfigResultWrapper>(ops.SET_EMBED_CONFIG, {
        tenantId,
        allowedOrigins,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.setEmbedConfig),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.tenant(tenantId) }),
  });
}

// ---- BYO-P4: per-tenant OIDC IdP config ------------------------------------
export function useTenantIdp() {
  return useQuery({
    queryKey: ["admin", "tenantIdp"],
    queryFn: () => graphqlRequest<ops.TenantIdpResult>(ops.TENANT_IDP).then((r) => r.tenantIdp),
  });
}

export function useSetTenantIdp() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: import("./types").SetTenantIdpInput) =>
      graphqlRequest<ops.SetTenantIdpResult>(ops.SET_TENANT_IDP, {
        input, idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.setTenantIdp),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "tenantIdp"] }),
  });
}

export function useDeleteTenantIdp() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => graphqlRequest<ops.DeleteTenantIdpResult>(ops.DELETE_TENANT_IDP).then((r) => r.deleteTenantIdp),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "tenantIdp"] }),
  });
}

export function useAuditEvents(vars: AuditEventsFilter = {}) {
  return useInfiniteQuery({
    queryKey: qk.auditEvents(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.AuditEventsResult>(ops.AUDIT_EVENTS, {
        first: PAGE,
        after: pageParam,
        ...vars,
      }).then((r) => r.auditEvents),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

/* ------- kill switches (agent-runtime + tool-plane, emergency stop) ------- */
export function useAgentKillSwitches() {
  return useQuery({
    queryKey: qk.agentKillSwitches(),
    queryFn: () => graphqlRequest<ops.AgentKillSwitchesResult>(ops.AGENT_KILL_SWITCHES).then((r) => r.agentKillSwitches),
  });
}

export function useToolKillSwitches() {
  return useQuery({
    queryKey: qk.toolKillSwitches(),
    queryFn: () => graphqlRequest<ops.ToolKillSwitchesResult>(ops.TOOL_KILL_SWITCHES).then((r) => r.toolKillSwitches),
  });
}

export function useCreateAgentKillSwitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { agentKey: string; scope?: string; version?: number; tenantId?: string; reason: string }) =>
      graphqlRequest<ops.CreateAgentKillSwitchResult>(ops.CREATE_AGENT_KILL_SWITCH, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.agentKillSwitches() }),
  });
}

export function useDeleteAgentKillSwitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (killId: string) =>
      graphqlRequest<ops.DeleteAgentKillSwitchResult>(ops.DELETE_AGENT_KILL_SWITCH, { killId }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.agentKillSwitches() }),
  });
}

export function useCreateToolKillSwitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { toolId: string; scope: string; version?: string; tenantId?: string; reason: string }) =>
      graphqlRequest<ops.CreateToolKillSwitchResult>(ops.CREATE_TOOL_KILL_SWITCH, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.toolKillSwitches() }),
  });
}

export function useDeleteToolKillSwitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteToolKillSwitchResult>(ops.DELETE_TOOL_KILL_SWITCH, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.toolKillSwitches() }),
  });
}

/** Run the authz "why was I denied" debug trace on demand (rbac-service POST
 * /authz/explain, a Query field server-side — no state changes). Needs
 * audit.log.read. */
export function useExplainAuthz() {
  return useMutation({
    mutationFn: (input: ExplainAuthzInput) =>
      graphqlRequest<ops.ExplainAuthzResult>(ops.EXPLAIN_AUTHZ, { input }).then((r) => r.explainAuthz),
  });
}

/* ------- audit: chain-integrity verify + compliance packs ----------------- */
export function useVerifyChainIntegrity() {
  return useMutation({
    mutationFn: (vars: { date: string; tenantId?: string }) =>
      graphqlRequest<ops.VerifyChainIntegrityResult>(ops.VERIFY_CHAIN_INTEGRITY, vars).then(
        (r) => r.verifyChainIntegrity,
      ),
  });
}

export function useGenerateSoc2Pack() {
  return useMutation({
    mutationFn: (vars: { from: string; to: string }) =>
      graphqlRequest<ops.GenerateSoc2PackResult>(ops.GENERATE_SOC2_PACK, vars).then((r) => r.generateSoc2Pack),
  });
}

export function useGenerateAiDecisionLog() {
  return useMutation({
    mutationFn: (vars: { from: string; to: string; agentId?: string }) =>
      graphqlRequest<ops.GenerateAiDecisionLogResult>(ops.GENERATE_AI_DECISION_LOG, vars).then(
        (r) => r.generateAiDecisionLog,
      ),
  });
}

/** Poll an async compliance-pack job until it settles (succeeded|failed). */
export function useComplianceOperation(
  id: string | null,
  options: { refetchInterval?: number | false | ((query: { state: { data?: ComplianceJob | null } }) => number | false) } = {},
) {
  return useQuery({
    queryKey: qk.complianceOperation(id ?? ""),
    queryFn: () =>
      graphqlRequest<ops.ComplianceOperationResult>(ops.COMPLIANCE_OPERATION, { id }).then(
        (r) => r.complianceOperation,
      ),
    enabled: !!id,
    refetchInterval: options.refetchInterval as never,
  });
}

/* ------- memory (memory-service): browse, single record, erasure, stats --- */
export function useMemories(vars: { scope?: string; scopeRef?: string; status?: string; tags?: string[] } = {}) {
  return useInfiniteQuery({
    queryKey: qk.memories(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.MemoriesResult>(ops.MEMORIES, { ...vars, first: PAGE, after: pageParam }).then(
        (r) => r.memories,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useMemory(id: string | null) {
  return useQuery({
    queryKey: qk.memory(id ?? ""),
    queryFn: () => graphqlRequest<ops.MemoryResult>(ops.MEMORY, { id }).then((r) => r.memory),
    enabled: !!id,
  });
}

export function useMemoryStats() {
  return useQuery({
    queryKey: qk.memoryStats(),
    queryFn: () => graphqlRequest<ops.MemoryStatsResult>(ops.MEMORY_STATS).then((r) => r.memoryStats),
  });
}

/** Poll a right-to-be-forgotten erasure request. Callers control polling via
 * `refetchInterval` while status is received|running|verifying. */
export function useErasure(
  id: string | null,
  options: { refetchInterval?: number | false | ((query: { state: { data?: ErasureRequest } }) => number | false) } = {},
) {
  return useQuery({
    queryKey: qk.erasure(id ?? ""),
    queryFn: () => graphqlRequest<ops.ErasureResult>(ops.ERASURE, { id }).then((r) => r.erasure),
    enabled: !!id,
    refetchInterval: options.refetchInterval as never,
  });
}

/** Start a right-to-be-forgotten erasure (compliance-sensitive, IRREVERSIBLE).
 * Needs memory.erasure.create. */
export function useRequestMemoryErasure() {
  return useMutation({
    mutationFn: (vars: { subjectId: string; subjectType?: string }) =>
      graphqlRequest<ops.RequestMemoryErasureResult>(ops.REQUEST_MEMORY_ERASURE, vars).then(
        (r) => r.requestMemoryErasure,
      ),
  });
}

/* ------- Tier 2a: eval (eval-service) --------------------------------------- */
export function useEvalSuite(suiteId: string, version?: number) {
  return useQuery({
    queryKey: qk.evalSuite(suiteId, version),
    queryFn: () => graphqlRequest<ops.EvalSuiteResult>(ops.EVAL_SUITE, { suiteId, version }).then((r) => r.evalSuite),
    enabled: !!suiteId,
  });
}

export function useCreateEvalSuite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateEvalSuiteInput) =>
      graphqlRequest<ops.CreateEvalSuiteResult>(ops.CREATE_EVAL_SUITE, { input }).then((r) => r.createEvalSuite),
    onSuccess: (d) => client.setQueryData(qk.evalSuite(d.suiteId, d.version), d),
  });
}

export function useUpdateEvalSuite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateEvalSuiteInput) =>
      graphqlRequest<ops.UpdateEvalSuiteResult>(ops.UPDATE_EVAL_SUITE, { input }).then((r) => r.updateEvalSuite),
    onSuccess: (d) => client.setQueryData(qk.evalSuite(d.suiteId, d.version), d),
  });
}

export function useEvalRuns(vars: { agentKey?: string; trigger?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.evalRuns(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.EvalRunsResult>(ops.EVAL_RUNS, { first: PAGE, after: pageParam, ...vars }).then(
        (r) => r.evalRuns,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
    enabled: !!vars.agentKey,
  });
}

export function useEvalRun(id: string) {
  return useQuery({
    queryKey: qk.evalRun(id),
    queryFn: () => graphqlRequest<ops.EvalRunResult>(ops.EVAL_RUN, { id }).then((r) => r.evalRun),
    enabled: !!id,
  });
}

/** Starts a REAL synchronous scoring run (eval-service executes it inline and
 * returns the completed/failed run) — there is no "pending" intermediate state
 * to poll for. */
export function useCreateEvalRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateEvalRunInput) =>
      graphqlRequest<ops.CreateEvalRunResult>(ops.CREATE_EVAL_RUN, { input }).then((r) => r.createEvalRun),
    onSuccess: () => client.invalidateQueries({ queryKey: ["eval", "runs"] }),
  });
}

export function useCancelEvalRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.CancelEvalRunResult>(ops.CANCEL_EVAL_RUN, { id }).then((r) => r.cancelEvalRun),
    onSuccess: (d) => {
      client.setQueryData(qk.evalRun(d.id), { evalRun: d });
      client.invalidateQueries({ queryKey: ["eval", "runs"] });
    },
  });
}

export function useEvalDatasets(vars: { agentKey?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.evalDatasets(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.EvalDatasetsResult>(ops.EVAL_DATASETS, { first: PAGE, after: pageParam, ...vars }).then(
        (r) => r.evalDatasets,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateEvalDataset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateEvalDatasetInput) =>
      graphqlRequest<ops.CreateEvalDatasetResult>(ops.CREATE_EVAL_DATASET, { input }).then((r) => r.createEvalDataset),
    onSuccess: () => client.invalidateQueries({ queryKey: ["eval", "datasets"] }),
  });
}

export function useFreezeEvalDataset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { datasetKey: string; version: number }) =>
      graphqlRequest<ops.FreezeEvalDatasetResult>(ops.FREEZE_EVAL_DATASET, vars).then((r) => r.freezeEvalDataset),
    onSuccess: () => client.invalidateQueries({ queryKey: ["eval", "datasets"] }),
  });
}

export function useEvalCases(vars: { datasetKey?: string; datasetVersion?: number; status?: string; source?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.evalCases(vars),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.EvalCasesResult>(ops.EVAL_CASES, { first: PAGE, after: pageParam, ...vars }).then(
        (r) => r.evalCases,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateEvalCase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateEvalCaseInput) =>
      graphqlRequest<ops.CreateEvalCaseResult>(ops.CREATE_EVAL_CASE, { input }).then((r) => r.createEvalCase),
    onSuccess: () => client.invalidateQueries({ queryKey: ["eval", "cases"] }),
  });
}

function invalidateEvalCases(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: ["eval", "cases"] });
}

export function usePromoteEvalCase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.PromoteEvalCaseResult>(ops.PROMOTE_EVAL_CASE, { id }).then((r) => r.promoteEvalCase),
    onSuccess: () => invalidateEvalCases(client),
  });
}

export function useAttestEvalCase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; attestedBy: string }) =>
      graphqlRequest<ops.AttestEvalCaseResult>(ops.ATTEST_EVAL_CASE, vars).then((r) => r.attestEvalCase),
    onSuccess: () => invalidateEvalCases(client),
  });
}

export function useRejectEvalCase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.RejectEvalCaseResult>(ops.REJECT_EVAL_CASE, { id }).then((r) => r.rejectEvalCase),
    onSuccess: () => invalidateEvalCases(client),
  });
}

export function useRetireEvalCase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.RetireEvalCaseResult>(ops.RETIRE_EVAL_CASE, { id }).then((r) => r.retireEvalCase),
    onSuccess: () => invalidateEvalCases(client),
  });
}

export function useUpdateEvalCase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; patch: EvalCasePatchInput }) =>
      graphqlRequest<ops.UpdateEvalCaseResult>(ops.UPDATE_EVAL_CASE, vars).then((r) => r.updateEvalCase),
    onSuccess: () => invalidateEvalCases(client),
  });
}

export function useEvalScorers() {
  return useInfiniteQuery({
    queryKey: qk.evalScorers(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.EvalScorersResult>(ops.EVAL_SCORERS, { first: PAGE, after: pageParam }).then(
        (r) => r.evalScorers,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateEvalScorer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateEvalScorerInput) =>
      graphqlRequest<ops.CreateEvalScorerResult>(ops.CREATE_EVAL_SCORER, { input }).then((r) => r.createEvalScorer),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.evalScorers() }),
  });
}

export function useUpdateEvalScorer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateEvalScorerInput) =>
      graphqlRequest<ops.UpdateEvalScorerResult>(ops.UPDATE_EVAL_SCORER, { input }).then((r) => r.updateEvalScorer),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.evalScorers() }),
  });
}

export function useActivateEvalScorer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { scorerKey: string; version: number }) =>
      graphqlRequest<ops.ActivateEvalScorerResult>(ops.ACTIVATE_EVAL_SCORER, vars).then((r) => r.activateEvalScorer),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.evalScorers() }),
  });
}

export function useEvalCanary(comparisonId: string | null) {
  return useQuery({
    queryKey: qk.evalCanary(comparisonId ?? ""),
    queryFn: () =>
      graphqlRequest<ops.EvalCanaryResult>(ops.EVAL_CANARY, { comparisonId }).then((r) => r.evalCanary),
    enabled: !!comparisonId,
  });
}

export function useCreateEvalCanary() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateEvalCanaryInput) =>
      graphqlRequest<ops.CreateEvalCanaryResult>(ops.CREATE_EVAL_CANARY, { input }).then((r) => r.createEvalCanary),
    onSuccess: (d) => client.setQueryData(qk.evalCanary(d.comparisonId), { evalCanary: d }),
  });
}

export function useIngestEvalCanarySamples() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { comparisonId: string; pairedScores: JSONValue }) =>
      graphqlRequest<ops.IngestEvalCanarySamplesResult>(ops.INGEST_EVAL_CANARY_SAMPLES, vars).then(
        (r) => r.ingestEvalCanarySamples,
      ),
    onSuccess: (d) => client.setQueryData(qk.evalCanary(d.comparisonId), { evalCanary: d }),
  });
}

export function useStopEvalCanary() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (comparisonId: string) =>
      graphqlRequest<ops.StopEvalCanaryResult>(ops.STOP_EVAL_CANARY, { comparisonId }).then((r) => r.stopEvalCanary),
    onSuccess: (d) => client.setQueryData(qk.evalCanary(d.comparisonId), { evalCanary: d }),
  });
}

/** The score-trend series for an agent — the raw data behind the model-version
 * scorecard comparison view. */
export function useEvalTrends(agentKey: string, scorer?: string, window?: string) {
  return useQuery({
    queryKey: qk.evalTrends(agentKey, scorer, window),
    queryFn: () =>
      graphqlRequest<ops.EvalTrendsResult>(ops.EVAL_TRENDS, { agentKey, scorer, window }).then((r) => r.evalTrends),
    enabled: !!agentKey,
  });
}

export function useEvalSlos(agentKey: string, window?: string) {
  return useQuery({
    queryKey: qk.evalSlos(agentKey, window),
    queryFn: () => graphqlRequest<ops.EvalSlosResult>(ops.EVAL_SLOS, { agentKey, window }).then((r) => r.evalSlos),
    enabled: !!agentKey,
  });
}

export function useSetEvalSloTargets() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { agentKey: string; agentVersion?: string; targets: JSONValue }) =>
      graphqlRequest<ops.SetEvalSloTargetsResult>(ops.SET_EVAL_SLO_TARGETS, vars).then((r) => r.setEvalSloTargets),
    onSuccess: (_r, vars) => client.invalidateQueries({ queryKey: ["eval", "slos", vars.agentKey] }),
  });
}

/* ------- Tier 2a: ai-gateway admin ------------------------------------------ */
export function useAiProviders() {
  return useInfiniteQuery({
    queryKey: qk.aiProviders(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.AiProvidersResult>(ops.AI_PROVIDERS, { first: PAGE, after: pageParam }).then(
        (r) => r.aiProviders,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

function invalidateAiProviders(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: qk.aiProviders() });
}

export function useCreateAiProvider() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateAiProviderInput) =>
      graphqlRequest<ops.CreateAiProviderResult>(ops.CREATE_AI_PROVIDER, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createAiProvider),
    onSuccess: () => invalidateAiProviders(client),
  });
}

export function usePatchAiProvider() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { deploymentId: string; input: PatchAiProviderInput; force?: boolean }) =>
      graphqlRequest<ops.PatchAiProviderResult>(ops.PATCH_AI_PROVIDER, vars).then((r) => r.patchAiProvider),
    onSuccess: () => invalidateAiProviders(client),
  });
}

export function useDrainAiProvider() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { deploymentId: string; force?: boolean }) =>
      graphqlRequest<ops.DrainAiProviderResult>(ops.DRAIN_AI_PROVIDER, vars).then((r) => r.drainAiProvider),
    onSuccess: () => invalidateAiProviders(client),
  });
}

export function useAiLadder(requestClass: string) {
  return useQuery({
    queryKey: qk.aiLadder(requestClass),
    queryFn: () => graphqlRequest<ops.AiLadderResult>(ops.AI_LADDER, { requestClass }).then((r) => r.aiLadder),
    enabled: !!requestClass,
  });
}

export function usePutAiLadder() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { requestClass: string; rungs: JSONValue; maxRung?: number; scope?: string }) =>
      graphqlRequest<ops.PutAiLadderResult>(ops.PUT_AI_LADDER, vars).then((r) => r.putAiLadder),
    onSuccess: (_r, vars) => client.invalidateQueries({ queryKey: qk.aiLadder(vars.requestClass) }),
  });
}

export function useAiBudgets(vars: { scopeType?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.aiBudgets(vars.scopeType),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.AiBudgetsResult>(ops.AI_BUDGETS, { first: PAGE, after: pageParam, ...vars }).then(
        (r) => r.aiBudgets,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

function invalidateAiBudgets(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: ["aigateway", "budgets"] });
}

export function useCreateAiBudget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateAiBudgetInput) =>
      graphqlRequest<ops.CreateAiBudgetResult>(ops.CREATE_AI_BUDGET, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createAiBudget),
    onSuccess: () => invalidateAiBudgets(client),
  });
}

export function useUpdateAiBudget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; input: PatchAiBudgetInput }) =>
      graphqlRequest<ops.UpdateAiBudgetResult>(ops.UPDATE_AI_BUDGET, vars).then((r) => r.updateAiBudget),
    onSuccess: () => invalidateAiBudgets(client),
  });
}

export function useDeleteAiBudget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => graphqlRequest<ops.DeleteAiBudgetResult>(ops.DELETE_AI_BUDGET, { id }).then((r) => r.deleteAiBudget),
    onSuccess: () => invalidateAiBudgets(client),
  });
}

export function useAiSpend(scopeType: string, scopeRef: string, window?: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.aiSpend(scopeType, scopeRef, window),
    queryFn: () =>
      graphqlRequest<ops.AiSpendResult>(ops.AI_SPEND, { scopeType, scopeRef, window }).then((r) => r.aiSpend),
    enabled: !!scopeType && !!scopeRef && (options.enabled ?? true),
  });
}

// ADDED (provider-agnostic + cost-detail): real per-provider/model breakdown.
export function useAiCostBreakdown(windowHours = 24) {
  return useQuery({
    queryKey: qk.aiCostBreakdown(windowHours),
    queryFn: () =>
      graphqlRequest<ops.AiCostBreakdownResult>(ops.AI_COST_BREAKDOWN, { windowHours }).then(
        (r) => r.aiCostBreakdown,
      ),
  });
}

export function useAiKeys() {
  return useInfiniteQuery({
    queryKey: qk.aiKeys(),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.AiKeysResult>(ops.AI_KEYS, { first: PAGE, after: pageParam }).then((r) => r.aiKeys),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

function invalidateAiKeys(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: qk.aiKeys() });
}

/** Issues a new virtual key; the returned `secret` is shown ONCE — the caller
 * must surface it immediately and never re-fetch it. */
export function useCreateAiVirtualKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateAiVirtualKeyInput) =>
      graphqlRequest<ops.CreateAiVirtualKeyResult>(ops.CREATE_AI_VIRTUAL_KEY, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createAiVirtualKey),
    onSuccess: () => invalidateAiKeys(client),
  });
}

export function useRevokeAiVirtualKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RevokeAiVirtualKeyResult>(ops.REVOKE_AI_VIRTUAL_KEY, { id }).then((r) => r.revokeAiVirtualKey),
    onSuccess: () => invalidateAiKeys(client),
  });
}

/** Rotates a virtual key; the returned `secret` is shown ONCE. */
export function useRotateAiVirtualKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RotateAiVirtualKeyResult>(ops.ROTATE_AI_VIRTUAL_KEY, { id }).then((r) => r.rotateAiVirtualKey),
    onSuccess: () => invalidateAiKeys(client),
  });
}

export function useAiGuardrailPolicy() {
  return useQuery({
    queryKey: qk.aiGuardrailPolicy(),
    queryFn: () => graphqlRequest<ops.AiGuardrailPolicyResult>(ops.AI_GUARDRAIL_POLICY).then((r) => r.aiGuardrailPolicy),
  });
}

export function usePutAiGuardrailPolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (policy: JSONValue) =>
      graphqlRequest<ops.PutAiGuardrailPolicyResult>(ops.PUT_AI_GUARDRAIL_POLICY, { policy }).then(
        (r) => r.putAiGuardrailPolicy,
      ),
    onSuccess: (d) => client.setQueryData(qk.aiGuardrailPolicy(), { aiGuardrailPolicy: d }),
  });
}

export type InfiniteConn<T> = UseInfiniteQueryResult<{ pages: Connection<T>[] }, Error>;
export { flatten };

// ============================================================================
// Tier 2b: notification-service — inbox, preferences, rules, webhooks,
// templates, admin ops.
// ============================================================================
export function useNotifications(vars: { unread?: boolean } = {}) {
  return useInfiniteQuery({
    queryKey: qk.notifications(vars),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.NotificationsResult>(ops.NOTIFICATIONS, {
        first: PAGE,
        after: pageParam,
        ...vars,
      }).then((r) => r.notifications),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

/** Unread count for the top-bar bell. Polls every 60s — the inbox has no push
 * channel today; the badge is a real count, refreshed on window focus too. */
export function useNotificationUnreadCount() {
  return useQuery({
    queryKey: qk.notificationUnreadCount(),
    queryFn: () =>
      graphqlRequest<ops.NotificationUnreadCountResult>(ops.NOTIFICATION_UNREAD_COUNT).then(
        (r) => r.notificationUnreadCount,
      ),
    refetchInterval: 60_000,
  });
}

function invalidateInbox(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: ["notifications", "inbox"] });
  void client.invalidateQueries({ queryKey: qk.notificationUnreadCount() });
}

export function useMarkNotificationRead() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { id: string; read: boolean }): Promise<boolean> =>
      vars.read
        ? graphqlRequest<ops.MarkNotificationReadResult>(ops.MARK_NOTIFICATION_READ, { id: vars.id }).then((r) => r.markNotificationRead)
        : graphqlRequest<ops.MarkNotificationUnreadResult>(ops.MARK_NOTIFICATION_UNREAD, { id: vars.id }).then((r) => r.markNotificationUnread),
    onSuccess: () => invalidateInbox(client),
  });
}

export function useMarkAllNotificationsRead() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      graphqlRequest<ops.MarkAllNotificationsReadResult>(ops.MARK_ALL_NOTIFICATIONS_READ).then(
        (r) => r.markAllNotificationsRead,
      ),
    onSuccess: () => invalidateInbox(client),
  });
}

export function useNotificationPreferences() {
  return useQuery({
    queryKey: qk.notificationPreferences(),
    queryFn: () =>
      graphqlRequest<ops.NotificationPreferencesResult>(ops.NOTIFICATION_PREFERENCES).then(
        (r) => r.notificationPreferences,
      ),
  });
}

export function useUpdateNotificationPreferences() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      channelOverrides?: Record<string, string[]>;
      mutes?: JSONValue;
      quietHours?: JSONValue;
      digestConfig?: Record<string, string>;
    }) =>
      graphqlRequest<ops.UpdateNotificationPreferencesResult>(ops.UPDATE_NOTIFICATION_PREFERENCES, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.updateNotificationPreferences),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationPreferences() }),
  });
}

export function useNotificationRules() {
  return useInfiniteQuery({
    queryKey: qk.notificationRules(),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.NotificationRulesResult>(ops.NOTIFICATION_RULES, {
        first: PAGE,
        after: pageParam,
      }).then((r) => r.notificationRules),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export interface NotificationRuleInputVars {
  scope?: string;
  subjectType?: string;
  subjectId?: string;
  eventTypes?: string[];
  resourceFilter?: JSONValue;
  channels?: string[];
  digestEnabled?: boolean;
  digestWindow?: string;
  active?: boolean;
}

export function useCreateNotificationRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: NotificationRuleInputVars) =>
      graphqlRequest<ops.CreateNotificationRuleResult>(ops.CREATE_NOTIFICATION_RULE, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createNotificationRule),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationRules() }),
  });
}

export function useUpdateNotificationRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; input: NotificationRuleInputVars }) =>
      graphqlRequest<ops.UpdateNotificationRuleResult>(ops.UPDATE_NOTIFICATION_RULE, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.updateNotificationRule),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationRules() }),
  });
}

export function useDeleteNotificationRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteNotificationRuleResult>(ops.DELETE_NOTIFICATION_RULE, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationRules() }),
  });
}

export function useNotificationWebhooks() {
  return useInfiniteQuery({
    queryKey: qk.notificationWebhooks(),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.NotificationWebhooksResult>(ops.NOTIFICATION_WEBHOOKS, {
        first: PAGE,
        after: pageParam,
      }).then((r) => r.notificationWebhooks),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useCreateNotificationWebhook() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { url: string; eventTypes: string[]; active?: boolean }) =>
      graphqlRequest<ops.CreateNotificationWebhookResult>(ops.CREATE_NOTIFICATION_WEBHOOK, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createNotificationWebhook),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationWebhooks() }),
  });
}

export function useUpdateNotificationWebhook() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; input: { url?: string; eventTypes?: string[]; active?: boolean } }) =>
      graphqlRequest<ops.UpdateNotificationWebhookResult>(ops.UPDATE_NOTIFICATION_WEBHOOK, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.updateNotificationWebhook),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationWebhooks() }),
  });
}

export function useDeleteNotificationWebhook() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.DeleteNotificationWebhookResult>(ops.DELETE_NOTIFICATION_WEBHOOK, { id }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationWebhooks() }),
  });
}

export function useRotateNotificationWebhookSecret() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      graphqlRequest<ops.RotateNotificationWebhookSecretResult>(ops.ROTATE_NOTIFICATION_WEBHOOK_SECRET, {
        id,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.rotateNotificationWebhookSecret),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.notificationWebhooks() }),
  });
}

export function useNotificationWebhookDeliveries(webhookId: string | null) {
  return useInfiniteQuery({
    queryKey: qk.notificationWebhookDeliveries(webhookId ?? ""),
    enabled: !!webhookId,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.NotificationWebhookDeliveriesResult>(ops.NOTIFICATION_WEBHOOK_DELIVERIES, {
        webhookId,
        first: PAGE,
        after: pageParam,
      }).then((r) => r.notificationWebhookDeliveries),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useRedeliverNotificationWebhookDelivery() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { webhookId: string; deliveryId: string }) =>
      graphqlRequest<ops.RedeliverNotificationWebhookDeliveryResult>(
        ops.REDELIVER_NOTIFICATION_WEBHOOK_DELIVERY,
        { ...vars, idempotencyKey: crypto.randomUUID() },
      ),
    onSuccess: (_d, vars) =>
      client.invalidateQueries({ queryKey: qk.notificationWebhookDeliveries(vars.webhookId) }),
  });
}

export function useNotificationTemplates(key: string) {
  return useQuery({
    queryKey: qk.notificationTemplates(key),
    enabled: key.length > 0,
    queryFn: () =>
      graphqlRequest<ops.NotificationTemplatesResult>(ops.NOTIFICATION_TEMPLATES, { key }).then(
        (r) => r.notificationTemplates,
      ),
  });
}

export function useCreateNotificationTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      key: string; channel: string; locale?: string;
      subjectTpl?: string; bodyHtmlTpl?: string; bodyTextTpl?: string;
    }) =>
      graphqlRequest<ops.CreateNotificationTemplateResult>(ops.CREATE_NOTIFICATION_TEMPLATE, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.createNotificationTemplate),
    onSuccess: (d) => client.invalidateQueries({ queryKey: qk.notificationTemplates(d.key) }),
  });
}

export function usePublishNotificationTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { key: string; templateId: string }) =>
      graphqlRequest<ops.PublishNotificationTemplateResult>(ops.PUBLISH_NOTIFICATION_TEMPLATE, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.publishNotificationTemplate),
    onSuccess: (d) => client.invalidateQueries({ queryKey: qk.notificationTemplates(d.key) }),
  });
}

export function usePreviewNotificationTemplate() {
  return useMutation({
    mutationFn: (vars: { key: string; channel?: string; locale?: string; sampleEvent?: JSONValue }) =>
      graphqlRequest<ops.PreviewNotificationTemplateResult>(ops.PREVIEW_NOTIFICATION_TEMPLATE, vars).then(
        (r) => r.previewNotificationTemplate,
      ),
  });
}

export function useNotificationDeliveryStats(window?: string) {
  return useQuery({
    queryKey: qk.notificationDeliveryStats(window),
    queryFn: () =>
      graphqlRequest<ops.NotificationDeliveryStatsResult>(ops.NOTIFICATION_DELIVERY_STATS, { window }).then(
        (r) => r.notificationDeliveryStats,
      ),
  });
}

export function useEmailSuppressions() {
  return useQuery({
    queryKey: qk.emailSuppressions(),
    queryFn: () =>
      graphqlRequest<ops.EmailSuppressionsResult>(ops.EMAIL_SUPPRESSIONS).then((r) => r.emailSuppressions),
  });
}

export function useClearEmailSuppression() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (emailHash: string) =>
      graphqlRequest<ops.ClearEmailSuppressionResult>(ops.CLEAR_EMAIL_SUPPRESSION, { emailHash }),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.emailSuppressions() }),
  });
}

// ============================================================================
// Tier 2b: tool-plane registry admin.
// ============================================================================
export function useTools(vars: { ownerService?: string } = {}) {
  return useInfiniteQuery({
    queryKey: qk.tools(vars),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      graphqlRequest<ops.ToolsResult>(ops.TOOLS, { first: PAGE, after: pageParam, ...vars }).then(
        (r) => r.tools,
      ),
    getNextPageParam: (last) => (last.pageInfo.hasMore ? last.pageInfo.nextCursor : undefined),
  });
}

export function useToolHealth(toolId: string | null) {
  return useQuery({
    queryKey: qk.toolHealth(toolId ?? ""),
    enabled: !!toolId,
    queryFn: () =>
      graphqlRequest<ops.ToolHealthResult>(ops.TOOL_HEALTH, { toolId }).then((r) => r.toolHealth),
  });
}

export function useToolSchema(toolId: string | null, version?: string) {
  return useQuery({
    queryKey: qk.toolSchema(toolId ?? "", version),
    enabled: !!toolId,
    queryFn: () =>
      graphqlRequest<ops.ToolSchemaResult>(ops.TOOL_SCHEMA, { toolId, version }).then((r) => r.toolSchema),
  });
}

export function useRegisterTool() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      toolId: string; displayName?: string; ownerService: string; ownerTeam?: string;
      enabledByDefault?: boolean; sideEffects?: string; tags?: string[];
    }) =>
      graphqlRequest<ops.RegisterToolResult>(ops.REGISTER_TOOL, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.registerTool),
    onSuccess: () => client.invalidateQueries({ queryKey: ["tools", "catalog"] }),
  });
}

export function useAddToolVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      toolId: string;
      input: {
        version: string; semanticDescription: string; inputSchema?: JSONValue; outputSchema?: JSONValue;
        permissionTier?: string; costWeight?: number; declaredSla?: JSONValue; sideEffects?: string; examples?: JSONValue;
      };
    }) =>
      graphqlRequest<ops.AddToolVersionResult>(ops.ADD_TOOL_VERSION, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.addToolVersion),
    onSuccess: (_d, vars) => client.invalidateQueries({ queryKey: qk.toolHealth(vars.toolId) }),
  });
}

export function usePublishToolVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { toolId: string; version: string }) =>
      graphqlRequest<ops.PublishToolVersionResult>(ops.PUBLISH_TOOL_VERSION, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.publishToolVersion),
    onSuccess: (_d, vars) => client.invalidateQueries({ queryKey: qk.toolHealth(vars.toolId) }),
  });
}

export function useDeprecateToolVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { toolId: string; version: string; deprecationEndsAt?: string }) =>
      graphqlRequest<ops.DeprecateToolVersionResult>(ops.DEPRECATE_TOOL_VERSION, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.deprecateToolVersion),
    onSuccess: (_d, vars) => client.invalidateQueries({ queryKey: qk.toolHealth(vars.toolId) }),
  });
}

export function useRetireToolVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { toolId: string; version: string; force?: boolean; reason?: string }) =>
      graphqlRequest<ops.RetireToolVersionResult>(ops.RETIRE_TOOL_VERSION, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.retireToolVersion),
    onSuccess: (_d, vars) => client.invalidateQueries({ queryKey: qk.toolHealth(vars.toolId) }),
  });
}

export function useSetToolEnablement() {
  return useMutation({
    mutationFn: (vars: {
      toolId: string;
      input: { enabled: boolean; maxTierOverride?: string; argumentConstraints?: JSONValue; rateLimitPerMin?: number };
    }) =>
      graphqlRequest<ops.SetToolEnablementResult>(ops.SET_TOOL_ENABLEMENT, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.setToolEnablement),
  });
}

export function useByoSubmissions(status?: string) {
  return useQuery({
    queryKey: qk.byoSubmissions(status),
    queryFn: () =>
      graphqlRequest<ops.ByoSubmissionsResult>(ops.BYO_SUBMISSIONS, { status }).then(
        (r) => r.byoSubmissions,
      ),
  });
}

export function useSubmitByoTool() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      manifest?: JSONValue; endpointUrl: string; authMethod?: string;
      requestedTier?: string; egressDescription?: string;
    }) =>
      graphqlRequest<ops.SubmitByoToolResult>(ops.SUBMIT_BYO_TOOL, {
        input,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.submitByoTool),
    onSuccess: () => client.invalidateQueries({ queryKey: ["tools", "byo"] }),
  });
}

export function useDecideByoTool() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; decision: "approve" | "reject"; message?: string }) =>
      vars.decision === "approve"
        ? graphqlRequest<ops.ApproveByoToolResult>(ops.APPROVE_BYO_TOOL, {
            id: vars.id, message: vars.message, idempotencyKey: crypto.randomUUID(),
          }).then((r) => r.approveByoTool)
        : graphqlRequest<ops.RejectByoToolResult>(ops.REJECT_BYO_TOOL, {
            id: vars.id, message: vars.message, idempotencyKey: crypto.randomUUID(),
          }).then((r) => r.rejectByoTool),
    onSuccess: () => client.invalidateQueries({ queryKey: ["tools", "byo"] }),
  });
}

// ============================================================================
// Tier 2b: agent-runtime catalog/registry.
// ============================================================================
export function useAgentDefinitions() {
  return useQuery({
    queryKey: qk.agentDefinitions(),
    queryFn: () =>
      graphqlRequest<ops.AgentDefinitionsResult>(ops.AGENT_DEFINITIONS).then((r) => r.agentDefinitions),
  });
}

export function useCreateCustomAgent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ops.CreateCustomAgentInput) =>
      graphqlRequest<ops.CreateCustomAgentResult>(ops.CREATE_CUSTOM_AGENT, { input }).then(
        (r) => r.createCustomAgent,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.agentDefinitions() }),
  });
}

export function useAutobindPersonaCopilots() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { roles: string[]; proposeTool?: string | null }) =>
      graphqlRequest<ops.AutobindPersonaCopilotsResult>(ops.AUTOBIND_PERSONA_COPILOTS, vars).then(
        (r) => r.autobindPersonaCopilots,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.agentDefinitions() }),
  });
}

export function useAgentCeilings(enabled = true) {
  return useQuery({
    queryKey: qk.agentCeilings(),
    enabled,
    queryFn: () =>
      graphqlRequest<ops.AgentCeilingsResult>(ops.AGENT_CEILINGS).then((r) => r.agentCeilings),
  });
}

export function useSetAgentCeilings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { maxBudgetTokens: number; maxTier: string }) =>
      graphqlRequest<ops.SetAgentCeilingsResult>(ops.SET_AGENT_CEILINGS, vars).then(
        (r) => r.setAgentCeilings,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.agentCeilings() }),
  });
}

export function useAgentVersions(agentKey: string | null) {
  return useQuery({
    queryKey: qk.agentVersions(agentKey ?? ""),
    enabled: !!agentKey,
    queryFn: () =>
      graphqlRequest<ops.AgentVersionsResult>(ops.AGENT_VERSIONS, { agentKey }).then(
        (r) => r.agentVersions,
      ),
  });
}

export function usePublishAgentVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { agentKey: string; version: number; force?: boolean; reason?: string }) =>
      graphqlRequest<ops.PublishAgentVersionResult>(ops.PUBLISH_AGENT_VERSION, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.publishAgentVersion),
    onSuccess: (_d, vars) => {
      void client.invalidateQueries({ queryKey: qk.agentVersions(vars.agentKey) });
      void client.invalidateQueries({ queryKey: qk.agentDefinitions() });
    },
  });
}

export function useTenantAgentConfig(agentKey: string | null) {
  return useQuery({
    queryKey: qk.tenantAgentConfig(agentKey ?? ""),
    enabled: !!agentKey,
    queryFn: () =>
      graphqlRequest<ops.TenantAgentConfigResult>(ops.TENANT_AGENT_CONFIG, { agentKey }).then(
        (r) => r.tenantAgentConfig,
      ),
  });
}

export function usePutTenantAgentConfig() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      agentKey: string;
      input: {
        enabled?: boolean; pinnedVersion?: number | null; promptParams?: JSONValue;
        autoExecutePolicy?: JSONValue; selfApproval?: boolean;
      };
    }) =>
      graphqlRequest<ops.PutTenantAgentConfigResult>(ops.PUT_TENANT_AGENT_CONFIG, {
        ...vars,
        idempotencyKey: crypto.randomUUID(),
      }).then((r) => r.putTenantAgentConfig),
    onSuccess: (_d, vars) =>
      client.invalidateQueries({ queryKey: qk.tenantAgentConfig(vars.agentKey) }),
  });
}

export function useAgentRunsList(vars: { agentKey?: string } = {}) {
  return useQuery({
    queryKey: qk.agentRuns(vars),
    queryFn: () =>
      graphqlRequest<ops.AgentRunsResult>(ops.AGENT_RUNS, { first: 100, ...vars }).then(
        (r) => r.agentRuns,
      ),
  });
}
