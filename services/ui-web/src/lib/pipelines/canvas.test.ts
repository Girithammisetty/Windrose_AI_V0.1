import { describe, it, expect } from "vitest";
import {
  nodeFromStep,
  serializeDefinition,
  hydrateFromDefinition,
  valuesFromParameters,
  collectRunParameters,
  canConnect,
  aliasMap,
  type CanvasEdge,
} from "./canvas";
import type { PipelineStepType } from "@/lib/graphql/types";

/**
 * Fixtures use the REAL pipeline-orchestrator catalog names + ports
 * (services/pipeline-orchestrator/app/domain/catalog.py) so serialization is
 * asserted against shapes dag.py actually accepts — no fabricated components.
 */

// IO source: read-from-warehouse — 0 inputs, 1 output "out" (dataframe).
const READ: PipelineStepType = {
  name: "read-from-warehouse",
  displayName: "Read From Warehouse",
  category: "io",
  description: null,
  minInputs: 0,
  maxInputs: 0,
  maxOutputs: 1,
  outputs: [{ name: "out", type: "dataframe" }],
  parameters: [{ name: "dataset", type: "string", required: true, default: null, enumValues: null, min: null, max: null, help: null }],
};

// IO sink: write-to-warehouse — 1 input, ZERO outputs, max_outputs 0.
const WRITE: PipelineStepType = {
  name: "write-to-warehouse",
  displayName: "Write To Warehouse",
  category: "io",
  description: null,
  minInputs: 1,
  maxInputs: 1,
  maxOutputs: 0,
  outputs: [],
  parameters: [{ name: "output_dataset_name", type: "string", required: true, default: null, enumValues: null, min: null, max: null, help: null }],
};

// Data-prep transform: filter-data — 1 in / 1 out (dataframe).
const FILTER: PipelineStepType = {
  name: "filter-data",
  displayName: "Filter Data",
  category: "data_prep",
  description: null,
  minInputs: 1,
  maxInputs: 1,
  maxOutputs: 1,
  outputs: [{ name: "out", type: "dataframe" }],
  parameters: [{ name: "expression", type: "string", required: true, default: null, enumValues: null, min: null, max: null, help: null }],
};

// Data-prep fan-out: split-data — 1 in / 2 outs, max_outputs 2.
const SPLIT: PipelineStepType = {
  name: "split-data",
  displayName: "Split Data",
  category: "data_prep",
  description: null,
  minInputs: 1,
  maxInputs: 1,
  maxOutputs: 2,
  outputs: [
    { name: "train", type: "dataframe" },
    { name: "test", type: "dataframe" },
  ],
  parameters: [{ name: "split_size", type: "number", required: true, default: 0.8, enumValues: null, min: 0, max: 1, help: null }],
};

// Data-prep wide sink: merge-data — 2..8 in / 1 out.
const MERGE: PipelineStepType = {
  name: "merge-data",
  displayName: "Merge Data",
  category: "data_prep",
  description: null,
  minInputs: 2,
  maxInputs: 8,
  maxOutputs: 1,
  outputs: [{ name: "out", type: "dataframe" }],
  parameters: [],
};

// Algorithm train component: xgboost-train — 1..2 in / 1 model out.
const XGB: PipelineStepType = {
  name: "xgboost-train",
  displayName: "XGBoost",
  category: "algorithm",
  description: null,
  minInputs: 1,
  maxInputs: 2,
  maxOutputs: 1,
  outputs: [{ name: "model", type: "model" }],
  parameters: [{ name: "n_estimators", type: "integer", required: false, default: 200, enumValues: null, min: 1, max: 2000, help: null }],
};

