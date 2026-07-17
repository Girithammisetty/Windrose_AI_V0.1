import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { ProposalDetail } from "./ProposalDetail";
import { renderWithProviders } from "@/test/utils";
import type { Proposal } from "@/lib/graphql/types";

const proposal: Proposal = {
  id: "p1",
  urn: "wr:t:proposal:proposal/p1",
  agentKey: "triage-agent",
  tool: "assign_case",
  argsDiff: { before: { assignee: null }, after: { assignee: "user-9" } },
  rationale: "High fraud score suggests reassignment.",
  affectedUrns: ["wr:t:case:case/c-1"],
  predictedEffect: { summary: "Case reassigned", blast_radius: 1, reversibility: "reversible" },
  status: "PENDING",
  decision: null,
  createdAt: null,
};

describe("ProposalDetail (UI-FR-033)", () => {
  it("shows the persistent AI label and the args diff", () => {
    renderWithProviders(<ProposalDetail proposal={proposal} />);
    expect(screen.getAllByRole("note").length).toBeGreaterThan(0); // AiLabel present
    expect(screen.getByText(/High fraud score/)).toBeInTheDocument();
    expect(screen.getAllByText(/assignee/).length).toBeGreaterThan(0); // diff path
  });

  it("requires a reason to reject (AC-6): confirm stays disabled until a reason is typed", () => {
    renderWithProviders(<ProposalDetail proposal={proposal} />);
    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));
    const confirm = screen.getByRole("button", { name: /confirm reject/i });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByTestId("reject-reason"), { target: { value: "not warranted" } });
    expect(confirm).toBeEnabled();
  });

  it("marks a destructive proposal", () => {
    renderWithProviders(<ProposalDetail proposal={{ ...proposal, tool: "delete_case" }} />);
    expect(screen.getByText(/destructive/i)).toBeInTheDocument();
  });
});
