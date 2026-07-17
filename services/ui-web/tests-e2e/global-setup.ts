import { spawn, type ChildProcess } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";

/**
 * Boots the REAL bff-graphql (services/bff-graphql) pointed at the OpenAPI-shaped
 * contract server, plus the contract server itself (which also serves the real
 * realtime-hub SSE wire protocol). The Next app (Playwright webServer) then talks
 * to this real BFF. JWKS is served by the app at /api/auth/jwks; the BFF verifies
 * the app-minted user JWTs against it for real (real RS256, real edge verify).
 */
const REPO = path.resolve(__dirname, "../../..");
const BFF_DIR = path.join(REPO, "services/bff-graphql");
const CONTRACT = path.join(__dirname, "contract-server.mjs");
const PID_FILE = path.join(__dirname, ".e2e-pids.json");

const HOMEBREW_PATH = "/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:" + (process.env.PATH ?? "");

async function waitFor(url: string, name: string, timeoutMs = 60_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${name} did not become ready at ${url}`);
}

export default async function globalSetup() {
  const children: ChildProcess[] = [];

  // 1) Contract server (downstream REST + realtime-hub SSE).
  const contract = spawn("node", [CONTRACT], {
    env: { ...process.env, PATH: HOMEBREW_PATH, CONTRACT_PORT: "4600" },
    stdio: "inherit",
    detached: true,
  });
  children.push(contract);
  await waitFor("http://localhost:4600/healthz", "contract-server");

  // 2) The REAL bff-graphql, pointed at the contract server + the app JWKS.
  const bff = spawn("pnpm", ["start"], {
    cwd: BFF_DIR,
    env: {
      ...process.env,
      PATH: HOMEBREW_PATH,
      NODE_ENV: "test", // introspection on, ad-hoc operations allowed (not prod)
      VERIFY_JWT: "true",
      JWKS_URL: "http://localhost:3100/api/auth/jwks",
      JWT_ISSUER: "windrose-dev",
      PORT: "4100",
      IDENTITY_URL: "http://localhost:4600",
      DATASET_URL: "http://localhost:4600",
      CASE_URL: "http://localhost:4600",
      CHART_URL: "http://localhost:4600",
      USAGE_URL: "http://localhost:4600",
      EXPERIMENT_URL: "http://localhost:4600",
      AGENT_RUNTIME_URL: "http://localhost:4600",
      REALTIME_HUB_URL: "http://localhost:4600",
    },
    stdio: "inherit",
    detached: true,
  });
  children.push(bff);
  await waitFor("http://localhost:4100/healthz", "bff-graphql");

  writeFileSync(PID_FILE, JSON.stringify(children.map((c) => c.pid).filter(Boolean)));
  console.log("[e2e] contract-server + real bff-graphql are live");
}
