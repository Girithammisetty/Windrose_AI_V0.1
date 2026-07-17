/**
 * eval-service REST client (EVL-FR-*). Backs the eval flywheel: suites, runs,
 * the case curation queue, scorers, quality gates (promotion-blocking verdicts),
 * canary A/B comparisons, and score-trend/SLO tracking.
 *
 * Response-envelope quirk: eval-service's `dump_page` (app/api/serialize.py)
 * serializes list endpoints FLAT — `{data, next_cursor, has_more}` — unlike the
 * master envelope's `{data, page:{next_cursor, has_more}}` nesting every other
 * service in this BFF uses. `adaptPage` below normalizes it to the shared
 * `Page<T>` shape so `toConnection` (pagination.ts) works unchanged.
 */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

function adaptPage<T>(raw: { data?: T[]; next_cursor?: string | null; has_more?: boolean }): Page<T> {
  return {
    data: raw?.data ?? [],
    page: { next_cursor: raw?.next_cursor ?? null, has_more: raw?.has_more ?? false },
  };
}

/** eval-service Suite (app/domain/entities.py Suite). */
export interface EvalSuiteDTO {
  id: string;
  suite_id: string;
  agent_key: string;
  version: number;
  datasets: Record<string, unknown>[];
  scorers: Record<string, unknown>[];
  gate_rule: string;
  baseline_version?: string | null;
  judge_ladder_pin?: Record<string, unknown>;
  min_cases: number;
  created_at?: string;
}

/** eval-service EvalRun. */
export interface EvalRunDTO {
  id: string;
  trigger: string;
  agent_key: string;
  candidate: Record<string, unknown>;
  baseline?: Record<string, unknown> | null;
  suite_pins: Record<string, unknown>;
  memory_snapshot_ver?: string | null;
  status: string;
  totals: Record<string, unknown>;
  cost_usd: number;
  cost_cap_usd: number;
  temporal_workflow_id?: string | null;
  started_by: string;
  created_at?: string;
  updated_at?: string;
}

/** eval-service CaseResult (one scorer's verdict on one case within a run). */
export interface EvalCaseResultDTO {
  id: string;
  run_id: string;
  case_id: string;
  scorer_key: string;
  scorer_version: number;
  score: number;
  passed: boolean;
  details: Record<string, unknown>;
  trace_ref?: string | null;
  latency_ms?: number | null;
  cost_usd: number;
  weight: number;
  created_at?: string;
}

