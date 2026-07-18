import { test, loginAs, PERSONAS } from "./fixtures";
import { resolve } from "node:path";

/**
 * Screenshot capture — the BUILD journey (data sourcing → preparation →
 * pipelines → AI/ML → copilot) for the End-User Guide. Best-effort, same rules
 * as zz-capture-guide: never abort the run on one missing screen, always
 * screenshot whatever rendered. Real running stack, real seeded data.
 */

const SHOTS = resolve(process.cwd(), "../../docs/platform/screenshots");
const VIEWPORT = { width: 1440, height: 900 };

test.use({ viewport: VIEWPORT });
test.describe.configure({ mode: "serial" });

// Opt-in only: this spec MUTATES docs/platform/screenshots and drives slow
// full-journey flows, so it must not run in the normal e2e:live suite / CI.
// Run explicitly:  CAPTURE=1 pnpm --dir services/ui-web e2e:live
test.beforeEach(() => {
  test.skip(!process.env.CAPTURE, "set CAPTURE=1 to run screenshot-capture specs");
});

async function shot(page: any, name: string) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: resolve(SHOTS, `${name}.png`), fullPage: false });
  // eslint-disable-next-line no-console
  console.log(`  captured ${name}.png`);
}

async function nav(page: any, label: string): Promise<boolean> {
  try {
    await page.getByRole("link", { name: label, exact: true }).first().click({ timeout: 8000 });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    return true;
  } catch {
    return false;
  }
}

async function clickIfPresent(page: any, name: RegExp, ms = 5000): Promise<boolean> {
  try {
    await page.getByRole("button", { name }).first().click({ timeout: ms });
    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
    return true;
  } catch {
    try {
      await page.getByRole("link", { name }).first().click({ timeout: ms });
      await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
      return true;
    } catch {
      return false;
    }
  }
}

test("capture — data sourcing (sources, ingestions, upload wizard)", async ({ page }) => {
  await loginAs(page, PERSONAS().admin);

  if (await nav(page, "Data Sources")) await shot(page, "20-data-sources");
  // Try to open the "new / connect / add source" wizard.
  if (await clickIfPresent(page, /new|connect|add source|add data source|create/i)) {
    await shot(page, "21-new-source-wizard");
  }

  if (await nav(page, "Ingestions")) await shot(page, "22-ingestions");
});

test("capture — data preparation (queries, semantic builder)", async ({ page }) => {
  await loginAs(page, PERSONAS().admin);

  if (await nav(page, "Queries")) await shot(page, "23-queries");

  if (await nav(page, "Semantic Models")) {
    // Open the builder via "New model".
    if (await clickIfPresent(page, /new model|create model|new semantic/i)) {
      await shot(page, "24-semantic-builder");
    }
  }
});

test("capture — pipelines (list + builder)", async ({ page }) => {
  await loginAs(page, PERSONAS().admin);

  if (await nav(page, "Pipelines")) {
    await shot(page, "25-pipelines");
    if (await clickIfPresent(page, /new pipeline|create pipeline|new/i)) {
      await shot(page, "26-pipeline-builder");
    }
  }
});

test("capture — AI/ML (experiments, models, eval)", async ({ page }) => {
  await loginAs(page, PERSONAS().admin);

  if (await nav(page, "ML")) {
    await shot(page, "27-ml");
    // Open first experiment/model if listed.
    try {
      await page.getByRole("row").nth(1).click({ timeout: 5000 });
      await page.waitForLoadState("networkidle").catch(() => {});
      await shot(page, "28-ml-detail");
    } catch { /* skip */ }
  }

  await loginAs(page, PERSONAS().admin);
  if (await nav(page, "Eval")) await shot(page, "29-eval");
});

test("capture — copilot in real use (grounded answer)", async ({ page }) => {
  // Real Ollama streaming on a Mac is slow: the answer poll loop below alone
  // budgets up to 60s, on top of login + nav + drawer open. The 90s default is
  // too tight and trips a hard test-timeout (not a product failure).
  test.setTimeout(180_000);
  await loginAs(page, PERSONAS().adjuster);

  // Work in a case so the copilot has grounded context.
  await nav(page, "Cases");
  try {
    await page.getByRole("row").filter({ hasText: /CLM-/i }).first().click({ timeout: 6000 });
    await page.waitForLoadState("networkidle").catch(() => {});
  } catch { /* stay on list */ }

  // Open the copilot drawer.
  await clickIfPresent(page, /copilot/i);
  await page.waitForTimeout(800);

  // Type a grounded question and send.
  const question = "In one or two sentences, what is this claim and why might it need review?";
  try {
    const box = page.getByPlaceholder(/ask about what you'?re looking at|ask/i).first();
    await box.fill(question, { timeout: 6000 });
    await shot(page, "30-copilot-ask");
    await page.getByRole("button", { name: /send/i }).first().click({ timeout: 5000 });
    // Wait for a real (Ollama) response to stream in — up to 60s.
    await page.waitForTimeout(2000);
    await page.getByText(question).first().waitFor({ timeout: 10000 }).catch(() => {});
    // Poll for an assistant reply bubble that isn't the disclaimer/echo.
    for (let i = 0; i < 30; i++) {
      await page.waitForTimeout(2000);
      const bubbles = await page.locator("text=/\\w{40,}/").count().catch(() => 0);
      if (bubbles > 2) break;
    }
    await shot(page, "31-copilot-answer");
  } catch {
    await shot(page, "31-copilot-answer");
  }
});
