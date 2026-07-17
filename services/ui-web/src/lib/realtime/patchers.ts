/**
 * EventBridge (UI-FR-044): the ONE place realtime-hub topics map to TanStack
 * Query cache mutations. Screens never touch raw SSE events — they register a
 * patcher. Every `apply` is a PURE function of (client, event) with no side
 * effects beyond the passed QueryClient, so each is unit-tested in isolation.
 */
import type { QueryClient } from "@tanstack/react-query";
import type {
  Case,
  Connection,
  Ingestion,
  InferenceJob,
  PipelineRun,
  Proposal,
  Run,
} from "@/lib/graphql/types";

export interface HubEvent {
  /** Topic scheme, e.g. "case.status", "ai.proposal.created", "run.status". */
  topic: string;
  /** JSON payload from the hub frame. */
  data: any;
}

export interface Patcher {
  /** Topic prefix this patcher handles (matched by startsWith). */
  match: string;
  apply: (client: QueryClient, event: HubEvent) => void;
}

/** Max nodes the live feed keeps on the first page of an infinite list, so a
 * high-churn realtime stream can't grow it without bound. */
const FIRST_PAGE_LIVE_CAP = 100;

/** Cursor lists are TanStack infinite queries: { pages: Connection[] }. */
interface InfiniteCache<T> {
  pages: Connection<T>[];
  pageParams: unknown[];
}

function mapInfiniteNodes<T>(
  client: QueryClient,
  prefix: readonly unknown[],
  fn: (nodes: T[]) => T[],
) {
  client.setQueriesData<InfiniteCache<T>>({ queryKey: prefix }, (old) =>
    old?.pages
      ? { ...old, pages: old.pages.map((pg) => ({ ...pg, nodes: fn(pg.nodes) })) }
      : old,
  );
}

/** case.status / case.assigned → patch the row in every list + the detail. */
export const casePatcher: Patcher = {
  match: "case.",
  apply(client, { data }) {
    const id = data?.id ?? data?.case_id;
    if (!id) return;
    mapInfiniteNodes<Case>(client, ["cases", "list"], (nodes) =>
      nodes.map((c) =>
        c.id === id ? { ...c, status: data.status ?? c.status, severity: data.severity ?? c.severity } : c,
      ),
    );
    client.setQueryData<{ case: Case | null }>(["cases", "case", id], (old) =>
      old?.case ? { case: { ...old.case, status: data.status ?? old.case.status } } : old,
    );
  },
};

/** run.status / experiment.run → patch a run's live status without refetch. */
export const runPatcher: Patcher = {
  match: "run.",
  apply(client, { data }) {
    const id = data?.id ?? data?.run_id;
    if (!id) return;
    client.setQueryData<{ run: Run | null }>(["ml", "run", id], (old) =>
      old?.run ? { run: { ...old.run, status: data.status ?? old.run.status } } : old,
    );
    // Patch runs nested inside an experiment detail.
    client.setQueriesData<any>({ queryKey: ["ml", "experiment"] }, (old: any) => {
      if (!old?.experiment?.runs?.nodes) return old;
      return {
        experiment: {
          ...old.experiment,
          runs: {
            ...old.experiment.runs,
            nodes: old.experiment.runs.nodes.map((r: Run) =>
              r.id === id ? { ...r, status: data.status ?? r.status } : r,
            ),
          },
        },
      };
    });
  },
};

/**
 * ai.proposal.created / .decided → keep the inbox list and badge live. A created
 * proposal invalidates the pending list (append via refetch-on-focus is wrong;
 * we prepend if present); a decided one drops it from the pending list.
 */
export const proposalPatcher: Patcher = {
  match: "ai.proposal.",
  apply(client, { topic, data }) {
    const id = data?.id;
    if (topic.endsWith("decided") || topic.endsWith("expired")) {
      mapInfiniteNodes<Proposal>(client, ["agentic", "proposals"], (nodes) =>
        nodes.filter((p) => p.id !== id),
      );
      if (id) {
        client.setQueryData<{ proposal: Proposal | null }>(["agentic", "proposal", id], (old) =>
          old?.proposal ? { proposal: { ...old.proposal, status: data.status ?? old.proposal.status } } : old,
        );
      }
    } else if (topic.endsWith("created") && data?.proposal) {
      client.setQueriesData<InfiniteCache<Proposal>>({ queryKey: ["agentic", "proposals"] }, (old) => {
        if (!old?.pages?.length) return old;
        if (old.pages.some((pg) => pg.nodes.some((p) => p.id === data.proposal.id))) return old;
        const [first, ...rest] = old.pages;
        // Cap the live-prepended first page: under a high create / low decide
        // workload the realtime feed would otherwise grow the first page's node
        // array without bound (and re-map it O(n) on every event). The tail is
        // still reachable via paginated refetch.
        const nodes = [data.proposal as Proposal, ...first.nodes].slice(0, FIRST_PAGE_LIVE_CAP);
        return { ...old, pages: [{ ...first, nodes }, ...rest] };
      });
    }
  },
};

