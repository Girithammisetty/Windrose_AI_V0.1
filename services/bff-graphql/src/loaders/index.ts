/**
 * Per-request dataloaders (BFF-FR-030/031).
 *
 * One loader per (downstream service, resource) pair, each backed by that
 * service's batch-get endpoint (`?filter[id]=a,b,c`). Every nested/list
 * resolver hydrates through a loader so a page of N cases costs one
 * `GET /users?filter[id]=…` — not N. Loaders are created per request (no
 * cross-request cache in v1 — tenant safety first) and preserve per-item error
 * isolation: one missing id yields `null` for that key only (BR-5).
 */
import DataLoader from "dataloader";
import type { Clients } from "../clients/index.js";
import { DownstreamError } from "../errors/errors.js";
import type { UserDTO } from "../clients/identity.js";
import type { DatasetDTO, ProfileDTO } from "../clients/dataset.js";
import type { ProposalDTO } from "../clients/agent.js";
import { budgetScopeString, type BudgetStateDTO } from "../clients/usage.js";
import type { RunDTO, ModelDTO } from "../clients/experiment.js";
import type {
  EvalCaseResultDTO,
  EvalGateResultDTO,
  EvalSuiteDTO,
} from "../clients/eval.js";

const MAX_BATCH = 100; // BFF-FR-031: <=100 ids per downstream call.
// Bounded per-id fallback when a downstream ignores filter[id] (transition aid).
const MAX_FALLBACK_GETS = 10;

/** Index a fetched list by id, returning one entry per requested key (null if
 * absent). Rows whose ids were NOT requested are dropped by construction — a
 * downstream that ignores filter[id] and returns arbitrary rows can never
 * hydrate the WRONG entity, only fail to hydrate (null). */
function byId<T extends { id?: string }>(rows: T[], ids: readonly string[]): (T | null)[] {
  const map = new Map<string, T>();
  for (const r of rows) if (r.id) map.set(r.id, r);
  return ids.map((id) => map.get(id) ?? null);
}

export interface Loaders {
  datasetById: DataLoader<string, DatasetDTO | null>;
  profileByDatasetId: DataLoader<string, ProfileDTO | null>;
  userById: DataLoader<string, UserDTO | null>;
  proposalsByResourceUrn: DataLoader<string, ProposalDTO[]>;
  budgetStateByScope: DataLoader<string, BudgetStateDTO | null>;
  /** Runs grouped by experiment id — one batched /runs call for a page of experiments. */
  runsByExperimentId: DataLoader<string, RunDTO[]>;
  /** Registered models by id — one batched /models call across a page of runs. */
  modelById: DataLoader<string, ModelDTO | null>;
  /** Eval suite by "suiteId@version" — dedups the per-run suite fetch so a page
   * of runs pinned to the same suite costs one call, not one per run. */
  evalSuiteByKey: DataLoader<string, EvalSuiteDTO | null>;
  /** Eval gates by "agentKey::contentDigest" — dedups the per-run gate lookup
   * across runs that share an agent+digest. */
  evalGatesByKey: DataLoader<string, EvalGateResultDTO[]>;
  /** Eval case results by run id — dedups repeat requests for the same run. */
  evalCasesByRunId: DataLoader<string, EvalCaseResultDTO[]>;
}

