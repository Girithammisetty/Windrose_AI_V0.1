import { describe, it, expect, vi, beforeEach } from "vitest";
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

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import DataIngestionsPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const ingestionsPage = {
  ingestions: {
    nodes: [
      { id: "ing-1", urn: "wr:t:ingestion:ingestion/ing-1", mode: "query", status: "failed", trigger: "manual",
        connectionId: "conn-1", datasetUrn: null, fileFormat: "parquet", statement: "SELECT 1",
        rowsAppended: 0, bytesReceived: 0, bytesTotal: 0, attempts: 1,
        createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:05:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const schedulesPage = {
  ingestionSchedules: {
    nodes: [
      { id: "sch-1", urn: "wr:t:ingestion:schedule/sch-1", connectionId: "conn-1",
        ingestionTemplate: { statement: "SELECT 1", new_dataset: { name: "landed" } },
        cron: "0 6 * * *", intervalSeconds: null, timezone: "UTC", watermark: null,
        overlapPolicy: "skip", enabled: true, workspaceId: "ws",
        lastFiredAt: null, nextFireAt: "2026-07-13T06:00:00Z",
        createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const connectionsPage = {
  connections: {
    nodes: [
      { id: "conn-1", urn: "wr:t:ingestion:connection/conn-1", name: "warehouse", connectorType: "postgres",
        config: {}, secretFields: [], secretSet: true, trafficDirection: "incoming", tags: [],
        workspaceId: "ws", lastTestStatus: "ok", lastTestedAt: null, createdAt: null, updatedAt: null },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query IngestionSchedules")) return schedulesPage;
    if (doc.includes("query Connections")) return connectionsPage;
    if (doc.includes("mutation CreateIngestionSchedule")) {
      return { createIngestionSchedule: schedulesPage.ingestionSchedules.nodes[0] };
    }
    if (doc.includes("query Ingestions")) return ingestionsPage;
    return {};
  };
});

describe("Ingestions page (runs + recurring schedules)", () => {
  // NOTE: DataTable rows never materialize in jsdom (virtualized) — assert on
  // request variables + aria-rowcount, per the repo convention (runs.test.tsx).
  it("lists ingestion runs in the grid (aria-rowcount) with the lifecycle actions column mounted", async () => {
    renderWithProviders(<DataIngestionsPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Ingestion runs" });
      expect(grid).toHaveAttribute("aria-rowcount", "1");
    });
    expect(requests.some((r) => r.doc.includes("query Ingestions"))).toBe(true);
  });

  it("switches to the Schedules tab and fetches real ingestionSchedules", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DataIngestionsPage />);

    await user.click(await screen.findByRole("tab", { name: /schedules/i }));
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Recurring schedules" });
      expect(grid).toHaveAttribute("aria-rowcount", "1");
    });
    expect(requests.some((r) => r.doc.includes("query IngestionSchedules"))).toBe(true);
  });

  it("creates a schedule with the real snake-free camel variables (cron XOR interval, template built from the form)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DataIngestionsPage />);

    await user.click(await screen.findByRole("tab", { name: /schedules/i }));
    await user.click(await screen.findByRole("button", { name: /new schedule/i }));

    await user.selectOptions(await screen.findByLabelText("Connection"), "conn-1");
    // "Cron expression" also names the timing radio — scope to the textbox.
    const cronInput = screen.getByRole("textbox", { name: "Cron expression" });
    await user.clear(cronInput);
    await user.type(cronInput, "0 7 * * 1");
    await user.type(screen.getByLabelText("Source SQL statement"), "SELECT * FROM claims");
    await user.type(screen.getByLabelText("New dataset name"), "claims_daily");
    await user.click(screen.getByRole("button", { name: /create schedule/i }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateIngestionSchedule"));
      expect(call?.vars?.input).toMatchObject({
        connectionId: "conn-1",
        cron: "0 7 * * 1",
        ingestionTemplate: { statement: "SELECT * FROM claims", new_dataset: { name: "claims_daily" } },
      });
      expect(call?.vars?.input?.intervalSeconds).toBeUndefined();
    });
  });
});
