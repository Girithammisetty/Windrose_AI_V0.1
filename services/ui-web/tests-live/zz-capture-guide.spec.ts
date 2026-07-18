import { test, loginAs, logout, PERSONAS } from "./fixtures";
import { resolve } from "node:path";

/**
 * Screenshot capture for the End-User Persona Guide (claims-triage pack).
 *
 * NOT a regression assertion suite — it drives the REAL running stack as each
 * seeded persona and writes real PNG screenshots into docs/platform/screenshots.
 * Every shot is best-effort: a failure to reach one screen must not abort the
 * rest of the capture, so each is wrapped and always screenshots whatever
 * actually rendered.
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
  await page.waitForTimeout(900); // let data/charts settle
  await page.screenshot({ path: resolve(SHOTS, `${name}.png`), fullPage: false });
  // eslint-disable-next-line no-console
  console.log(`  captured ${name}.png`);
}

/** Click a left-nav link by its visible text; return true if it navigated. */
async function nav(page: any, label: string): Promise<boolean> {
  try {
    const link = page.getByRole("link", { name: label, exact: true }).first();
    await link.click({ timeout: 8000 });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    return true;
  } catch {
    return false;
  }
}

test("capture — login page", async ({ page }) => {
  await page.goto("/login");
  await page.waitForLoadState("networkidle").catch(() => {});
  await shot(page, "01-login");
});

test("capture — admin home / learning loop", async ({ page }) => {
  await loginAs(page, PERSONAS().admin);
  await shot(page, "02-home");
});

test("capture — data: datasets + dashboards + semantic models", async ({ page }) => {
  await loginAs(page, PERSONAS().datascientist);

  if (await nav(page, "Datasets")) await shot(page, "03-datasets");

  // Open first dataset → profile if possible
  try {
    await page.getByRole("link").filter({ hasText: /claim|dataset|\.csv/i }).first().click({ timeout: 6000 });
    await page.waitForLoadState("networkidle").catch(() => {});
    await shot(page, "04-dataset-detail");
  } catch { /* skip */ }

  if (await nav(page, "Dashboards")) {
    await shot(page, "05-dashboards");
    try {
      await page.getByText(/claims insights/i).first().click({ timeout: 6000 });
      await page.waitForLoadState("networkidle").catch(() => {});
      await shot(page, "06-dashboard-detail");
    } catch { /* skip */ }
  }

  if (await nav(page, "Semantic Models")) await shot(page, "07-semantic-models");
});

test("capture — adjuster: case queue + case detail + copilot", async ({ page }) => {
  await logout(page);
  await loginAs(page, PERSONAS().adjuster);

  if (await nav(page, "Cases")) await shot(page, "08-cases");

  // Open the first case row/link in the worklist.
  try {
    await page.getByRole("row").filter({ hasText: /CLM-/i }).first().click({ timeout: 6000 });
    await page.waitForLoadState("networkidle").catch(() => {});
    await shot(page, "09-case-detail");
  } catch {
    try {
      await page.getByRole("link").filter({ hasText: /CLM-/i }).first().click({ timeout: 6000 });
      await page.waitForLoadState("networkidle").catch(() => {});
      await shot(page, "09-case-detail");
    } catch { /* skip */ }
  }

  // Try to reveal a proposal/rationale tab within the case.
  try {
    await page.getByRole("tab", { name: /proposal|recommendation|copilot/i }).first().click({ timeout: 5000 });
    await shot(page, "10-case-proposal");
  } catch { /* skip */ }

  // Open the copilot drawer if present.
  try {
    await page.getByRole("button", { name: /copilot/i }).first().click({ timeout: 5000 });
    await page.waitForTimeout(1200);
    await shot(page, "11-copilot");
  } catch { /* skip */ }
});

test("capture — manager: approvals inbox + usage", async ({ page }) => {
  await logout(page);
  await loginAs(page, PERSONAS().manager);

  if (await nav(page, "Approvals")) await shot(page, "12-approvals");

  // Open a proposal card if one exists.
  try {
    await page.getByText(/rationale|proposed|approve|pending/i).first().click({ timeout: 5000 });
    await page.waitForLoadState("networkidle").catch(() => {});
    await shot(page, "13-proposal-detail");
  } catch { /* skip */ }

  // Usage / cost panel.
  try {
    await page.goto("/admin/usage");
    await page.waitForLoadState("networkidle").catch(() => {});
    await shot(page, "14-usage");
  } catch { /* skip */ }
});

test("capture — governance: decision tables + admin", async ({ page }) => {
  await logout(page);
  await loginAs(page, PERSONAS().admin);

  if (await nav(page, "Decision Tables")) await shot(page, "15-decision-tables");

  for (const [route, name] of [
    ["/admin/tools", "16-admin-tools"],
    ["/admin/ai-gateway/ladders", "17-ai-gateway-ladders"],
  ] as const) {
    try {
      await page.goto(route);
      await page.waitForLoadState("networkidle").catch(() => {});
      await shot(page, name);
    } catch { /* skip */ }
  }
});
