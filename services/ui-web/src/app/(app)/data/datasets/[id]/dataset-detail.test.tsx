import { describe, it, expect, vi, beforeEach } from "vitest";
import { Suspense } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** Route graphqlRequest by operation name to a per-test handler. */
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

import DatasetDetailPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Dataset(")) {
      return { dataset: { id: "ds-1", urn: "wr:t:dataset:dataset/ds-1", name: "claims", description: null,
        status: "ready", tags: [], rowCount: 100, createdAt: "2026-07-01T00:00:00Z", archived: false,
        archivedAt: null, profile: null } };
    }
    if (doc.includes("query DatasetConsumers")) {
      return { datasetConsumers: { downstreamEdges: 4, byService: { query: 3, chart: 1 },
        byActivity: { executed: 4 }, truncated: false } };
    }
    if (doc.includes("query SimilarDatasets")) {
      return { similarDatasets: [{ id: "ds-2", urn: "wr:t:dataset:dataset/ds-2", name: "claims_2024", score: 0.91 }] };
    }
    if (doc.includes("query DatasetVersions")) {
      return { datasetVersions: { nodes: [
        { id: "dv-3", urn: null, versionNo: 3, icebergSnapshotId: "987", schema: null, schemaDiff: null,
          breakingChange: false, rowCount: 100, bytes: 2048, producedByUrn: null, profileStatus: "completed",
          expired: false, createdAt: "2026-07-12T00:00:00Z" },
      ], pageInfo: { nextCursor: null, hasMore: false } } };
    }
    if (doc.includes("mutation ReprofileDataset")) {
      return { reprofileDataset: { operationId: "prof-1", profileId: "prof-1", status: "queued" } };
    }
    return {};
  };
});

// Pre-instrumented promise so React's `use()` reads it synchronously — an
// untracked promise suspends the first render and jsdom never flushes the
// retry for the first test in the file.
const params = Promise.resolve({ id: "ds-1" }) as Promise<{ id: string }> & {
  status?: string;
  value?: { id: string };
};
params.status = "fulfilled";
params.value = { id: "ds-1" };

describe("Dataset detail page (consumers/versions/similar/re-profile, Tier 4a)", () => {
  it("renders the consumers rollup from the real datasetConsumers query", async () => {
    const user = userEvent.setup();
    // `use(params)` suspends on the first render — a Suspense boundary is
    // required in jsdom (the app shell provides one in production).
    renderWithProviders(
      <Suspense fallback={null}>
        <DatasetDetailPage params={params} />
      </Suspense>,
    );

    // First mount in the suite: give the suspended params promise a beat.
    await user.click(await screen.findByRole("tab", { name: /consumers/i }, { timeout: 3000 }));
    // Non-virtualized card content — real counts render. ("query" also names
    // the query TAB, so scope to the by-service section instead.)
    expect(await screen.findByText(/4 downstream edge/)).toBeInTheDocument();
    expect(screen.getByText("By consuming service")).toBeInTheDocument();
    expect(screen.getAllByText("query").length).toBeGreaterThanOrEqual(2);
    expect(requests.some((r) => r.doc.includes("query DatasetConsumers") && r.vars?.id === "ds-1")).toBe(true);
  });

  it("lists similar datasets ranked by the real similarity search", async () => {
    const user = userEvent.setup();
    // `use(params)` suspends on the first render — a Suspense boundary is
    // required in jsdom (the app shell provides one in production).
    renderWithProviders(
      <Suspense fallback={null}>
        <DatasetDetailPage params={params} />
      </Suspense>,
    );

    await user.click(await screen.findByRole("tab", { name: /similar/i }));
    expect(await screen.findByText("claims_2024")).toBeInTheDocument();
    expect(screen.getByText(/91%/)).toBeInTheDocument();
  });

  it("shows the version history grid (aria-rowcount; rows are virtualized)", async () => {
    const user = userEvent.setup();
    // `use(params)` suspends on the first render — a Suspense boundary is
    // required in jsdom (the app shell provides one in production).
    renderWithProviders(
      <Suspense fallback={null}>
        <DatasetDetailPage params={params} />
      </Suspense>,
    );

    await user.click(await screen.findByRole("tab", { name: /versions/i }));
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Dataset versions" });
      expect(grid).toHaveAttribute("aria-rowcount", "1");
    });
    expect(requests.some((r) => r.doc.includes("query DatasetVersions") && r.vars?.datasetId === "ds-1")).toBe(true);
  });

  it("triggers a real re-profile (202 async) from the header action", async () => {
    const user = userEvent.setup();
    // `use(params)` suspends on the first render — a Suspense boundary is
    // required in jsdom (the app shell provides one in production).
    renderWithProviders(
      <Suspense fallback={null}>
        <DatasetDetailPage params={params} />
      </Suspense>,
    );

    await user.click(await screen.findByRole("button", { name: /re-profile/i }));
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation ReprofileDataset"));
      expect(call?.vars?.id).toBe("ds-1");
      expect(call?.vars?.idempotencyKey).toBeTruthy();
    });
    expect(await screen.findByTestId("notice-banner")).toHaveTextContent(/re-profile started/i);
  });
});
