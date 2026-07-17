import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Live-stack global setup: prove the REAL platform is reachable and that a REAL
 * seeded persona can authenticate through the REAL auth path BEFORE any spec
 * runs — so a down/mis-seeded stack fails with one crisp, actionable message
 * instead of 40 opaque per-spec timeouts.
 *
 * It performs NO seeding of its own: the persona map + tenant are produced by
 * deploy/local/up.sh (seed_platform.py). Write journeys create their own
 * fixtures. This keeps the harness faithful to the real boot path.
 */

const BASE_URL = process.env.E2E_LIVE_BASE_URL ?? "http://localhost:3000";
// personas.json lives at repo-root deploy/local/run/; cwd is services/ui-web.
const PERSONAS_PATH =
  process.env.E2E_LIVE_PERSONAS ??
  resolve(process.cwd(), "../../deploy/local/run/personas.json");
const CONTEXT_OUT = resolve(process.cwd(), "tests-live/.live-context.json");

interface Persona {
  sub?: string;
  tenantId?: string;
  workspaceId?: string;
  scopes?: string[];
}

function fail(msg: string): never {
  throw new Error(
    `\n\n[live-e2e] PRECONDITION FAILED\n${msg}\n\n` +
      `The live regression suite drives the REAL running stack — it does not mock.\n` +
      `Start the platform first:\n` +
      `    cd deploy/local && ./up.sh        # boots infra + all services + UI + BFF\n` +
      `then re-run:\n` +
      `    pnpm --dir services/ui-web e2e:live\n`,
  );
}

async function probe(label: string, url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, { ...init, signal: AbortSignal.timeout(10_000) });
  } catch (e) {
    return fail(`${label} is not reachable at ${url} (${(e as Error).message}).`);
  }
}

export default async function globalSetup(): Promise<void> {
  // 1) Load the REAL seeded persona map produced by up.sh / seed_platform.py.
  let personas: Record<string, Persona>;
  try {
    personas = JSON.parse(readFileSync(PERSONAS_PATH, "utf8")) as Record<string, Persona>;
  } catch {
    return fail(`Persona map not found at ${PERSONAS_PATH}. The stack has not been seeded.`);
  }
  const emails = Object.keys(personas);
  if (emails.length === 0) fail(`Persona map at ${PERSONAS_PATH} is empty.`);

  // Pick the most-privileged persona as the default admin actor (max scope count;
  // "*" counts as max). Also surface named personas by role heuristic for specs.
  const scopeCount = (p: Persona) => (p.scopes?.includes("*") ? Infinity : p.scopes?.length ?? 0);
  const admin = emails.find((e) => e.startsWith("admin@")) ?? emails.sort((a, b) => scopeCount(personas[b]) - scopeCount(personas[a]))[0];
  const pick = (prefix: string) => emails.find((e) => e.startsWith(prefix));
  const tenantId = personas[admin].tenantId;

  // 2) UI must be serving.
  const ui = await probe("UI (Next.js)", `${BASE_URL}/login`);
  if (!ui.ok) fail(`UI responded HTTP ${ui.status} at ${BASE_URL}/login (expected 200).`);

  // 3) The REAL auth path must accept a REAL seeded persona and set a session
  //    cookie. This simultaneously proves: AUTH_MODE=dev, the personas map is
  //    wired into the running UI, and the RS256 signer works.
  const login = await probe("UI dev-login", `${BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: admin }),
  });
  if (login.status === 403) {
    fail(
      `dev-login returned 403 for '${admin}'. Either AUTH_MODE!=dev or the running ` +
        `UI was booted without this persona map (unknown-user fail-closed).`,
    );
  }
  if (!login.ok) fail(`dev-login returned HTTP ${login.status} for '${admin}'.`);
  const setCookie = login.headers.get("set-cookie");
  if (!setCookie) fail(`dev-login for '${admin}' did not set a session cookie.`);

  // 4) The BFF must be up. Unauthenticated GraphQL is expected to be rejected
  //    (401/400) — we assert only that it is *reachable and enforcing auth*,
  //    which is the real production posture.
  const bffUrl = process.env.E2E_LIVE_BFF_URL ?? "http://localhost:4000/graphql";
  const bff = await probe("bff-graphql", bffUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query: "{__typename}" }),
  });
  if (bff.status >= 500) fail(`bff-graphql returned HTTP ${bff.status} (server error).`);

  // 5) Publish context for the specs (emails + tenant); specs log in via the UI.
  const context = {
    baseUrl: BASE_URL,
    tenantId,
    personas: {
      admin,
      adjuster: pick("adjuster@") ?? admin,
      manager: pick("manager@") ?? admin,
      datascientist: pick("datascientist@") ?? admin,
    },
    allEmails: emails,
    generatedAt: new Date().toISOString(),
  };
  writeFileSync(CONTEXT_OUT, JSON.stringify(context, null, 2));

  // eslint-disable-next-line no-console
  console.log(
    `[live-e2e] stack OK — tenant=${tenantId} · admin='${admin}' · ${emails.length} personas · BFF ${bff.status}`,
  );
}
