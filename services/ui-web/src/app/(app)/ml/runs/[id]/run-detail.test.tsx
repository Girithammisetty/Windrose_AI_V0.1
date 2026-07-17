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
vi.mock("@/lib/realtime/useHubTopics", () => ({ useHubTopics: () => {} }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import RunDetailPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const runResult = {
  run: {
    id: "run-1", urn: "wr:t-42:experiment:run/run-1", name: "trial-1", status: "SUCCEEDED",
    metrics: { f1: 0.91 }, params: { max_depth: "6" }, experimentId: "exp-1",
    model: null,
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query RunDetail")) return runResult;
    if (doc.includes("query RunNote")) return { runNote: { runId: "run-1", description: "seed note" } };
    if (doc.includes("query RunArtifacts")) return { runArtifacts: [] };
    if (doc.includes("query RunMetricHistory")) return { runMetricHistory: [] };
    return {};
  };
});

// Pre-instrumented promise so React's `use()` reads it synchronously.
const params = Promise.resolve({ id: "run-1" }) as Promise<{ id: string }> & {
  status?: string;
  value?: { id: string };
};
params.status = "fulfilled";
params.value = { id: "run-1" };

function renderPage() {
  return renderWithProviders(
    <Suspense fallback={null}>
      <RunDetailPage params={params} />
    </Suspense>,
  );
}

describe("Run detail — register as model (experiment-service register)", () => {
  it("sends the dialog fields as the real mutation variables", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query RunDetail")) return runResult;
      if (doc.includes("query RunNote")) return { runNote: null };
      if (doc.includes("query RunArtifacts")) return { runArtifacts: [] };
      if (doc.includes("query RunMetricHistory")) return { runMetricHistory: [] };
      if (doc.includes("mutation RegisterRunAsModel")) {
        return { registerRunAsModel: { modelId: "m-9", version: 1, stage: "none", modelCreated: true } };
      }
      return {};
    };
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Register as model" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Model name"), "claims-severity");
    await user.type(within(dialog).getByLabelText("Description (optional)"), "first cut");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation RegisterRunAsModel"));
      expect(call?.vars).toMatchObject({
        experimentId: "exp-1",
        runId: "run-1",
        input: { modelName: "claims-severity", description: "first cut" },
      });
      // Untouched optional fields stay unset (JSON.stringify drops undefined
      // on the wire), never empty strings.
      expect(call?.vars?.input?.flavor).toBeUndefined();
    });
    // The success view links to the REAL new model id.
    const link = await screen.findByRole("link", { name: /Open model m-9/ });
    expect(link).toHaveAttribute("href", "/ml/models/m-9");
  });

  it("surfaces the RunNotFinished error verbatim in the dialog", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query RunDetail")) return runResult;
      if (doc.includes("query RunNote")) return { runNote: null };
      if (doc.includes("query RunArtifacts")) return { runArtifacts: [] };
      if (doc.includes("query RunMetricHistory")) return { runMetricHistory: [] };
      if (doc.includes("mutation RegisterRunAsModel")) {
        return Promise.reject(new Error("run must be finished to register (EXP-FR-031)"));
      }
      return {};
    };
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Register as model" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Model name"), "claims-severity");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));

    expect(await within(dialog).findByTestId("mutation-error")).toHaveTextContent(
      "run must be finished to register (EXP-FR-031)",
    );
  });
});

describe("Run detail — notes tab (experiment-service /runs/{id}/note)", () => {
  it("prefills from the real note and saves via upsertRunNote with the edited text", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query RunDetail")) return runResult;
      if (doc.includes("query RunNote")) return { runNote: { runId: "run-1", description: "seed note" } };
      if (doc.includes("query RunArtifacts")) return { runArtifacts: [] };
      if (doc.includes("query RunMetricHistory")) return { runMetricHistory: [] };
      if (doc.includes("mutation UpsertRunNote")) {
        return { upsertRunNote: { runId: "run-1", description: "seed note — revised" } };
      }
      return {};
    };
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "notes" }));
    const textarea = await screen.findByLabelText("Run note");
    await waitFor(() => expect(textarea).toHaveValue("seed note"));

    await user.type(textarea, " — revised");
    await user.click(screen.getByRole("button", { name: "Save note" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation UpsertRunNote"));
      expect(call?.vars).toMatchObject({ runId: "run-1", description: "seed note — revised" });
    });
  });
});