describe("canvas serialization (real catalog components)", () => {
  it("serializes a read→write data_prep graph to a dag.py-valid shape (sink has zero outputs)", () => {
    const read = nodeFromStep(READ, { x: 0, y: 0 });
    const write = nodeFromStep(WRITE, { x: 300, y: 0 });
    read.values.dataset = "wr:acme:dataset:claims";
    write.values.output_dataset_name = "claims_clean";

    const edges: CanvasEdge[] = [
      { id: "e1", from: { nodeId: read.id, port: "out", type: "dataframe" }, to: { nodeId: write.id, port: "in0" } },
    ];

    const { definition, ok } = serializeDefinition([read, write], edges);
    expect(ok).toBe(true);
    expect(definition.nodes).toEqual([
      {
        alias: "read_from_warehouse_0",
        component: "read-from-warehouse",
        parameters: { dataset: "wr:acme:dataset:claims" },
        outputs: [{ name: "out", type: "dataframe" }],
      },
      {
        alias: "write_to_warehouse_0",
        component: "write-to-warehouse",
        parameters: { output_dataset_name: "claims_clean" },
        outputs: [], // sink: no phantom output
      },
    ]);
    expect(definition.edges).toEqual([
      { from: "read_from_warehouse_0.out", to: "write_to_warehouse_0.in0", type: "dataframe" },
    ]);
  });

  it("serializes a read→xgboost-train training graph with the real `<name>-train` component", () => {
    const read = nodeFromStep(READ, { x: 0, y: 0 });
    const xgb = nodeFromStep(XGB, { x: 300, y: 0 });
    read.values.dataset = "wr:acme:dataset:claims";

    const edges: CanvasEdge[] = [
      { id: "e1", from: { nodeId: read.id, port: "out", type: "dataframe" }, to: { nodeId: xgb.id, port: "in0" } },
    ];

    const { definition, ok } = serializeDefinition([read, xgb], edges);
    expect(ok).toBe(true);
    expect(definition.nodes[1]).toEqual({
      alias: "xgboost_train_0",
      component: "xgboost-train",
      parameters: { n_estimators: 200 }, // default applied
      outputs: [{ name: "model", type: "model" }],
    });
    expect(definition.edges).toEqual([
      { from: "read_from_warehouse_0.out", to: "xgboost_train_0.in0", type: "dataframe" },
    ]);
  });

  it("reports per-node param errors when a required param is blank", () => {
    const read = nodeFromStep(READ, { x: 0, y: 0 }); // dataset required, left blank
    const { ok, paramErrors } = serializeDefinition([read], []);
    expect(ok).toBe(false);
    expect(paramErrors[read.id].dataset).toBeTruthy();
  });

  it("gives duplicate components distinct running-index aliases", () => {
    const a = nodeFromStep(READ, { x: 0, y: 0 });
    const b = nodeFromStep(READ, { x: 0, y: 0 });
    const aliases = aliasMap([a, b]);
    expect(aliases.get(a.id)).toBe("read_from_warehouse_0");
    expect(aliases.get(b.id)).toBe("read_from_warehouse_1");
  });
});

describe("canvas rehydration from a saved definition (builder edit mode)", () => {
  const CATALOG: PipelineStepType[] = [READ, WRITE, FILTER, SPLIT, MERGE, XGB];

  it("round-trips a serialized definition back to an equivalent canvas", () => {
    const read = nodeFromStep(READ, { x: 0, y: 0 });
    const filter = nodeFromStep(FILTER, { x: 300, y: 0 });
    const xgb = nodeFromStep(XGB, { x: 600, y: 0 });
    read.values.dataset = "wr:acme:dataset:claims";
    filter.values.expression = "amount > 0";
    xgb.values.n_estimators = "500"; // non-default, user-edited

    const edges: CanvasEdge[] = [
      { id: "e1", from: { nodeId: read.id, port: "out", type: "dataframe" }, to: { nodeId: filter.id, port: "in0" } },
      { id: "e2", from: { nodeId: filter.id, port: "out", type: "dataframe" }, to: { nodeId: xgb.id, port: "in0" } },
    ];
    const original = serializeDefinition([read, filter, xgb], edges).definition;

    // Rehydrate the saved definition, then re-serialize: the DAG must be identical.
    const { nodes, edges: hydEdges } = hydrateFromDefinition(original, CATALOG);
    expect(nodes).toHaveLength(3);
    const reserialized = serializeDefinition(nodes, hydEdges).definition;
    expect(reserialized).toEqual(original);
  });

  it("preserves a component missing from the current catalog structurally", () => {
    const definition = {
      nodes: [
        { alias: "read_from_warehouse_0", component: "read-from-warehouse", parameters: { dataset: "wr:x" }, outputs: [{ name: "out", type: "dataframe" }] },
        { alias: "custom_op_0", component: "custom-op", parameters: { k: "v" }, outputs: [{ name: "out", type: "dataframe" }] },
      ],
      edges: [{ from: "read_from_warehouse_0.out", to: "custom_op_0.in0", type: "dataframe" }],
    };
    const { nodes, edges } = hydrateFromDefinition(definition, CATALOG);
    expect(nodes).toHaveLength(2);
    expect(nodes[1].component).toBe("custom-op");
    expect(nodes[1].outputs).toEqual([{ name: "out", type: "dataframe" }]);
    // The edge survives because the unknown node still gets input slots + an id.
    expect(edges).toHaveLength(1);
  });

  it("valuesFromParameters inverts collect: JSON widgets re-stringify, defaults fill omissions", () => {
    const params = XGB.parameters; // n_estimators integer, default 200
    // Value present -> string form; omitted optional -> schema default.
    expect(valuesFromParameters(params, { n_estimators: 500 })).toEqual({ n_estimators: "500" });
    expect(valuesFromParameters(params, {})).toEqual({ n_estimators: "200" });
  });
});