export function buildLoaders(clients: Clients): Loaders {
  return {
    // dataset-service does not honor filter[id] yet: the filtered list returns
    // arbitrary rows (dropped by byId, never mis-hydrated). Any still-missing
    // ids fall back to bounded per-id GETs so Case.sourceDataset works
    // regardless of the downstream contract state.
    datasetById: new DataLoader<string, DatasetDTO | null>(
      async (ids) => {
        let listed: DatasetDTO[] = [];
        try {
          listed = await clients.dataset.datasetsByIds([...ids]);
        } catch {
          listed = []; // fall through to per-id GETs
        }
        const map = new Map<string, DatasetDTO>();
        for (const r of listed) if (r.id) map.set(r.id, r);
        const missing = [...new Set(ids)].filter((id) => !map.has(id)).slice(0, MAX_FALLBACK_GETS);
        const fetched = await Promise.all(
          missing.map((id) => clients.dataset.dataset(id).catch(() => null as DatasetDTO | null)),
        );
        for (const d of fetched) if (d?.id) map.set(d.id, d);
        return ids.map((id) => map.get(id) ?? null);
      },
      { maxBatchSize: MAX_BATCH },
    ),

    // Profiles are keyed by dataset id; the downstream lacks a batch endpoint
    // so we fetch per-key but still dedupe within the request via the loader.
    // A 404/409 means "not profiled yet" (null); a 5xx/transport failure is an
    // OUTAGE and must surface as a field error (AsyncBoundary error contract),
    // not masquerade as "no profile".
    profileByDatasetId: new DataLoader<string, ProfileDTO | null>(
      async (datasetIds) =>
        Promise.all(
          datasetIds.map((id) =>
            clients.dataset.profile(id).catch((e): ProfileDTO | null | Error => {
              // Returning the Error rejects ONLY this key (dataloader contract),
              // so one outage doesn't poison the sibling datasets in the batch.
              if (e instanceof DownstreamError && (e.httpStatus >= 500 || e.httpStatus === 0)) {
                return e;
              }
              return null;
            }),
          ),
        ),
      { maxBatchSize: MAX_BATCH },
    ),

    userById: new DataLoader<string, UserDTO | null>(
      async (ids) => byId(await clients.identity.usersByIds([...ids]), ids),
      { maxBatchSize: MAX_BATCH },
    ),

    // One filter[resource_urn] IN-call; bucket each row on the resource_urn the
    // downstream RETURNS. Rows without one yet (contract in transition) fall
    // back to affected_urns membership so a case's proposals still attach.
    proposalsByResourceUrn: new DataLoader<string, ProposalDTO[]>(
      async (urns) => {
        const rows = await clients.agent.proposalsByResourceUrns([...urns]);
        const map = new Map<string, ProposalDTO[]>();
        const add = (key: string, p: ProposalDTO) => {
          if (!map.has(key)) map.set(key, []);
          map.get(key)!.push(p);
        };
        for (const p of rows) {
          if (p.resource_urn) {
            add(p.resource_urn, p);
          } else {
            // No resource_urn on the row: attribute it to every REQUESTED urn
            // it names in affected_urns (never invent an association).
            for (const u of p.affected_urns ?? []) if (urns.includes(u)) add(u, p);
          }
        }
        return urns.map((u) => map.get(u) ?? []);
      },
      { maxBatchSize: MAX_BATCH },
    ),

    budgetStateByScope: new DataLoader<string, BudgetStateDTO | null>(
      async (scopes) => {
        const rows = await clients.usage.budgetStates();
        const map = new Map<string, BudgetStateDTO>();
        for (const s of rows) {
          // scope may arrive as a string or the nested dimension object.
          const key = budgetScopeString(s.scope);
          if (key) map.set(key, s);
        }
        return scopes.map((s) => map.get(s) ?? null);
      },
      { maxBatchSize: MAX_BATCH },
    ),

    runsByExperimentId: new DataLoader<string, RunDTO[]>(
      async (experimentIds) => {
        const rows = await clients.experiment.runsByExperimentIds([...experimentIds]);
        const map = new Map<string, RunDTO[]>();
        for (const r of rows) {
          const key = r.experiment_id ?? "";
          if (!map.has(key)) map.set(key, []);
          map.get(key)!.push(r);
        }
        return experimentIds.map((id) => map.get(id) ?? []);
      },
      { maxBatchSize: MAX_BATCH },
    ),

    modelById: new DataLoader<string, ModelDTO | null>(
      async (ids) => byId(await clients.experiment.modelsByIds([...ids]), ids),
      { maxBatchSize: MAX_BATCH },
    ),

    // eval-service exposes only get-by-key endpoints (no batch), so these
    // loaders' value is per-request DEDUP + cache: a page of runs sharing a
    // suite/agent+digest collapses to one call instead of one per run. Keys are
    // stringified composites; each key maps to its own client call, but repeats
    // within the request are served from the loader cache. Per-key error
    // isolation: a failing key yields an empty/null value for that key only.
    evalSuiteByKey: new DataLoader<string, EvalSuiteDTO | null>(async (keys) =>
      Promise.all(
        keys.map((k) => {
          const [suiteId = "", v = ""] = k.split("@");
          const version = v === "" ? undefined : Number(v);
          return clients.eval.suite(suiteId, version).catch(() => null);
        }),
      ),
    ),
    evalGatesByKey: new DataLoader<string, EvalGateResultDTO[]>(async (keys) =>
      Promise.all(
        keys.map((k) => {
          const [agentKey = "", digest = ""] = k.split("::");
          return clients.eval.gatesByDigest(agentKey, digest).catch(() => []);
        }),
      ),
    ),
    evalCasesByRunId: new DataLoader<string, EvalCaseResultDTO[]>(async (runIds) =>
      Promise.all(runIds.map((id) => clients.eval.runCases(id).catch(() => []))),
    ),
  };
}
