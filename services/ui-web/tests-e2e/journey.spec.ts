import { test, expect, request as pwRequest } from "@playwright/test";

// Reset the contract server's mutable proposal state before each spec so tests
// are independent (approving in one spec must not empty another's inbox).
test.beforeEach(async () => {
  const ctx = await pwRequest.newContext();
  await ctx.post("http://localhost:4600/__reset").catch(() => {});
  await ctx.dispose();
});

/**
 * The release-gating agentic journey, driven against the REAL bff-graphql:
 *   login → view a claim case → open the copilot drawer → see the AI label +
 *   provenance → see the proposal in the approval inbox → approve it (real
 *   decideProposal mutation to the BFF) → destructive proposal excluded from bulk.
 *
 * Everything the app renders here comes from the real BFF composing the contract
 * services; the copilot streams over real SSE from the contract realtime-hub.
 */

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("adjuster@acme.com");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL("**/");
  await expect(page.getByRole("heading", { name: /welcome/i })).toBeVisible();
}

test("login → case → copilot → inbox approve, with AI label + provenance", async ({ page }) => {
  await login(page);

  // --- View a claim case (real caseSearch → contract case-service) ---
  await page.goto("/cases");
  await expect(page.getByRole("grid", { name: "Cases" })).toBeVisible();
  await expect(page.getByText(/Suspicious auto claim #4471/)).toBeVisible();
  await page.getByText(/Suspicious auto claim #4471/).click();
  await page.waitForURL("**/cases/case-1");
  // Composed cross-service data: assignee (identity) + source dataset (dataset).
  await expect(page.getByText(/Ann Adjuster/)).toBeVisible();

  // Proposals tab shows the AI label + provenance badge on the triage suggestion.
  await page.getByRole("tab", { name: /proposals/i }).click();
  await expect(page.getByRole("button", { name: /AI-generated/i }).first()).toBeVisible();

  // --- Open the copilot drawer: persistent AI label + context URN, then stream ---
  await page.getByRole("button", { name: /copilot/i }).first().click();
  const drawer = page.locator('[data-copilot-drawer="open"]');
  await expect(drawer).toBeVisible();
  // AC-3: context URN present + non-suppressible AI disclosure before streaming.
  await expect(drawer.locator('[data-copilot-context*="case/case-1"]')).toBeVisible();
  await expect(drawer.locator('[data-ai-disclosure="true"]')).toBeVisible();
  await expect(drawer.locator('[data-ai-label="true"]').first()).toBeVisible();

  await drawer.getByLabel(/Ask about what/i).fill("Why is this case high severity?");
  await drawer.getByRole("button", { name: /send/i }).click();
  // Real SSE token stream from the contract hub fills the assistant message.
  await expect(drawer.locator('[data-role="assistant"]')).toContainText(/fraud score/i, { timeout: 20_000 });
  // AI label persists during/after streaming (BR-2).
  await expect(drawer.locator('[data-ai-label="true"]').first()).toBeVisible();

  // --- Approval inbox: destructive excluded from bulk, then approve the benign one ---
  await page.goto("/inbox");
  const assignCard = page.getByRole("listitem").filter({ hasText: "assign_case" });
  const deleteCard = page.getByRole("listitem").filter({ hasText: "delete_case" });
  await expect(assignCard).toBeVisible();
  await expect(deleteCard).toBeVisible();

  // The destructive proposal's bulk checkbox is unselectable by construction (AC-5).
  const destructiveCheckbox = page.locator('input[type="checkbox"][data-destructive="true"]');
  await expect(destructiveCheckbox).toBeDisabled();

  // Approve the benign proposal via the real decideProposal mutation.
  await assignCard.click();
  const detail = page.locator('[data-proposal-detail="prop-assign"]');
  await expect(detail).toBeVisible();
  await expect(detail.locator('[data-ai-label="true"]').first()).toBeVisible();

  const [decideResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/graphql") && r.request().postData()?.includes("DecideProposal") === true,
    ),
    detail.getByTestId("approve").click(),
  ]);
  const body = await decideResp.json();
  expect(body.data.decideProposal.status).toBe("APPROVED");

  // The decided proposal drops out of the pending inbox without a refresh.
  await expect(assignCard).toBeHidden({ timeout: 10_000 });
});

test("reject requires a reason (AC-6)", async ({ page }) => {
  await login(page);
  await page.goto("/inbox");
  await page.getByRole("listitem").filter({ hasText: "assign_case" }).click();
  const detail = page.locator('[data-proposal-detail="prop-assign"]');
  await detail.getByRole("button", { name: /^reject$/i }).click();
  const confirm = detail.getByRole("button", { name: /confirm reject/i });
  await expect(confirm).toBeDisabled();
  await detail.getByTestId("reject-reason").fill("Not warranted");
  await expect(confirm).toBeEnabled();
});

test("route guard fail-closed: unauthenticated users are redirected to login", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/cases");
  await page.waitForURL("**/login**");
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
});
