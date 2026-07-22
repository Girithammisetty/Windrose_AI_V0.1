import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "server-only": fileURLToPath(new URL("./src/test/server-only-stub.ts", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
    // WS5 (BRD 58): a real, enforced coverage floor. 30% is deliberately low
    // -- measured overall coverage was ~56% lines at the time this landed
    // (tests-e2e/tests-live Playwright specs pull the denominator down since
    // they're not vitest-covered; still a real, conservative starting bar).
    // Ratchet up over time; never lower without recording why in
    // docs/brd/58_production_hardening_BRD.md.
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary"],
      thresholds: {
        lines: 30,
        statements: 30,
        functions: 30,
        branches: 30,
      },
    },
  },
});