describe("canvas edge legality (typed input ports)", () => {
  it("accepts a dataframe→dataframe connection", () => {
    const read = nodeFromStep(READ, { x: 0, y: 0 });
    const filter = nodeFromStep(FILTER, { x: 300, y: 0 });
    const res = canConnect(
      { nodeId: read.id, port: "out", type: "dataframe" },
      { nodeId: filter.id, port: "in0" },
      [read, filter],
      [],
    );
    expect(res.ok).toBe(true);
  });

  it("rejects a model→dataframe connection (input ports default to dataframe)", () => {
    const xgb = nodeFromStep(XGB, { x: 0, y: 0 });
    const write = nodeFromStep(WRITE, { x: 300, y: 0 });
    const res = canConnect(
      { nodeId: xgb.id, port: "model", type: "model" },
      { nodeId: write.id, port: "in0" },
      [xgb, write],
      [],
    );
    expect(res.ok).toBe(false);
    expect(res.reason).toMatch(/type mismatch/i);
  });

  it("rejects self-connections", () => {
    const filter = nodeFromStep(FILTER, { x: 0, y: 0 });
    const res = canConnect(
      { nodeId: filter.id, port: "out", type: "dataframe" },
      { nodeId: filter.id, port: "in0" },
      [filter],
      [],
    );
    expect(res.ok).toBe(false);
  });

  it("rejects a target input that is already connected", () => {
    const read = nodeFromStep(READ, { x: 0, y: 0 });
    const filter = nodeFromStep(FILTER, { x: 0, y: 0 });
    const split = nodeFromStep(SPLIT, { x: 0, y: 0 });
    const edges: CanvasEdge[] = [
      { id: "e1", from: { nodeId: read.id, port: "out", type: "dataframe" }, to: { nodeId: filter.id, port: "in0" } },
    ];
    const res = canConnect(
      { nodeId: split.id, port: "train", type: "dataframe" },
      { nodeId: filter.id, port: "in0" },
      [read, filter, split],
      edges,
    );
    expect(res.ok).toBe(false);
    expect(res.reason).toMatch(/already connected/i);
  });

  it("enforces the source maxOutputs fan-out limit", () => {
    // split-data has max_outputs 2: wire two edges into a wide merge sink, then a
    // third (to a still-free input slot) must fail on the source fan-out guard.
    const split = nodeFromStep(SPLIT, { x: 0, y: 0 });
    const merge = nodeFromStep(MERGE, { x: 300, y: 0 });
    const nodes = [split, merge];
    const edges: CanvasEdge[] = [
      { id: "e1", from: { nodeId: split.id, port: "train", type: "dataframe" }, to: { nodeId: merge.id, port: "in0" } },
      { id: "e2", from: { nodeId: split.id, port: "test", type: "dataframe" }, to: { nodeId: merge.id, port: "in1" } },
    ];
    const third = canConnect(
      { nodeId: split.id, port: "train", type: "dataframe" },
      { nodeId: merge.id, port: "in2" },
      nodes,
      edges,
    );
    expect(third.ok).toBe(false);
    expect(third.reason).toMatch(/output limit/i);
  });
});

// A read-from-warehouse whose `dataset` param is a real dataset_ref (matches the
// catalog's read component). Its value is a dataset URN.
const READ_REF: PipelineStepType = {
  ...READ,
  parameters: [{ name: "dataset", type: "dataset_ref", required: true, default: null, enumValues: null, min: null, max: null, help: null }],
};

describe("collectRunParameters", () => {
  it("threads the picked dataset_ref URN through, keyed by param name", () => {
    const read = nodeFromStep(READ_REF, { x: 0, y: 0 });
    read.values.dataset = "wr:t-acme:dataset:dataset/ds-9";
    const filter = nodeFromStep(FILTER, { x: 300, y: 0 });
    filter.values.expression = "amount > 0";

    // Only dataset_ref values surface — the filter's string param is ignored.
    expect(collectRunParameters([read, filter])).toEqual({
      dataset: "wr:t-acme:dataset:dataset/ds-9",
    });
  });

  it("omits an unset dataset_ref (no empty URN in run parameters)", () => {
    const read = nodeFromStep(READ_REF, { x: 0, y: 0 });
    expect(collectRunParameters([read])).toEqual({});
  });
});
