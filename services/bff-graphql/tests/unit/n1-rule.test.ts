import { describe, it } from "vitest";
import { RuleTester } from "eslint";
import rule from "../../eslint-rules/no-n1-in-nested-resolver.mjs";

/**
 * Proves the custom lint rule (BFF-FR-030 CI gate) flags a per-item client
 * call in a nested entity resolver and passes a loader-based one. This is the
 * static guard that would have caught the Experiment.runs / Run.model finding.
 */
const tester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: "module" },
});

describe("no-n1-in-nested-resolver ESLint rule", () => {
  it("flags per-item client calls and allows loader/annotated ones", () => {
    tester.run("no-n1-in-nested-resolver", rule as any, {
      valid: [
        // Hydrates via a dataloader -> OK.
        { code: `const r = { Experiment: { runs: (p, a, ctx) => ctx.loaders.runsByExperimentId.load(p.id) } };` },
        // Non-entity blocks (roots) may call clients freely.
        { code: `const r = { Query: { experiment: (p, a, ctx) => ctx.clients.experiment.experiment(a.id) } };` },
        // Explicit escape hatch for a differently-batched resolver.
        {
          code: `const r = { Case: {\n// n1-safe: batched via dashboardData\ndata: (p, a, ctx) => ctx.clients.chart.chartData(p.id) } };`,
        },
      ],
      invalid: [
        {
          code: `const r = { Experiment: { runs: (p, a, ctx) => ctx.clients.experiment.experimentRuns(p.id) } };`,
          errors: [{ messageId: "n1" }],
        },
        {
          code: `const r = { Run: { model: (p, a, ctx) => ctx.clients.experiment.model(p._modelId) } };`,
          errors: [{ messageId: "n1" }],
        },
      ],
    });
  });
});
