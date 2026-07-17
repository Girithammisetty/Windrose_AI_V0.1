import { defineConfig, devices } from "@playwright/test";

/**
 * LIVE-STACK end-to-end regression suite.
 *
 * Unlike `playwright.config.ts` (which boots the UI against a contract server),
 * this config drives the REAL running platform with NOTHING mocked:
 *
 *   real Next.js UI (:3000)  →  real bff-graphql (:4000)  →  all ~22 services
 *   →  real Postgres (RLS) / Redpanda / Redis / MinIO / Iceberg / Temporal.
 *
 * Login is a REAL RS256 dev-JWT minted by the UI's /api/auth/login for a REAL
 * seeded persona (deploy/local/run/personas.json), verified for real by the BFF
 * and enforced by each service's RBAC/OPA. Write journeys create their own
 * fixtures through the real UI so specs are self-contained and order-independent.
 *
 * PREREQUISITE: the stack must already be up (deploy/local/up.sh). This config
 * does NOT boot a webServer — it reuses the running :3000 UI. global-setup
 * fail-closes with an actionable message if the stack is not reachable.
 *
 * Run:  pnpm e2e:live
 */
const BASE_URL = process.env.E2E_LIVE_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests-live",
  testMatch: /.*\.spec\.ts/,
  // Live stack + real infra: generous but bounded timeouts.
  timeout: 90_000,
  expect: { timeout: 20_000 },
  // Journeys mutate a shared RLS tenant; keep them serial + deterministic.
  fullyParallel: false,
  workers: 1,
  // Live services can have transient cold-starts; one retry locally, more in CI.
  retries: process.env.CI ? 2 : 1,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never", outputFolder: "playwright-live-report" }], ["github"]]
    : [["list"], ["html", { open: "never", outputFolder: "playwright-live-report" }]],
  globalSetup: "./tests-live/global-setup.ts",
  outputDir: "./test-results-live",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
