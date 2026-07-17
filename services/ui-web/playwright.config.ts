import { defineConfig, devices } from "@playwright/test";
import { generateKeyPairSync } from "node:crypto";

/**
 * E2E runs against a REAL running bff-graphql (booted in globalSetup, pointed at
 * the OpenAPI-shaped contract server) and the REAL Next.js app (webServer below).
 * The app's data layer calls the real BFF; SSE goes to the contract hub speaking
 * the real realtime-hub wire protocol. See tests-e2e/global-setup.ts.
 */
const APP_PORT = 3100;

// Fix the dev signing key across route-handler bundles: in Next dev each route
// handler is bundled separately, so an *ephemeral* in-process key would differ
// between /api/auth/login (signs) and /api/auth/jwks (BFF verifies against),
// yielding a signature mismatch. Injecting one real RS256 keypair via env makes
// every route use the same key — real crypto, deterministic within the run.
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const kid = "ui-web-dev-1";
const privJwk = { ...(privateKey.export({ format: "jwk" }) as object), kid, alg: "RS256", use: "sig" };
const pubJwk = { ...(publicKey.export({ format: "jwk" }) as object), kid, alg: "RS256", use: "sig" };

export default defineConfig({
  testDir: "./tests-e2e",
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./tests-e2e/global-setup.ts",
  globalTeardown: "./tests-e2e/global-teardown.ts",
  use: {
    baseURL: `http://localhost:${APP_PORT}`,
    trace: "retain-on-failure",
    actionTimeout: 15_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Boot the real Next app in dev, wired to the real BFF + contract hub.
    command: "pnpm dev",
    port: APP_PORT,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      AUTH_MODE: "dev",
      JWT_ISSUER: "windrose-dev",
      BFF_URL: "http://localhost:4100/graphql",
      REALTIME_HUB_URL: "http://localhost:4600",
      NEXT_PUBLIC_REALTIME_HUB_URL: "http://localhost:4600",
      AGENT_RUNTIME_URL: "http://localhost:4600",
      DEV_JWT_PRIVATE_JWK: JSON.stringify(privJwk),
      DEV_JWT_PUBLIC_JWK: JSON.stringify(pubJwk),
    },
  },
});
