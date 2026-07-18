import { test, loginAs, PERSONAS } from "./fixtures";
import { resolve } from "node:path";

/** Copilot-only capture: open the drawer on a real case, ask a grounded
 * question, wait (bounded) for the real Ollama answer, screenshot. */

const SHOTS = resolve(process.cwd(), "../../docs/platform/screenshots");
test.use({ viewport: { width: 1440, height: 900 } });

// Opt-in only: this spec MUTATES docs/platform/screenshots and drives slow
// full-journey flows, so it must not run in the normal e2e:live suite / CI.
// Run explicitly:  CAPTURE=1 pnpm --dir services/ui-web e2e:live
test.beforeEach(() => {
  test.skip(!process.env.CAPTURE, "set CAPTURE=1 to run screenshot-capture specs");
});

test("capture — copilot grounded answer", async ({ page }) => {
  test.setTimeout(180_000);
  await loginAs(page, PERSONAS().adjuster);

  await page.getByRole("link", { name: "Cases", exact: true }).first().click().catch(() => {});
  await page.waitForLoadState("networkidle").catch(() => {});
  try {
    await page.getByRole("row").filter({ hasText: /CLM-/i }).first().click({ timeout: 8000 });
    await page.waitForLoadState("networkidle").catch(() => {});
  } catch { /* stay */ }

  // Open copilot drawer.
  await page.getByRole("button", { name: /copilot/i }).first().click({ timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(1000);

  const q = "In one sentence, what is this claim about?";
  const box = page.getByPlaceholder(/ask about what you'?re looking at|ask/i).first();
  await box.fill(q).catch(() => {});
  await page.getByRole("button", { name: /send/i }).first().click({ timeout: 6000 }).catch(() => {});

  // Bounded wait for the answer to stream in (Ollama, local).
  await page.waitForTimeout(45_000);
  await page.screenshot({ path: resolve(SHOTS, "31-copilot-answer.png"), fullPage: false });
  // eslint-disable-next-line no-console
  console.log("  captured 31-copilot-answer.png");
});
