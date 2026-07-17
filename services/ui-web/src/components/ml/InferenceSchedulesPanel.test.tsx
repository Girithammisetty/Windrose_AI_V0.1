import { describe, it, expect, vi, beforeEach } from "vitest";
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
vi.mock("@/lib/realtime/useHubTopics", () => ({ useHubTopics: () => {} }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
// jsdom has no layout, so the windowed DataTable would render zero rows —
// replace the virtualizer with a full-render stand-in so row ACTIONS (pause/
// trigger/edit/delete) are reachable. The grid semantics stay real.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 44,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * 44, size: 44 })),
    scrollToIndex: () => {},
    measureElement: () => {},
  }),
}));

import { InferenceSchedulesPanel } from "./InferenceSchedulesPanel";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

function schedule(overrides: Record<string, unknown> = {}) {
  return {
    id: "sch-1", urn: "wr:t-42:inference:schedule/sch-1", name: "nightly scoring",
    enabled: true, pausedReason: null,
    modelVersionUrn: "wr:t:experiment:model_version/m-1@2", modelUrn: null, stageSelector: null,
    inputSelector: { dataset_urn: "wr:t:dataset:dataset/ds-1" },
    output: { dataset_name: "claims-scores", mode: "append" },
    cron: "0 6 * * *", intervalSeconds: null, timezone: "UTC", overlapPolicy: "skip",
    consecutiveFailures: 0, notifyOnFailure: true, nextFireAt: "2026-07-13T06:00:00Z",
    ...overrides,
  };
}

const schedulesPage = {
  inferenceSchedules: {
    nodes: [
      schedule(),
      schedule({ id: "sch-2", name: "hourly scoring", enabled: false, pausedReason: "USER_PAUSED",
        cron: null, intervalSeconds: 3600, nextFireAt: null }),
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const datasetsPage = {
  datasets: {
    nodes: [{ id: "ds-1", urn: "wr:t:dataset:dataset/ds-1", name: "claims-july", status: "READY" }],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const modelsPage = {
  models: {
    nodes: [{ id: "m-1", urn: "wr:t:experiment:model/m-1", name: "claims", modelType: "classification",
      ownerId: null, description: null, createdAt: null }],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const modelDetail = {
  model: {
    id: "m-1", urn: "wr:t:experiment:model/m-1", name: "claims", modelType: "classification",
    ownerId: null, description: null, createdAt: null,
    versions: [
      { modelId: "m-1", version: 2, urn: "wr:t:experiment:model_version/m-1@2", stage: "production",
        sourceRunId: null, flavor: "mlflow.sklearn", mlflowModelRef: null, stageUpdatedAt: null },
    ],
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query InferenceSchedules")) return schedulesPage;
    if (doc.includes("query Datasets")) return datasetsPage;
    if (doc.includes("query Models")) return modelsPage;
    if (doc.includes("query ModelDetail")) return modelDetail;
    return {};
  };
});

describe("InferenceSchedulesPanel (inference-service /schedules)", () => {
  it("renders the real schedule rows in the grid", async () => {
    renderWithProviders(<InferenceSchedulesPanel />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Inference schedules" });
      expect(grid).toHaveAttribute("aria-rowcount", "2");
    });
    // Enabled row offers Pause; the paused row offers Resume instead.
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("creates a cron-mode schedule with a pinned model version (XOR fields absent)", async () => {
    handler = (doc: string, vars: any) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query InferenceSchedules")) return schedulesPage;
      if (doc.includes("query Datasets")) return datasetsPage;
      if (doc.includes("query ModelDetail")) return modelDetail;
      if (doc.includes("query Models")) return modelsPage;
      if (doc.includes("mutation CreateInferenceSchedule")) {
        return { createInferenceSchedule: schedule({ id: "sch-new", name: vars.input.name }) };
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<InferenceSchedulesPanel />);

    await user.click(await screen.findByRole("button", { name: "New schedule" }));
    const dialog = await screen.findByRole("dialog");

    await user.type(within(dialog).getByLabelText("Name"), "Nightly claims scoring");
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "Model" }), "m-1");
    await user.selectOptions(
      await within(dialog).findByRole("combobox", { name: "Model version" }),
      "wr:t:experiment:model_version/m-1@2",
    );
    await user.selectOptions(within(dialog).getByLabelText("Input dataset"), "wr:t:dataset:dataset/ds-1");
    await user.type(within(dialog).getByLabelText("Output dataset name"), "claims-scores");
    await user.click(within(dialog).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateInferenceSchedule"));
      expect(call?.vars?.input).toMatchObject({
        name: "Nightly claims scoring",
        inputSelector: { dataset_urn: "wr:t:dataset:dataset/ds-1" },
        output: { dataset_name: "claims-scores", mode: "append" },
        modelVersionUrn: "wr:t:experiment:model_version/m-1@2",
        cron: "0 6 * * *",
        timezone: "UTC",
        overlapPolicy: "skip",
        notifyOnFailure: true,
      });
      // The server enforces exactly-one-of on PRESENCE — the unchosen XOR
      // halves must be absent, not null.
      expect(call?.vars?.input).not.toHaveProperty("intervalSeconds");
      expect(call?.vars?.input).not.toHaveProperty("modelUrn");
      expect(call?.vars?.input).not.toHaveProperty("stageSelector");
    });
  });

  it("pause fires the real mutation with the schedule id", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query InferenceSchedules")) return schedulesPage;
      if (doc.includes("query Datasets")) return datasetsPage;
      if (doc.includes("query Models")) return modelsPage;
      if (doc.includes("mutation PauseInferenceSchedule")) {
        return { pauseInferenceSchedule: schedule({ enabled: false, pausedReason: "USER_PAUSED", nextFireAt: null }) };
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<InferenceSchedulesPanel />);

    // Only the enabled row (sch-1) offers Pause.
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation PauseInferenceSchedule"));
      expect(call?.vars).toMatchObject({ id: "sch-1" });
    });
  });
});
