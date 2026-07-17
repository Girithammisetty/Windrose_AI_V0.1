import { describe, it, expect, vi, beforeEach } from "vitest";
import { Suspense } from "react";
import { screen, waitFor, within } from "@testing-library/react";
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
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import CaseDetailPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};
const usersResult = {
  users: {
    nodes: [{ id: "u-1", urn: "wr:t-42:identity:user/u-1", email: "ann@x.com", fullName: "Ann" }],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};
const dispositionsResult = {
  dispositions: [
    { id: "d-1", urn: "wr:t:case:disposition/d-1", workspaceId: "ws", code: "fraud_confirmed",
      label: "Fraud confirmed", category: "true_positive", requiresNote: true, active: true,
      createdAt: null, updatedAt: null },
    { id: "d-2", urn: "wr:t:case:disposition/d-2", workspaceId: "ws", code: "retired",
      label: "Retired", category: "other", requiresNote: false, active: false,
      createdAt: null, updatedAt: null },
  ],
};
const emptyTimeline = { caseTimeline: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };

function caseResult(overrides: Record<string, unknown> = {}) {
  return {
    case: {
      id: "c-1", urn: "wr:t-42:case:case/c-1", caseNumber: 7, title: "Case #7",
      status: "IN_PROGRESS", severity: "HIGH", dueDate: "2026-07-20T00:00:00Z",
      createdAt: "2026-07-09T00:00:00Z", description: "suspicious claim",
      dispositionId: null, resolutionNote: null, resolvedAt: null, closedAt: null,
      caseVersion: 4, reassignCount: 1,
      assignee: { id: "u-2", email: "bob@x.com", fullName: "Bob" },
      sourceDataset: null, proposals: [],
      ...overrides,
    },
  };
}

let detail = caseResult();

beforeEach(() => {
  requests.length = 0;
  detail = caseResult();
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Users")) return usersResult;
    if (doc.includes("query Dispositions")) return dispositionsResult;
    if (doc.includes("query CaseTimeline")) return emptyTimeline;
    if (doc.includes("query CaseDetail")) return detail;
    // Sync-to-SoR dialog (task #69) fetches outgoing connections eagerly in
    // CaseActionsBar — an empty connection list here since no test targets it.
    if (doc.includes("query Connections")) return { connections: { nodes: [], pageInfo: { hasMore: false } } };
    return {};
  };
});

// Pre-instrumented promise so React's `use()` reads it synchronously — an
// untracked promise suspends the first render and jsdom never flushes the
// retry for the first test in the file.
const params = Promise.resolve({ id: "c-1" }) as Promise<{ id: string }> & {
  status?: string;
  value?: { id: string };
};
params.status = "fulfilled";
params.value = { id: "c-1" };

function renderPage() {
  // `use(params)` suspends on the first render — a Suspense boundary is
  // required in jsdom (the app shell provides one in production).
  return renderWithProviders(
    <Suspense fallback={null}>
      <CaseDetailPage params={params} />
    </Suspense>,
  );
}

describe("Case detail actions bar — buttons derived from the real state machine", () => {
  it("in_progress offers reassign/unassign/resolve/escalate but never start/reopen/close", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Resolve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reassign" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unassign" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Escalate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reopen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  });

  it("unassigned offers assign (not reassign) + escalate only", async () => {
    detail = caseResult({ status: "UNASSIGNED", assignee: null });
    renderPage();
    expect(await screen.findByRole("button", { name: "Assign" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Escalate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unassign" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
  });

  it("draft offers start; resolved offers reopen + close; a >30-day-old resolution disables reopen", async () => {
    detail = caseResult({ status: "DRAFT" });
    const first = renderPage();
    expect(await first.findByRole("button", { name: "Start" })).toBeInTheDocument();
    first.unmount();

    detail = caseResult({
      status: "RESOLVED",
      resolvedAt: new Date(Date.now() - 40 * 86_400_000).toISOString(),
    });
    renderPage();
    const reopen = await screen.findByRole("button", { name: "Reopen" });
    expect(reopen).toBeDisabled();
    expect(reopen).toHaveAttribute("title", expect.stringMatching(/30 days/));
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
  });

  it("closed is terminal — no lifecycle buttons at all", async () => {
    detail = caseResult({ status: "CLOSED", closedAt: "2026-07-11T00:00:00Z" });
    renderPage();
    await screen.findByText("Case #7");
    for (const name of ["Assign", "Reassign", "Unassign", "Start", "Resolve", "Reopen", "Close", "Escalate"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });
});

describe("Case detail actions — real mutations", () => {
  it("resolveCase carries the chosen dispositionId + required note", async () => {
    handler = (doc: string, vars: any) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Users")) return usersResult;
      if (doc.includes("query Dispositions")) return dispositionsResult;
      if (doc.includes("query CaseTimeline")) return emptyTimeline;
      if (doc.includes("query CaseDetail")) return detail;
      if (doc.includes("query Connections")) return { connections: { nodes: [], pageInfo: { hasMore: false } } };
      if (doc.includes("mutation ResolveCase")) {
        return { resolveCase: { ...caseResult().case, status: "RESOLVED", dispositionId: vars.dispositionId } };
      }
      return {};
    };
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Resolve" }));
    const dialog = await screen.findByRole("dialog");
    // Only ACTIVE dispositions are offered — the inactive "Retired" is absent.
    const select = within(dialog).getByLabelText(/disposition/i);
    expect(within(select).queryByRole("option", { name: /retired/i })).toBeNull();
    await user.selectOptions(select, "d-1");
    // d-1 requiresNote — the confirm is a no-op until a note is typed.
    await user.click(within(dialog).getByRole("button", { name: "Resolve" }));
    expect(requests.some((r) => r.doc.includes("mutation ResolveCase"))).toBe(false);

    await user.type(within(dialog).getByLabelText(/resolution note/i), "confirmed staged accident");
    await user.click(within(dialog).getByRole("button", { name: "Resolve" }));
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation ResolveCase"));
      expect(call?.vars).toMatchObject({
        id: "c-1",
        dispositionId: "d-1",
        resolutionNote: "confirmed staged accident",
      });
      expect(call?.vars?.idempotencyKey).toBeTruthy();
    });
  });

  it("addCaseComment posts the composed body against the real mutation", async () => {
    handler = (doc: string, vars: any) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Users")) return usersResult;
      if (doc.includes("query Dispositions")) return dispositionsResult;
      if (doc.includes("query CaseTimeline")) return emptyTimeline;
      if (doc.includes("query CaseDetail")) return detail;
      if (doc.includes("query Connections")) return { connections: { nodes: [], pageInfo: { hasMore: false } } };
      if (doc.includes("mutation AddCaseComment")) {
        return { addCaseComment: { id: "cm-1", caseId: "c-1", authorId: "u", body: vars.body,
          editedAt: null, createdAt: "2026-07-12T11:00:00Z" } };
      }
      return {};
    };
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("tab", { name: /activity/i }));
    await user.type(await screen.findByLabelText("Add a comment"), "flagging for SIU review");
    await user.click(screen.getByRole("button", { name: "Comment" }));
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation AddCaseComment"));
      expect(call?.vars).toMatchObject({ caseId: "c-1", body: "flagging for SIU review" });
      expect(call?.vars?.idempotencyKey).toBeTruthy();
    });
  });
});