/** eval-service EvalCase (curation-queue item). */
export interface EvalCaseDTO {
  id: string;
  dataset_key: string;
  dataset_version: number;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  source: string;
  source_ref?: string | null;
  tags: string[];
  weight: number;
  status: string;
  anonymization_attested_by?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** eval-service Dataset (an eval dataset VERSION, distinct from dataset-service's Dataset). */
export interface EvalDatasetDTO {
  id: string;
  dataset_key: string;
  agent_key: string;
  version: number;
  status: string;
  description?: string | null;
  case_count: number;
  provenance_summary: Record<string, unknown>;
  frozen_by?: string | null;
  frozen_at?: string | null;
  created_by: string;
  created_at?: string;
  updated_at?: string;
}

/** eval-service Scorer. */
export interface EvalScorerDTO {
  id: string;
  scorer_key: string;
  version: number;
  kind: string;
  gate_eligible: boolean;
  config_schema: Record<string, unknown>;
  applicable_expected_kinds: string[];
  image_ref?: string | null;
  judge_prompt_ref?: string | null;
  judge_prompt_ver?: string | null;
  judge_agreement?: number | null;
  status: string;
  created_at?: string;
}

/** eval-service GateResult (a promotion-blocking gate verdict). */
export interface EvalGateResultDTO {
  id: string;
  gate_run_id: string;
  run_id: string;
  agent_key: string;
  content_digest: string;
  suite_id: string;
  suite_version: number;
  dataset_version: number;
  gate_passed: boolean;
  verdicts: Record<string, unknown>[];
  failed_cases_sample: Record<string, unknown>[];
  report_url?: string | null;
  created_at?: string;
}

/** eval-service CanaryComparison. */
export interface EvalCanaryDTO {
  id: string;
  comparison_id: string;
  agent_key: string;
  candidate_version: string;
  baseline_version: string;
  sample_spec: Record<string, unknown>;
  mode: string;
  status: string;
  report: Record<string, unknown>;
  samples: number;
  created_at?: string;
  updated_at?: string;
}

/** A score-trend point (eval-service TrendService.trends — a plain dict, not a dataclass). */
export interface EvalTrendPointDTO {
  run_id: string;
  agent_version?: string | null;
  scorer: string;
  mean?: number | null;
  pass_rate?: number | null;
  at: string;
}

/** An SLO rollup row (eval-service SloService.query — a plain dict). */
export interface EvalSloRowDTO {
  agent_key: string;
  agent_version?: string | null;
  tenant_id?: string | null;
  window: string;
  window_start: string;
  metrics: Record<string, unknown>;
  targets: Record<string, unknown>;
  sample_n: number;
}

export interface CreateEvalSuiteBody {
  suite_id: string;
  agent_key: string;
  datasets: Record<string, unknown>[];
  scorers: Record<string, unknown>[];
  gate_rule: string;
  baseline_version?: string;
  judge_ladder_pin?: Record<string, unknown>;
  min_cases?: number;
}

export interface EvalSuitePatchBody {
  datasets?: Record<string, unknown>[];
  scorers?: Record<string, unknown>[];
  gate_rule?: string;
  baseline_version?: string;
  judge_ladder_pin?: Record<string, unknown>;
  min_cases?: number;
}

export interface CreateEvalRunBody {
  trigger?: string;
  agent_key: string;
  candidate: Record<string, unknown>;
  suite_id: string;
  suite_version?: number;
  candidate_outputs?: Record<string, Record<string, unknown>>;
  baseline?: Record<string, unknown>;
  memory_snapshot_ver?: string;
  cost_cap_usd?: number;
}

export interface CreateEvalDatasetBody {
  dataset_key: string;
  agent_key: string;
  description?: string;
  provenance_summary?: Record<string, unknown>;
}

export interface CreateEvalCaseBody {
  dataset_key: string;
  agent_key?: string;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  source?: string;
  source_ref?: string;
  tags?: string[];
  weight?: number;
  status?: string;
  anonymization_attested_by?: string;
}

export interface EvalCasePatchBody {
  input?: Record<string, unknown>;
  expected?: Record<string, unknown>;
  tags?: string[];
  weight?: number;
  anonymization_attested_by?: string;
}

export interface CreateEvalScorerBody {
  scorer_key: string;
  version: number;
  kind: string;
  gate_eligible?: boolean;
  config_schema?: Record<string, unknown>;
  applicable_expected_kinds?: string[];
  image_ref?: string;
  judge_prompt_ref?: string;
  judge_prompt_ver?: string;
  judge_agreement?: number;
  status?: string;
}

export interface EvalScorerPatchBody {
  gate_eligible?: boolean;
  config_schema?: Record<string, unknown>;
  applicable_expected_kinds?: string[];
  image_ref?: string;
  judge_prompt_ref?: string;
  judge_prompt_ver?: string;
  judge_agreement?: number;
  status?: string;
}

export interface CreateEvalCanaryBody {
  agent_key: string;
  candidate_version: string;
  baseline_version: string;
  mode?: string;
  sample_spec?: Record<string, unknown>;
  thresholds?: Record<string, unknown>;
  must_scorers?: string[];
}

export class EvalClient {
  constructor(private readonly http: ServiceClient) {}

  // ---- suites ---------------------------------------------------------------
  async createSuite(body: CreateEvalSuiteBody): Promise<EvalSuiteDTO> {
    const r = await this.http.post<{ data: EvalSuiteDTO }>("/api/v1/suites", { body });
    return unwrap<EvalSuiteDTO>(r);
  }

  async suite(suiteId: string, version?: number): Promise<EvalSuiteDTO> {
    const r = await this.http.get<{ data: EvalSuiteDTO }>(
      `/api/v1/suites/${encodeURIComponent(suiteId)}`,
      { query: { version } },
    );
    return unwrap<EvalSuiteDTO>(r);
  }

  async updateSuite(suiteId: string, patch: EvalSuitePatchBody, version?: number): Promise<EvalSuiteDTO> {
    const r = await this.http.patch<{ data: EvalSuiteDTO }>(
      `/api/v1/suites/${encodeURIComponent(suiteId)}`,
      { body: patch, query: { version } },
    );
    return unwrap<EvalSuiteDTO>(r);
  }

  // ---- runs -------------------------------------------------------------
  async createRun(body: CreateEvalRunBody): Promise<EvalRunDTO> {
    const r = await this.http.post<{ data: EvalRunDTO }>("/api/v1/runs", { body });
    return unwrap<EvalRunDTO>(r);
  }

  async runs(p: { agentKey?: string; trigger?: string; limit: number; cursor?: string }): Promise<Page<EvalRunDTO>> {
    const raw = await this.http.get<{ data: EvalRunDTO[]; next_cursor?: string | null; has_more?: boolean }>(
      "/api/v1/runs",
      { query: { agent_key: p.agentKey, trigger: p.trigger, limit: p.limit, cursor: p.cursor } },
    );
    return adaptPage(raw);
  }

  async run(id: string): Promise<EvalRunDTO> {
    const r = await this.http.get<{ data: EvalRunDTO }>(`/api/v1/runs/${encodeURIComponent(id)}`);
    return unwrap<EvalRunDTO>(r);
  }

  async runCases(runId: string): Promise<EvalCaseResultDTO[]> {
    const r = await this.http.get<{ data: EvalCaseResultDTO[] }>(
      `/api/v1/runs/${encodeURIComponent(runId)}/cases`,
    );
    return r.data ?? [];
  }

  async cancelRun(id: string): Promise<EvalRunDTO> {
    const r = await this.http.post<{ data: EvalRunDTO }>(`/api/v1/runs/${encodeURIComponent(id)}/cancel`);
    return unwrap<EvalRunDTO>(r);
  }

  // ---- datasets ---------------------------------------------------------
  async createDataset(body: CreateEvalDatasetBody): Promise<EvalDatasetDTO> {
    const r = await this.http.post<{ data: EvalDatasetDTO }>("/api/v1/datasets", { body });
    return unwrap<EvalDatasetDTO>(r);
  }

  async datasets(p: { agentKey?: string; limit: number; cursor?: string }): Promise<Page<EvalDatasetDTO>> {
    const raw = await this.http.get<{ data: EvalDatasetDTO[]; next_cursor?: string | null; has_more?: boolean }>(
      "/api/v1/datasets",
      { query: { agent_key: p.agentKey, limit: p.limit, cursor: p.cursor } },
    );
    return adaptPage(raw);
  }

  async dataset(datasetKey: string, version: number): Promise<EvalDatasetDTO> {
    const r = await this.http.get<{ data: EvalDatasetDTO }>(
      `/api/v1/datasets/${encodeURIComponent(datasetKey)}/versions/${version}`,
    );
    return unwrap<EvalDatasetDTO>(r);
  }

  async freezeDataset(datasetKey: string, version: number): Promise<EvalDatasetDTO> {
    const r = await this.http.post<{ data: EvalDatasetDTO }>(
      `/api/v1/datasets/${encodeURIComponent(datasetKey)}/versions/${version}/freeze`,
    );
    return unwrap<EvalDatasetDTO>(r);
  }

  // ---- cases (curation queue) --------------------------------------------
  async createCase(body: CreateEvalCaseBody): Promise<EvalCaseDTO> {
    const r = await this.http.post<{ data: EvalCaseDTO }>("/api/v1/cases", { body });
    return unwrap<EvalCaseDTO>(r);
  }

  async cases(p: {
    status?: string;
    datasetKey?: string;
    datasetVersion?: number;
    source?: string;
    limit: number;
    cursor?: string;
  }): Promise<Page<EvalCaseDTO>> {
    const raw = await this.http.get<{ data: EvalCaseDTO[]; next_cursor?: string | null; has_more?: boolean }>(
      "/api/v1/cases",
      {
        query: {
          "filter[status]": p.status,
          "filter[dataset_key]": p.datasetKey,
          "filter[dataset_version]": p.datasetVersion,
          "filter[source]": p.source,
          limit: p.limit,
          cursor: p.cursor,
        },
      },
    );
    return adaptPage(raw);
  }

  async case_(id: string): Promise<EvalCaseDTO> {
    const r = await this.http.get<{ data: EvalCaseDTO }>(`/api/v1/cases/${encodeURIComponent(id)}`);
    return unwrap<EvalCaseDTO>(r);
  }

  async promoteCase(id: string): Promise<EvalCaseDTO> {
    const r = await this.http.post<{ data: EvalCaseDTO }>(`/api/v1/cases/${encodeURIComponent(id)}/promote`);
    return unwrap<EvalCaseDTO>(r);
  }

  async attestCase(id: string, attestedBy: string): Promise<EvalCaseDTO> {
    const r = await this.http.post<{ data: EvalCaseDTO }>(`/api/v1/cases/${encodeURIComponent(id)}/attest`, {
      body: { attested_by: attestedBy },
    });
    return unwrap<EvalCaseDTO>(r);
  }

  async rejectCase(id: string): Promise<EvalCaseDTO> {
    const r = await this.http.post<{ data: EvalCaseDTO }>(`/api/v1/cases/${encodeURIComponent(id)}/reject`);
    return unwrap<EvalCaseDTO>(r);
  }

  async retireCase(id: string): Promise<EvalCaseDTO> {
    const r = await this.http.post<{ data: EvalCaseDTO }>(`/api/v1/cases/${encodeURIComponent(id)}/retire`);
    return unwrap<EvalCaseDTO>(r);
  }

  async patchCase(id: string, patch: EvalCasePatchBody): Promise<EvalCaseDTO> {
    const r = await this.http.patch<{ data: EvalCaseDTO }>(`/api/v1/cases/${encodeURIComponent(id)}`, {
      body: patch,
    });
    return unwrap<EvalCaseDTO>(r);
  }

  // ---- scorers ------------------------------------------------------------
  async createScorer(body: CreateEvalScorerBody): Promise<EvalScorerDTO> {
    const r = await this.http.post<{ data: EvalScorerDTO }>("/api/v1/scorers", { body });
    return unwrap<EvalScorerDTO>(r);
  }

  async updateScorer(scorerKey: string, patch: EvalScorerPatchBody, version?: number): Promise<EvalScorerDTO> {
    const r = await this.http.patch<{ data: EvalScorerDTO }>(
      `/api/v1/scorers/${encodeURIComponent(scorerKey)}`,
      { body: patch, query: { version } },
    );
    return unwrap<EvalScorerDTO>(r);
  }

  async activateScorer(scorerKey: string, version: number): Promise<EvalScorerDTO> {
    const r = await this.http.post<{ data: EvalScorerDTO }>(
      `/api/v1/scorers/${encodeURIComponent(scorerKey)}/versions/${version}/activate`,
    );
    return unwrap<EvalScorerDTO>(r);
  }

  async scorers(p: { limit: number; cursor?: string }): Promise<Page<EvalScorerDTO>> {
    const raw = await this.http.get<{ data: EvalScorerDTO[]; next_cursor?: string | null; has_more?: boolean }>(
      "/api/v1/scorers",
      { query: { limit: p.limit, cursor: p.cursor } },
    );
    return adaptPage(raw);
  }

  // ---- gates (promotion-blocking verdicts) --------------------------------
  async gate(gateRunId: string): Promise<EvalGateResultDTO> {
    const r = await this.http.get<{ data: EvalGateResultDTO }>(`/api/v1/gates/${encodeURIComponent(gateRunId)}`);
    return unwrap<EvalGateResultDTO>(r);
  }

  async gatesByDigest(agentKey: string, contentDigest: string): Promise<EvalGateResultDTO[]> {
    const r = await this.http.get<{ data: EvalGateResultDTO[] }>("/api/v1/gates", {
      query: { agent_key: agentKey, content_digest: contentDigest },
    });
    return r.data ?? [];
  }

  // ---- canaries (A/B rollout comparisons) ---------------------------------
  async createCanary(body: CreateEvalCanaryBody): Promise<EvalCanaryDTO> {
    const r = await this.http.post<{ data: EvalCanaryDTO }>("/api/v1/canaries", { body });
    return unwrap<EvalCanaryDTO>(r);
  }

  async canary(comparisonId: string): Promise<EvalCanaryDTO> {
    const r = await this.http.get<{ data: EvalCanaryDTO }>(
      `/api/v1/canaries/${encodeURIComponent(comparisonId)}`,
    );
    return unwrap<EvalCanaryDTO>(r);
  }

  async ingestCanarySamples(
    comparisonId: string,
    pairedScores: Record<string, [number, number][]>,
  ): Promise<EvalCanaryDTO> {
    const r = await this.http.post<{ data: EvalCanaryDTO }>(
      `/api/v1/canaries/${encodeURIComponent(comparisonId)}/samples`,
      { body: { paired_scores: pairedScores } },
    );
    return unwrap<EvalCanaryDTO>(r);
  }

  async stopCanary(comparisonId: string): Promise<EvalCanaryDTO> {
    const r = await this.http.post<{ data: EvalCanaryDTO }>(
      `/api/v1/canaries/${encodeURIComponent(comparisonId)}/stop`,
    );
    return unwrap<EvalCanaryDTO>(r);
  }

  // ---- trends + SLOs (score history / operational health) ----------------
  async trends(agentKey: string, scorer?: string, window?: string): Promise<EvalTrendPointDTO[]> {
    const r = await this.http.get<{ data: EvalTrendPointDTO[] }>("/api/v1/trends", {
      query: { agent_key: agentKey, scorer, window },
    });
    return r.data ?? [];
  }

  async slos(agentKey: string, window?: string): Promise<EvalSloRowDTO[]> {
    const r = await this.http.get<{ data: EvalSloRowDTO[] }>("/api/v1/slos", {
      query: { agent_key: agentKey, window },
    });
    return r.data ?? [];
  }

  async setSloTargets(agentKey: string, agentVersion: string | undefined, targets: Record<string, unknown>): Promise<void> {
    await this.http.post("/api/v1/slos/targets", {
      body: { agent_key: agentKey, agent_version: agentVersion, targets },
    });
  }
}
