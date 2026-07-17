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

import PipelineSchedulesPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const schedulesResult = {
  pipelineSchedules: [
    {
      id: "sch-1", urn: "wr:t-42:pipeline:schedule/sch-1", scheduleId: "sch-1", templateId: "tpl-1",
      name: "Nightly Retrain", cron: "0 2 * * *", timezone: "UTC", runParameters: { label_column: "label" },
      enabled: true, nextFireAt: "2026-07-16T02:00:00Z", lastFireAt: null, lastRunId: null,
      createdAt: "2026-07-12T00:00:00Z",
    },
  ],
};

const templatesPage = {
  pipelineTemplates: {
    nodes: [
      { id: "tpl-1", urn: "wr:t-42:pipeline:template/tpl-1", name: "Claims Retrain", pipelineType: "training",
        activeVersionId: "ver-1", validationStatus: "valid", isSystem: false, archived: false,
        createdBy: null, createdAt: "2026-07-10T00:00:00Z", updatedAt: "2026-07-10T00:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query PipelineSchedules")) return schedulesResult;
    if (doc.includes("query PipelineTemplates")) return templatesPage;
    if (doc.includes("mutation CreatePipelineSchedule")) {
      return { createPipelineSchedule: schedulesResult.pipelineSchedules[0] };
    }
    return {};
  };
});

describe("Pipeline schedules page", () => {
  // DataTable rows never materialize in jsdom (virtualized) — assert on request
  // variables + aria-rowcount, per the repo convention.
  it("lists real pipelineSchedules in the grid (aria-rowcount)", async () => {
    renderWithProviders(<PipelineSchedulesPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Pipeline schedules" });
      expect(grid).toHaveAttribute("aria-rowcount", "1");
    });
    expect(requests.some((r) => r.doc.includes("query PipelineSchedules"))).toBe(true);
  });

  it("creates a schedule with the real camel variables (template + cron + parsed runParameters JSON)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PipelineSchedulesPage />);

    await user.click(await screen.findByRole("button", { name: /new schedule/i }));

    await user.selectOptions(await screen.findByLabelText("Pipeline"), "tpl-1");
    const cronInput = screen.getByLabelText("Cron expression");
    await user.clear(cronInput);
    await user.type(cronInput, "0 3 * * *");
    await user.type(screen.getByLabelText("Name"), "Daily");
    await user.type(screen.getByLabelText("Run parameters (JSON)"), '{{"k": 1}');
    await user.click(screen.getByRole("button", { name: /create schedule/i }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreatePipelineSchedule"));
      expect(call?.vars?.input).toMatchObject({
        templateId: "tpl-1",
        name: "Daily",
        cron: "0 3 * * *",
        runParameters: { k: 1 },
      });
      expect(typeof call?.vars?.idempotencyKey).toBe("string");
    });
  });
});
