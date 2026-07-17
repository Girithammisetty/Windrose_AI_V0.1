import { describe, it, expect } from "vitest";
import {
  FRIENDLY_CHART_TYPES,
  FRIENDLY_CHART_NAMES,
  requiredEncodings,
  validateEncodings,
  measureColName,
  buildChartConfig,
  buildDisplayMeta,
  buildSources,
  buildChartSpec,
  validateHeatmapEncodings,
  buildHeatmapConfig,
  validateNetworkEncodings,
  buildNetworkConfig,
  buildSavedQuerySource,
  validateMetricSource,
  buildDatasetSource,
  type Encodings,
} from "./spec";

/**
 * Serialization is asserted against the chart-service config shapes (internal/
 * domain/shape.go + charttypes.go) so the spec we POST is one the backend
 * accepts. Fixture is a bar chart over `claims_core`: claim_type × claim_count.
 */
const CLAIMS_ENC: Encodings = { x: "claim_type", y: [{ measure: "claim_count", agg: "count" }] };

describe("friendly chart-type catalog", () => {
  it("offers all 27 query/dataset-class chart-service types (30 total minus the 3 dataClass=run types)", () => {
    expect(FRIENDLY_CHART_NAMES).toHaveLength(27);
    // every name is unique
    expect(new Set(FRIENDLY_CHART_NAMES).size).toBe(27);
    // the dataClass="run" types are deliberately excluded (see report: no
    // hand-authored path exists for them today).
    expect(FRIENDLY_CHART_NAMES).not.toContain("roc_curve");
    expect(FRIENDLY_CHART_NAMES).not.toContain("confusion_matrix");
    expect(FRIENDLY_CHART_NAMES).not.toContain("decision_tree");
    // spot-check a few from each family/group are present
    expect(FRIENDLY_CHART_NAMES).toEqual(
      expect.arrayContaining([
        "vertical_bar_chart",
        "vertical_stackedbar_chart",
        "scatter_plot",
        "bubble_chart",
        "line_chart",
        "pie_chart",
        "funnel_chart",
        "gauge_chart",
        "word_cloud_chart",
        "grid_chart",
        "pivot_table_chart",
        "heatmap_chart",
        "sunburst_chart",
        "sankey_chart",
        "tree_map_chart",
        "chord_chart",
        "network_chart",
        "network_graph_chart",
        "tree_chart",
        "decision_tree_chart",
        "metric_chart",
        "parameter_chart",
      ]),
    );
  });

  it("every entry has a non-empty group (drives the picker's <optgroup>)", () => {
    for (const f of FRIENDLY_CHART_TYPES) {
      expect(f.group.length).toBeGreaterThan(0);
    }
  });
});

describe("requiredEncodings (driven by family)", () => {
  it("axis needs x+y, y_only needs only y, grid needs x+y", () => {
    expect(requiredEncodings("axis")).toEqual({ x: true, y: true });
    expect(requiredEncodings("y_only")).toEqual({ x: false, y: true });
    expect(requiredEncodings("grid")).toEqual({ x: true, y: true });
  });
});

describe("validateEncodings", () => {
  it("flags a missing x for an axis chart", () => {
    const errs = validateEncodings("axis", { x: undefined, y: [{ measure: "claim_count", agg: "count" }] });
    expect(errs).toHaveLength(1);
    expect(errs[0].field).toBe("x");
  });

  it("flags a missing measure for every family", () => {
    expect(validateEncodings("y_only", { x: undefined, y: [] })[0].field).toBe("y");
    expect(validateEncodings("axis", { x: "claim_type", y: [] })[0].field).toBe("y");
  });

  it("passes a complete axis selection", () => {
    expect(validateEncodings("axis", CLAIMS_ENC)).toEqual([]);
  });
});

describe("measureColName (matches chart-service Shape column naming)", () => {
  it("prefixes the agg fn when present", () => {
    expect(measureColName("claim_count", "count")).toBe("count_claim_count");
    expect(measureColName("amount", "sum")).toBe("sum_amount");
    expect(measureColName("amount", null)).toBe("amount");
  });
});

describe("buildChartConfig per family", () => {
  it("axis (bar): x dimension + y measures with agg_fn", () => {
    expect(buildChartConfig("axis", CLAIMS_ENC)).toEqual({
      x: { dimension: "claim_type" },
      y: [{ measure: "claim_count", agg_fn: "count" }],
    });
  });

  it("y_only (pie): y only, with an optional x label", () => {
    expect(buildChartConfig("y_only", { x: undefined, y: [{ measure: "claim_count", agg: "count" }] })).toEqual({
      y: [{ measure: "claim_count", agg_fn: "count" }],
    });
    expect(buildChartConfig("y_only", CLAIMS_ENC)).toEqual({
      x: { dimension: "claim_type" },
      y: [{ measure: "claim_count", agg_fn: "count" }],
    });
  });

  it("grid: x + y + a pass-through `columns` list of shaped column names", () => {
    const cfg = buildChartConfig("grid", {
      x: "claim_type",
      y: [
        { measure: "claim_count", agg: "count" },
        { measure: "amount", agg: "sum" },
      ],
    });
    expect(cfg).toEqual({
      x: { dimension: "claim_type" },
      y: [
        { measure: "claim_count", agg_fn: "count" },
        { measure: "amount", agg_fn: "sum" },
      ],
      columns: ["claim_type", "count_claim_count", "sum_amount"],
    });
  });
});

