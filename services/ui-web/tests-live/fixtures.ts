import { test as base, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Shared helpers for the live-stack suite. The persona emails + tenant are
 * resolved by global-setup into tests-live/.live-context.json (from the REAL
 * seeded map), so specs never hardcode a tenant id.
 */

interface LiveContext {
  baseUrl: string;
  tenantId: string;
  personas: { admin: string; adjuster: string; manager: string; datascientist: string };
  allEmails: string[];
  generatedAt: string;
}

let cached: LiveContext | null = null;

export function liveContext(): LiveContext {
  if (cached) return cached;
  const p = resolve(process.cwd(), "tests-live/.live-context.json");
  cached = JSON.parse(readFileSync(p, "utf8")) as LiveContext;
  return cached;
}

export const PERSONAS = () => liveContext().personas;

/**
 * Log in through the REAL UI dev-login form as a seeded persona and land on the
 * authenticated home. Mirrors the production-shaped flow: fill email → submit →
 * a real RS256 session cookie is set → redirect to "/".
 */
export async function loginAs(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  // exact match: the login page also has a "Sign in with SSO" button (BYO OIDC),
  // so a loose /sign in/i regex is now ambiguous.
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL("**/");
  await expect(page.getByRole("heading", { name: /welcome/i })).toBeVisible();
}

/** Log out by clearing the session cookie (fail-closed guard tests rely on this). */
export async function logout(page: Page): Promise<void> {
  await page.context().clearCookies();
}

/**
 * Assert a route rendered its real content rather than an error boundary or a
 * silent auth bounce. Used by breadth/smoke specs so a 500 from any downstream
 * service surfaces as a clear failure on that exact page.
 */
export async function expectPageHealthy(page: Page, opts: { notRedirectedFrom?: string } = {}): Promise<void> {
  if (opts.notRedirectedFrom) {
    expect(page.url(), `should not have been bounced to /login from ${opts.notRedirectedFrom}`).not.toContain("/login");
  }
  // A heading proves the page shell + its data-loading header rendered.
  await expect(page.getByRole("heading").first()).toBeVisible();
  // No error-boundary / hard-failure text.
  const errorText = page.getByText(/something went wrong|failed to load|internal server error|unexpected error/i);
  await expect(errorText).toHaveCount(0);
}

/** Assert no console errors accrued during the block (excluding known-noisy sources). */
export function trackConsoleErrors(page: Page): () => string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  return () =>
    errors.filter(
      (e) =>
        // Ignore benign dev/network noise unrelated to app correctness.
        !/favicon|Download the React DevTools|hydration|ResizeObserver/i.test(e),
    );
}

export const test = base;
export { expect };