/** usage.events.v1 / budget.threshold → refresh the cost panel in place. */
export const usagePatcher: Patcher = {
  match: "usage.",
  apply(client, { data }) {
    client.setQueriesData<any>({ queryKey: ["usage", "costPanel"] }, (old: any) => {
      if (!old?.workspaceCostPanel) return old;
      const scope = data?.scope;
      return {
        workspaceCostPanel: {
          ...old.workspaceCostPanel,
          budgetStates: old.workspaceCostPanel.budgetStates.map((b: any) =>
            b.scope === scope
              ? { ...b, consumed: data.consumed ?? b.consumed, lastThreshold: data.threshold ?? b.lastThreshold, exhaustedAt: data.exhaustedAt ?? b.exhaustedAt }
              : b,
          ),
        },
      };
    });
  },
};

/** ingestion.status / ingestion.progress → patch an ingestion's live status in
 * the list + detail without a refetch (the poll fallback still covers a missed
 * event). */
export const ingestionPatcher: Patcher = {
  match: "ingestion.",
  apply(client, { data }) {
    const id = data?.id ?? data?.ingestion_id;
    if (!id) return;
    mapInfiniteNodes<Ingestion>(client, ["data", "ingestions"], (nodes) =>
      nodes.map((n) => (n.id === id ? { ...n, status: data.status ?? n.status } : n)),
    );
    // useIngestion(id) caches the Ingestion directly (unwrapped).
    client.setQueryData<Ingestion>(["data", "ingestion", id], (old) =>
      old ? { ...old, status: data.status ?? old.status } : old,
    );
  },
};

/** inference.status → patch a batch-scoring job's live status. */
export const inferencePatcher: Patcher = {
  match: "inference.",
  apply(client, { data }) {
    const id = data?.id ?? data?.job_id;
    if (!id) return;
    mapInfiniteNodes<InferenceJob>(client, ["ml", "inferenceJobs"], (nodes) =>
      nodes.map((n) => (n.id === id ? { ...n, status: data.status ?? n.status } : n)),
    );
    // useInferenceJob(id) caches the wrapped { inferenceJob } result.
    client.setQueryData<{ inferenceJob: InferenceJob | null }>(["ml", "inferenceJob", id], (old) =>
      old?.inferenceJob
        ? { inferenceJob: { ...old.inferenceJob, status: data.status ?? old.inferenceJob.status } }
        : old,
    );
  },
};

/** pipeline.run.status_changed → patch a pipeline run's live status in the runs
 * list. The realtime-hub already routes `pipeline.run.*`; the runs page also
 * polls as a belt-and-suspenders fallback. */
export const pipelineRunPatcher: Patcher = {
  match: "pipeline.run.",
  apply(client, { data }) {
    const id = data?.run_id ?? data?.id;
    if (!id) return;
    mapInfiniteNodes<PipelineRun>(client, ["pipelines", "runs"], (nodes) =>
      nodes.map((n) => (n.id === id ? { ...n, status: data.status ?? n.status } : n)),
    );
  },
};

export const REGISTRY: Patcher[] = [
  casePatcher,
  runPatcher,
  proposalPatcher,
  usagePatcher,
  ingestionPatcher,
  inferencePatcher,
  pipelineRunPatcher,
];

/** Dispatch a hub event to every matching patcher (the EventBridge entrypoint). */
export function dispatchEvent(client: QueryClient, event: HubEvent, registry: Patcher[] = REGISTRY): number {
  let handled = 0;
  for (const p of registry) {
    if (event.topic.startsWith(p.match)) {
      p.apply(client, event);
      handled++;
    }
  }
  return handled;
}
