import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

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

import AiGuardrailsPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const initialPolicy = { policy: { pii: { mode: "redact" }, injection: { mode: "block" }, schema_validation: "on" }, version: 3 };

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query AiGuardrailPolicy")) return { aiGuardrailPolicy: initialPolicy };
    if (doc.includes("mutation PutAiGuardrailPolicy")) return { putAiGuardrailPolicy: { policy: { pii: { mode: "block" }, injection: { mode: "block" }, schema_validation: "on" }, version: 4 } };
    return {};
  };
});

describe("ai-gateway guardrails page", () => {
  it("loads the current policy into the editor and shows its version", async () => {
    renderWithProviders(<AiGuardrailsPage />);
    await screen.findByText(/v3/);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toContain('"mode": "redact"'));
  });

  it("saves an edited policy as real JSON via putAiGuardrailPolicy", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AiGuardrailsPage />);

    const textarea = await screen.findByRole("textbox");
    await waitFor(() => expect((textarea as HTMLTextAreaElement).value).toContain("redact"));

    fireEvent.change(textarea, {
      target: { value: JSON.stringify({ pii: { mode: "block" }, injection: { mode: "block" }, schema_validation: "on" }) },
    });

    await user.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation PutAiGuardrailPolicy"));
      expect(call?.vars.policy).toMatchObject({ pii: { mode: "block" } });
    });
  });
});
