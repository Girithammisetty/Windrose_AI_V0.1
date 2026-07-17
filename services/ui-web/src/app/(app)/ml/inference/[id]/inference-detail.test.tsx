import { describe, it, expect, vi, beforeEach } from "vitest";
import { Suspense } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** Route graphqlRequest by operation name to a per-test handler
 * (same conventions as data/pipelines/runs/runs.test.tsx). */
let handler: (doc: string, vars: any) => any = () => ({});
const requests: { doc: string; vars: any }[] = [];
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: (doc: string, vars: any) => {
      requests.push({ doc, vars });
      return Promise.resolve(handler(doc, vars));
    },
  };
});
// The realtime hub is out of scope here.
vi.mock("@/lib/realtime/useHubTopics", () => ({ useHubTopics: () => {} }));
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import InferenceJobDetailPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

function jobResult(overrides: Record<string, unknown> = {}) {
  return {
    inferenceJob: {
      id: "job-1", urn: "wr:t-42:inference:job/job-1", name: "score claims", description: null,
      status: "running",
      model: { urn: "wr:t:experiment:model_version/m-1@2", name: "claims", version: 2, stageAtSubmit: "production" },
      inputDataset: { urn: "wr:t:dataset:dataset/ds-1", version: 3 },
      outputDataset: null, rowCount: null, error: null, pipelineRunUrn: null,
      scheduleId: null, retriedFromJobId: null,
      createdAt: "2026-07-12T00:00:00Z", submittedAt: null, startedAt: null, finishedAt: null,
      ...overrides,
    },
  };
}

let job = jobResult();

beforeEach(() => {
  requests.length = 0;
  push.mockClear();
  job = jobResult();
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query InferenceJobDetail")) return job;
    return {};
  };
});

// Pre-instrumented promise so React's `use()` reads it synchronously.
const params = Promise.resolve({ id: "job-1" }) as Promise<{ id: string }> & {
  status?: string;
  value?: { id: string };
};
params.status = "fulfilled";
params.value = { id: "job-1" };

function renderPage() {
  return renderWithProviders(
    <Suspense fallback={null}>
      <InferenceJobDetailPage params={params} />
    </Suspense>,
  );
}

describe("Inference job detail — lifecycle buttons derive from the REAL state machine", () => {
  // CANCELLABLE={queued,submitted,running}; TERMINAL_FAILURE={rejected,failed,
  // cancelled}; deletable=TERMINAL (inference-service domain/enums.py).
  it("running: Cancel only (no Retry, no Delete)", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Cancel job" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("failed: Retry + Delete (no Cancel)", async () => {
    job = jobResult({ status: "failed", error: "boom" });
    renderPage();
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
  });

  it("succeeded: Delete only (terminal but not a failure — no Retry)", async () => {
    job = jobResult({ status: "succeeded", rowCount: 42 });
    renderPage();
    expect(await screen.findByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
  });

  it("queued: Cancel present; validating: nothing (not yet cancellable)", async () => {
    job = jobResult({ status: "queued" });
    const first = renderPage();
    expect(await first.findByRole("button", { name: "Cancel job" })).toBeInTheDocument();
    first.unmount();

    job = jobResult({ status: "validating" });
    renderPage();
    await screen.findByText("score claims");
    expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("cancel fires the real mutation with the job id after the confirm dialog", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query InferenceJobDetail")) return job;
      if (doc.includes("mutation CancelInferenceJob")) {
        return { cancelInferenceJob: jobResult({ status: "cancelling" }).inferenceJob };
      }
      return {};
    };
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Cancel job" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel job" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CancelInferenceJob"));
      expect(call?.vars).toMatchObject({ id: "job-1" });
    });
  });

  it("retry navigates to the NEW job id returned by the mutation", async () => {
    job = jobResult({ status: "failed" });
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query InferenceJobDetail")) return job;
      if (doc.includes("mutation RetryInferenceJob")) {
        return { retryInferenceJob: jobResult({ id: "job-2", status: "submitted", retriedFromJobId: "job-1" }).inferenceJob };
      }
      return {};
    };
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation RetryInferenceJob"));
      expect(call?.vars).toMatchObject({ id: "job-1" });
      expect(push).toHaveBeenCalledWith("/ml/inference/job-2");
    });
  });

  it("shows a link back to the source job when retriedFromJobId is set", async () => {
    job = jobResult({ status: "running", retriedFromJobId: "job-0" });
    renderPage();
    const link = await screen.findByRole("link", { name: "job-0" });
    expect(link).toHaveAttribute("href", "/ml/inference/job-0");
  });
});