describe("buildDisplayMeta + buildSources", () => {
  it("carries the semantic model + workspace in display_meta", () => {
    expect(buildDisplayMeta("claims_core", "ws-1")).toEqual({
      semantic_model: "claims_core",
      workspace_id: "ws-1",
    });
  });

  it("builds a semantic_measure source URN from the first measure + tenant", () => {
    expect(buildSources("claim_count", "acme")).toEqual([
      { position: 0, sourceType: "semantic_measure", sourceUrn: "wr:acme:semantic:measure/claim_count" },
    ]);
  });

  it("falls back to a best-effort tenant marker when tenant is unknown", () => {
    expect(buildSources("claim_count")[0].sourceUrn).toBe("wr:tenant:semantic:measure/claim_count");
  });
});

describe("buildChartSpec (full create-chart payload for a bar over claims_core)", () => {
  it("emits the exact config/displayMeta/sources the bff contract expects", () => {
    const spec = buildChartSpec({
      family: "axis",
      encodings: CLAIMS_ENC,
      modelName: "claims_core",
      workspaceId: "ws-1",
      tenantId: "acme",
      firstMeasure: "claim_count",
    });
    expect(spec).toEqual({
      config: {
        x: { dimension: "claim_type" },
        y: [{ measure: "claim_count", agg_fn: "count" }],
      },
      displayMeta: { semantic_model: "claims_core", workspace_id: "ws-1" },
      sources: [
        { position: 0, sourceType: "semantic_measure", sourceUrn: "wr:acme:semantic:measure/claim_count" },
      ],
    });
  });

  it("uses the first selected measure for the URN when the model's first measure is absent", () => {
    const spec = buildChartSpec({
      family: "axis",
      encodings: CLAIMS_ENC,
      modelName: "claims_core",
      workspaceId: "ws-1",
      tenantId: "acme",
    });
    expect(spec.sources[0].sourceUrn).toBe("wr:acme:semantic:measure/claim_count");
  });
});

describe("buildChartConfig: axis family's optional `dataseries` (stack-by / series-split)", () => {
  it("omits the key entirely when no series dimension is picked (unchanged wire shape)", () => {
    const cfg = buildChartConfig("axis", CLAIMS_ENC) as Record<string, unknown>;
    expect(Object.keys(cfg).sort()).toEqual(["x", "y"]);
  });

  it("adds a {dimension} ref under `dataseries` when one is picked — the SAME generic field every axis-family type reuses (stacked bar's stack-by, combination's per-series split, etc.); confirmed no per-type config fields exist in chart-service's charttypes.go", () => {
    const cfg = buildChartConfig("axis", { ...CLAIMS_ENC, dataseries: "vendor" });
    expect(cfg).toEqual({
      x: { dimension: "claim_type" },
      y: [{ measure: "claim_count", agg_fn: "count" }],
      dataseries: { dimension: "vendor" },
    });
  });
});

describe("heatmap family (sunburst/sankey/tree_map/heatmap/chord — x/y/dataseries are all dimensions)", () => {
  it("requires all three dimensions", () => {
    expect(validateHeatmapEncodings({})).toHaveLength(3);
    expect(validateHeatmapEncodings({ x: "a" })).toHaveLength(2);
    expect(validateHeatmapEncodings({ x: "a", y: "b", dataseries: "c" })).toEqual([]);
  });

  it("builds x/y/dataseries all as {dimension} refs (heatmap's `y` is a DIMENSION, not a measure)", () => {
    expect(buildHeatmapConfig({ x: "region", y: "product", dataseries: "segment" })).toEqual({
      x: { dimension: "region" },
      y: { dimension: "product" },
      dataseries: { dimension: "segment" },
    });
  });

  it("emits null for any missing field rather than omitting the key", () => {
    expect(buildHeatmapConfig({})).toEqual({ x: null, y: null, dataseries: null });
  });
});

describe("network family (network_chart/network_graph_chart/tree_chart/decision_tree_chart)", () => {
  it("requires nodes + children column labels (node_values is optional)", () => {
    expect(validateNetworkEncodings({})).toHaveLength(2);
    expect(validateNetworkEncodings({ nodes: "parent_id" })).toHaveLength(1);
    expect(validateNetworkEncodings({ nodes: "parent_id", children: "child_id" })).toEqual([]);
  });

  it("builds {nodes, children} plain strings, node_values only when set", () => {
    expect(buildNetworkConfig({ nodes: "parent_id", children: "child_id" })).toEqual({
      nodes: "parent_id",
      children: "child_id",
    });
    expect(buildNetworkConfig({ nodes: "parent_id", children: "child_id", nodeValues: "weight" })).toEqual({
      nodes: "parent_id",
      children: "child_id",
      node_values: "weight",
    });
  });

  it("sources a saved query (network configs have no y-measure, so chart-service can only resolve them over source_type=saved_query)", () => {
    expect(buildSavedQuerySource("wr:acme:query:saved/q1")).toEqual([
      { position: 0, sourceType: "saved_query", sourceUrn: "wr:acme:query:saved/q1" },
    ]);
  });
});

describe("metric family, dataClass=dataset (metric_chart/parameter_chart — no x/y, a dataset source only)", () => {
  it("requires a dataset URN", () => {
    expect(validateMetricSource(undefined)).toHaveLength(1);
    expect(validateMetricSource("")).toHaveLength(1);
    expect(validateMetricSource("wr:acme:dataset:dataset/d1")).toEqual([]);
  });

  it("sources a dataset (chart-service resolveArtifact fetches sources[0] directly, config is always {})", () => {
    expect(buildDatasetSource("wr:acme:dataset:dataset/d1")).toEqual([
      { position: 0, sourceType: "dataset", sourceUrn: "wr:acme:dataset:dataset/d1" },
    ]);
    expect(buildChartConfig("metric", { y: [] })).toEqual({});
  });
});
