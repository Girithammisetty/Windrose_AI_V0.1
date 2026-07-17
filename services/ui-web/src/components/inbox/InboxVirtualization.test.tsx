import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import type { Proposal } from "@/lib/graphql/types";

// --- Mocks for the page's data + platform hooks (keep the test on the list) ---
const proposals: Proposal[] = Array.from({ length: 1000 }, (_, i) => ({
  id: `p${i}`,
  urn: `urn:${i}`,
  agentKey: "triage",
  tool: "assign_case",
  riskTier: "read",
  argsDiff: {},
  rationale: `rationale ${i}`,
  affectedUrns: [],
  predictedEffect: null,
  status: "PENDING",
  decision: null,
  createdAt: null,
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: () => null }),
}));
vi.mock("@/lib/realtime/useHubTopics", () => ({ useHubTopics: () => {} }));
vi.mock("@/stores/ui", () => ({
  useToasts: (selector: (s: { push: () => void }) => unknown) => selector({ push: () => {} }),
}));
vi.mock("@/components/inbox/ProposalDetail", () => ({
  ProposalDetail: () => <div data-testid="detail" />,
}));
vi.mock("@/lib/graphql/hooks", () => ({
  useProposalsInbox: () => ({
    data: { pages: [{ nodes: proposals, pageInfo: { nextCursor: null, hasMore: false } }] },
    isLoading: false,
    isError: false,
    error: null,
    refetch: () => {},
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: () => {},
  }),
  useDecideProposal: () => ({ mutateAsync: vi.fn() }),
}));

import InboxPage from "@/app/(app)/inbox/page";

describe("Inbox virtualization (UI-FR-033)", () => {
  it("keeps the DOM bounded over a 1000-proposal list (windowed, not a full .map)", () => {
    render(<InboxPage />);
    // A naive proposals.map(...) would put all 1000 cards in the DOM. Windowed
    // virtualization renders only a small window (jsdom reports no viewport, so
    // this is well under 100 — never the full 1000).
    const cards = document.querySelectorAll('[role="listitem"]');
    expect(cards.length).toBeLessThan(100);
  });

  it("sizes the scroll spacer for the FULL list, proving all 1000 are virtualized (not empty/broken)", () => {
    render(<InboxPage />);
    const list = document.querySelector('[role="list"]');
    const spacer = list?.firstElementChild as HTMLElement | null;
    expect(spacer).not.toBeNull();
    // getTotalSize() ≈ 1000 rows × estimated height — a large scroll extent that
    // only exists if the virtualizer accounts for every proposal.
    const height = parseInt(spacer!.style.height || "0", 10);
    expect(height).toBeGreaterThan(1000 * 40);
  });
});
