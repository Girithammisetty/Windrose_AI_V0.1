import { defineConfig } from "vitest/config";

// WS5 (BRD 58): a real, enforced coverage floor. 40% is a deliberately low
// starting bar -- measured overall coverage was ~76% lines / ~66% branches at
// the time this landed, so this only catches a newly-added, essentially
// untested module, not today's code. Ratchet up over time; never lower
// without recording why in docs/brd/58_production_hardening_BRD.md.
export default defineConfig({
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary"],
      thresholds: {
        lines: 40,
        statements: 40,
        functions: 40,
        branches: 40,
      },
    },
  },
});
