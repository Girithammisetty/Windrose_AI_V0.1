import { describe, it, expect, beforeEach } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { dispatchEvent, casePatcher, proposalPatcher, runPatcher } from "./patchers";
import type { Case, Connection, Proposal, Run } from "@/lib/graphql/types";

function conn<T>(nodes: T[]): Connection<T> {
  return { nodes, pageInfo: { nextCursor: null, hasMore: false } };
}
// Lists are TanStack infinite queries: { pages: Connection[], pageParams }.
function infinite<T>(nodes: T[]) {
  return { pages: [conn(nodes)], pageParams: [null] };
}

describe("EventBridge patchers (UI-FR-044) — pure cache mutations, no refetch", () => {
  let client: QueryClient;
  beforeEach(() => {
    client = new QueryClient();
  });

  it("casePatcher patches a case row in a list cache in place (AC-1)", () => {
    const c: Case = {
      id: "c1", urn: "u", caseNumber: 1, title: "t", status: "IN_PROGRESS", severity: "LOW",
      dueDate: null, createdAt: null, assignee: null, sourceDataset: null, proposals: [],
    };
    client.setQueryData(["cases", "list", {}], infinite([c]));
    dispatchEvent(client, { topic: "case.status", data: { id: "c1", status: "RESOLVED" } });
    const after = client.getQueryData<{ pages: Connection<Case>[] }>(["cases", "list", {}]);
    expect(after?.pages[0].nodes[0].status).toBe("RESOLVED");
  });

  it("proposalPatcher drops a decided proposal from the pending inbox and decrements", () => {
    const p: Proposal = {
      id: "p1", urn: "u", agentKey: "a", tool: "assign", argsDiff: {}, rationale: null,
      affectedUrns: [], predictedEffect: null, status: "PENDING", decision: null, createdAt: null,
    };
    client.setQueryData(["agentic", "proposals", { status: "PENDING" }], infinite([p]));
    dispatchEvent(client, { topic: "ai.proposal.decided", data: { id: "p1", status: "APPROVED" } });
    const after = client.getQueryData<{ pages: Connection<Proposal>[] }>([
      "agentic", "proposals", { status: "PENDING" },
    ]);
    expect(after?.pages[0].nodes).toHaveLength(0);
  });

  it("proposalPatcher prepends a newly created proposal", () => {
    client.setQueryData(["agentic", "proposals", { status: "PENDING" }], infinite<Proposal>([]));
    const np: Proposal = {
      id: "p2", urn: "u", agentKey: "a", tool: "tag", argsDiff: {}, rationale: null,
      affectedUrns: [], predictedEffect: null, status: "PENDING", decision: null, createdAt: null,
    };
    dispatchEvent(client, { topic: "ai.proposal.created", data: { proposal: np } });
    const after = client.getQueryData<{ pages: Connection<Proposal>[] }>([
      "agentic", "proposals", { status: "PENDING" },
    ]);
    expect(after?.pages[0].nodes[0].id).toBe("p2");
  });

  it("runPatcher updates a run detail status without refetch", () => {
    const r: Run = { id: "r1", urn: "u", name: "run", status: "RUNNING", metrics: {}, params: {}, model: null };
    client.setQueryData(["ml", "run", "r1"], { run: r });
    dispatchEvent(client, { topic: "run.status", data: { id: "r1", status: "SUCCEEDED" } });
    expect(client.getQueryData<{ run: Run }>(["ml", "run", "r1"])?.run.status).toBe("SUCCEEDED");
  });

  it("ingestionPatcher patches an ingestion status in the list and detail", () => {
    client.setQueryData(["data", "ingestions", {}], infinite([{ id: "ig1", status: "running" }]));
    client.setQueryData(["data", "ingestion", "ig1"], { id: "ig1", status: "running" });
    dispatchEvent(client, { topic: "ingestion.status", data: { id: "ig1", status: "completed" } });
    expect(
      client.getQueryData<{ pages: { nodes: { status: string }[] }[] }>(["data", "ingestions", {}])
        ?.pages[0].nodes[0].status,
    ).toBe("completed");
    expect(client.getQueryData<{ status: string }>(["data", "ingestion", "ig1"])?.status).toBe("completed");
  });

  it("inferencePatcher patches a batch-scoring job status (wrapped detail)", () => {
    client.setQueryData(["ml", "inferenceJobs", {}], infinite([{ id: "j1", status: "running" }]));
    client.setQueryData(["ml", "inferenceJob", "j1"], { inferenceJob: { id: "j1", status: "running" } });
    dispatchEvent(client, { topic: "inference.status", data: { id: "j1", status: "succeeded" } });
    expect(
      client.getQueryData<{ inferenceJob: { status: string } }>(["ml", "inferenceJob", "j1"])
        ?.inferenceJob.status,
    ).toBe("succeeded");
  });

  it("pipelineRunPatcher patches a pipeline run status in the runs list", () => {
    client.setQueryData(["pipelines", "runs", {}], infinite([{ id: "pr1", status: "queued" }]));
    dispatchEvent(client, {
      topic: "pipeline.run.status_changed",
      data: { run_id: "pr1", status: "running" },
    });
    expect(
      client.getQueryData<{ pages: { nodes: { status: string }[] }[] }>(["pipelines", "runs", {}])
        ?.pages[0].nodes[0].status,
    ).toBe("running");
  });

  it("pipeline.run.* does NOT collide with the run. patcher", () => {
    // "pipeline.run.status_changed" must not match the model-training runPatcher
    // prefix "run." — only the pipelineRunPatcher.
    const handled = dispatchEvent(
      client,
      { topic: "pipeline.run.status_changed", data: { run_id: "x", status: "running" } },
      [runPatcher],
    );
    expect(handled).toBe(0);
  });

  it("dispatch routes only to matching patchers", () => {
    const handled = dispatchEvent(client, { topic: "case.status", data: { id: "x" } }, [casePatcher, runPatcher, proposalPatcher]);
    expect(handled).toBe(1);
  });
});
