import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/** graphqlRequest routed by operation name; viewer is a full admin. DataTable
 * rows never materialize in jsdom (virtualized) — assertions target request
 * variables + aria-rowcount (repo convention). */
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

import { AgentCatalogCard } from "./AgentCatalogCard";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const definitions = {
  agentDefinitions: [
    { agentKey: "analytics", displayName: "Analytics", description: "Read-only analytics copilot",
      ownerTeam: "platform-ai", defaultWriteMode: "read_only", status: "published", latestPublishedVersion: 1 },
    { agentKey: "case-triage", displayName: "Case Triage", description: "Triage assistant",
      ownerTeam: "platform-ai", defaultWriteMode: "proposal", status: "published", latestPublishedVersion: 1 },
  ],
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query AgentDefinitions")) return definitions;
    return {};
  };
});

describe("AgentCatalogCard (agent-runtime registry browse)", () => {
  it("lists the real agent catalog and reflects the row count in the grid", async () => {
    renderWithProviders(<AgentCatalogCard />);
    await waitFor(() => {
      expect(screen.getByRole("grid", { name: "Agent catalog" })).toHaveAttribute("aria-rowcount", "2");
    });
    expect(requests.some((r) => r.doc.includes("query AgentDefinitions"))).toBe(true);
    // No versions/config fan-out before an agent is selected.
    expect(requests.some((r) => r.doc.includes("query AgentVersions"))).toBe(false);
    expect(requests.some((r) => r.doc.includes("query TenantAgentConfig"))).toBe(false);
  });
});
