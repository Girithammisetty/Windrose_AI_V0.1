import { describe, it, expect } from "vitest";
import { collect, defaultValues } from "./form";
import type { PipelineStepParam } from "@/lib/graphql/types";

const PARAMS: PipelineStepParam[] = [
  { name: "test_size", type: "number", required: true, default: 0.2, enumValues: null, min: 0, max: 1, help: "Holdout fraction" },
  { name: "n_estimators", type: "integer", required: false, default: 100, enumValues: null, min: 1, max: 1000, help: null },
  { name: "strategy", type: "enum", required: false, default: "mean", enumValues: ["mean", "median", "most_frequent"], min: null, max: null, help: null },
  { name: "shuffle", type: "boolean", required: false, default: true, enumValues: null, min: null, max: null, help: null },
  { name: "columns", type: "array", required: false, default: null, enumValues: null, min: null, max: null, help: null },
];

describe("pipeline step-param form helpers", () => {
  it("seeds defaults from the schema (booleans + JSON-encoded objects)", () => {
    const v = defaultValues(PARAMS);
    expect(v.test_size).toBe("0.2");
    expect(v.n_estimators).toBe("100");
    expect(v.strategy).toBe("mean");
    expect(v.shuffle).toBe(true);
    expect(v.columns).toBe("");
  });

  it("coerces + collects a valid parameter object (types honoured)", () => {
    const r = collect(PARAMS, {
      test_size: "0.25",
      n_estimators: "200",
      strategy: "median",
      shuffle: false,
      columns: '["a","b"]',
    });
    expect(r.ok).toBe(true);
    expect(r.parameters).toEqual({
      test_size: 0.25,
      n_estimators: 200,
      strategy: "median",
      shuffle: false,
      columns: ["a", "b"],
    });
  });

  it("errors on a missing required field and omits empty optionals", () => {
    const r = collect(PARAMS, { test_size: "", n_estimators: "", strategy: "", shuffle: true, columns: "" });
    expect(r.ok).toBe(false);
    expect(r.errors.test_size).toBeTruthy();
    expect(r.parameters).not.toHaveProperty("n_estimators");
  });

  it("enforces numeric min/max and enum membership", () => {
    const bad = collect(PARAMS, { test_size: "5", n_estimators: "0", strategy: "bogus", shuffle: true, columns: "" });
    expect(bad.errors.test_size).toBeTruthy(); // > max 1
    expect(bad.errors.n_estimators).toBeTruthy(); // < min 1
    expect(bad.errors.strategy).toBeTruthy(); // not in enum
  });

  it("rejects a non-integer where integer is required", () => {
    const bad = collect(PARAMS, { test_size: "0.2", n_estimators: "12.5", strategy: "mean", shuffle: true, columns: "" });
    expect(bad.errors.n_estimators).toBeTruthy();
  });

  it("rejects malformed JSON in array/object fields", () => {
    const bad = collect(PARAMS, { test_size: "0.2", n_estimators: "1", strategy: "mean", shuffle: true, columns: "[not json" });
    expect(bad.errors.columns).toBeTruthy();
  });

  it("collects data-aware formats by their storage shape (columns→array, column/expression→string)", () => {
    const params: PipelineStepParam[] = [
      { name: "features", type: "array", required: false, default: null, enumValues: null, min: null, max: null, help: null, format: "columns" },
      { name: "target", type: "string", required: true, default: null, enumValues: null, min: null, max: null, help: null, format: "column" },
      { name: "expr", type: "string", required: false, default: null, enumValues: null, min: null, max: null, help: null, format: "expression" },
      { name: "opts", type: "object", required: false, default: null, enumValues: null, min: null, max: null, help: null, format: "key_value" },
    ];
    const r = collect(params, {
      features: '["age","income"]',
      target: "label",
      expr: "col_a + col_b",
      opts: '{"k":"v"}',
    });
    expect(r.ok).toBe(true);
    expect(r.parameters).toEqual({
      features: ["age", "income"],
      target: "label",
      expr: "col_a + col_b",
      opts: { k: "v" },
    });
  });

  it("errors on malformed JSON in a columns/key_value format field", () => {
    const params: PipelineStepParam[] = [
      { name: "features", type: "array", required: true, default: null, enumValues: null, min: null, max: null, help: null, format: "columns" },
    ];
    expect(collect(params, { features: "[bad" }).errors.features).toBeTruthy();
  });

  it("treats a dataset_ref value as a plain string URN", () => {
    const params: PipelineStepParam[] = [
      { name: "dataset", type: "dataset_ref", required: true, default: null, enumValues: null, min: null, max: null, help: null },
    ];
    // Default is an empty string; a required + empty dataset_ref errors.
    expect(defaultValues(params).dataset).toBe("");
    expect(collect(params, { dataset: "" }).errors.dataset).toBeTruthy();

    // A picked URN passes straight through unchanged (no coercion).
    const ok = collect(params, { dataset: "wr:t-acme:dataset:dataset/ds-1" });
    expect(ok.ok).toBe(true);
    expect(ok.parameters.dataset).toBe("wr:t-acme:dataset:dataset/ds-1");
  });
});
