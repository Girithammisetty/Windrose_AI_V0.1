import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { resolveKind, ChartView } from "./ChartView";
import { gridColumns, gridRows } from "./GridChart";
import { HeatmapChart } from "./HeatmapChart";
import { GaugeChart } from "./GaugeChart";
import { WordCloudChart } from "./WordCloudChart";
import { NetworkChart } from "./NetworkChart";
import { MetricChart } from "./MetricChart";

/** REAL-shaped grid data (chart-service ShapedResult): columns string[], rows any[][]. */
const COLUMNS = ["claim_type", "claim_count"];
const ROWS: unknown[][] = [
  ["auto", 9],
  ["property", 3],
  ["health", 2],
];

describe("ChartView.resolveKind (dispatch by chartType then family)", () => {
  it("maps the friendly catalog names to their renderer kind", () => {
    expect(resolveKind("vertical_bar_chart")).toBe("bar");
    expect(resolveKind("line_chart")).toBe("line");
    expect(resolveKind("pie_chart")).toBe("pie");
    expect(resolveKind("grid_chart")).toBe("grid");
  });

  it("falls back to family when the chartType is unknown", () => {
    expect(resolveKind(null, "axis")).toBe("bar");
    expect(resolveKind(undefined, "y_only")).toBe("pie");
    expect(resolveKind("", "grid")).toBe("grid");
  });

  it("defaults to a bar chart when neither is known", () => {
    expect(resolveKind(null, null)).toBe("bar");
  });

  it("maps every remaining axis-family type to the bar renderer (they all reuse BarChart's dataseries-pivot support)", () => {
    for (const ct of [
      "vertical_stackedbar_chart",
      "scatter_plot",
      "bubble_chart",
      "whisker_chart",
      "combination_chart",
      "geo_map_chart",
      "histogram_chart",
      "waterfall_chart",
      "funnel_chart",
    ]) {
      expect(resolveKind(ct)).toBe("bar");
    }
  });

  it("maps the new y_only-family bespoke renderers", () => {
    expect(resolveKind("gauge_chart")).toBe("gauge");
    expect(resolveKind("word_cloud_chart")).toBe("wordcloud");
  });

  it("maps grid_chart + pivot_table_chart, and the heatmap-family tabular fallback types, to the grid renderer", () => {
    expect(resolveKind("pivot_table_chart")).toBe("grid");
    for (const ct of ["sunburst_chart", "sankey_chart", "tree_map_chart", "chord_chart"]) {
      expect(resolveKind(ct)).toBe("grid");
    }
  });

  it("maps heatmap_chart to the bespoke heatmap renderer", () => {
    expect(resolveKind("heatmap_chart")).toBe("heatmap");
  });

  it("maps every network-family type to the network renderer", () => {
    for (const ct of ["network_chart", "network_graph_chart", "tree_chart", "decision_tree_chart"]) {
      expect(resolveKind(ct)).toBe("network");
    }
  });

  it("maps the metric-family dataset types to the metric renderer", () => {
    expect(resolveKind("metric_chart")).toBe("metric");
    expect(resolveKind("parameter_chart")).toBe("metric");
  });

  it("falls back to family for heatmap/network/metric when chartType is unknown", () => {
    expect(resolveKind(null, "heatmap")).toBe("grid");
    expect(resolveKind(null, "network")).toBe("network");
    expect(resolveKind(null, "metric")).toBe("metric");
  });
});

describe("HeatmapChart (bespoke SVG grid renderer)", () => {
  it("renders a cell per (x,y) row with a value tooltip", () => {
    const { container } = render(
      <HeatmapChart columns={["region", "product", "value"]} rows={[["EMEA", "auto", 5], ["APAC", "home", 9]]} />,
    );
    expect(container.querySelectorAll("rect")).toHaveLength(2);
    expect(container.textContent).toContain("EMEA");
  });

  it("shows an empty state for no rows", () => {
    render(<HeatmapChart columns={[]} rows={[]} />);
    expect(screen.getByText(/no values to plot/i)).toBeInTheDocument();
  });
});

describe("GaugeChart (single-measure arc gauge)", () => {
  it("prints the exact value", () => {
    render(<GaugeChart columns={["label", "value"]} rows={[["conversion", 42]]} />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders the real no-x gauge_chart preview shape correctly (117503.15, not 0 — see geometry.test.ts)", () => {
    render(<GaugeChart columns={["label", "sum_total_amount"]} rows={[[117503.15]]} />);
    expect(screen.getByText("117.5k")).toBeInTheDocument();
    expect(screen.getByText("sum_total_amount")).toBeInTheDocument();
  });
});

describe("WordCloudChart (real values → real font sizes, no layout dependency)", () => {
  it("renders one word per category, sized by its value", () => {
    render(
      <WordCloudChart
        columns={["term", "count"]}
        rows={[["claim", 40], ["denied", 5]]}
      />,
    );
    const claim = screen.getByText("claim");
    const denied = screen.getByText("denied");
    const claimSize = parseFloat(claim.style.fontSize);
    const deniedSize = parseFloat(denied.style.fontSize);
    expect(claimSize).toBeGreaterThan(deniedSize);
  });
});

describe("NetworkChart (honest real-data-or-explicit-gap renderer)", () => {
  it("renders a real edge list when rows are present", () => {
    render(<NetworkChart rows={[["root", "child-a", 3]]} />);
    expect(screen.getByText("root")).toBeInTheDocument();
    expect(screen.getByText("child-a")).toBeInTheDocument();
  });

  it("shows an explanatory (non-mock) status when the API returns no rows — chart-service's `graph` shape isn't exposed by chartPreview yet", () => {
    render(<NetworkChart rows={[]} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

describe("ChartView end-to-end with a REAL chartPreview response", () => {
  // Captured verbatim from a live `chartPreview` call against a running
  // chart-service + semantic-service (vertical_stackedbar_chart, x=claim_type,
  // y=[count claim_count], dataseries=vendor over the real claims_core model)
  // — see the chart-builder report for the full curl transcript. Proves the
  // dataseries-pivot path (geometry.ts's toChartModel) renders real
  // production-shaped [x, series, value] rows, not just synthetic fixtures.
  const REAL_COLUMNS = ["claim_type", "vendor", "count_claim_count"];
  const REAL_ROWS: unknown[][] = [
    ["property", "RapidDry Restoration", 2],
    ["health", "MercyCare Clinic", 2],
    ["property", "StormShield Roofing", 1],
    ["auto", "ACME Auto Body", 5],
    ["auto", "Bayview Collision", 3],
    ["auto", "Precision Auto Glass", 1],
  ];

  it("renders the real stacked-bar preview via BarChart with every category and series present", () => {
    const { container } = render(
      <ChartView chartType="vertical_stackedbar_chart" family="axis" columns={REAL_COLUMNS} rows={REAL_ROWS} />,
    );
    // 3 categories (auto/property/health) × up to 6 series bars.
    expect(container.querySelectorAll("rect").length).toBeGreaterThan(0);
    expect(container.textContent).toContain("auto");
    expect(container.textContent).toContain("property");
    expect(container.textContent).toContain("health");
  });
});

describe("MetricChart (honest real-data-or-explicit-gap renderer)", () => {
  it("shows an explanatory (non-mock) status when neither artifact metrics nor rows are present", () => {
    render(<MetricChart columns={[]} rows={[]} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders the dataset_summary artifact's key/value metrics (chart-service ShapedResult.artifact)", () => {
    render(
      <MetricChart
        columns={[]}
        rows={[]}
        artifact={{
          kind: "dataset_summary",
          metrics: [
            { label: "Rows", value: 100 },
            { label: "Columns", value: 3 },
            { label: "Completeness %", value: 85 },
          ],
        }}
      />,
    );
    expect(screen.getByText("Rows")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("Columns")).toBeInTheDocument();
    expect(screen.getByText("Completeness %")).toBeInTheDocument();
    expect(screen.getByText("85")).toBeInTheDocument();
    // Artifact metrics render as an explicit success, NOT the gap state.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("falls back to real key/value pairs from tabular rows when no artifact is given", () => {
    render(<MetricChart columns={["metric", "value"]} rows={[["accuracy", 0.91]]} />);
    expect(screen.getByText("accuracy")).toBeInTheDocument();
    expect(screen.getByText("0.91")).toBeInTheDocument();
  });
});

describe("GridChart column/row pass-through", () => {
  it("maps every shaped column name to a DataTable column header, in order", () => {
    const cols = gridColumns(COLUMNS);
    expect(cols).toHaveLength(2);
    expect(cols.map((c) => c.header)).toEqual(["claim_type", "claim_count"]);
    expect(cols.map((c) => c.id)).toEqual(["c0", "c1"]);
  });

  it("passes shaped rows straight through as id-carrying cells", () => {
    const rows = gridRows(ROWS);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toEqual({ __id: "0", cells: ["auto", 9] });
    expect(rows[2]).toEqual({ __id: "2", cells: ["health", 2] });
    // cells are the raw shaped values, untouched
    expect(rows.map((r) => r.cells)).toEqual(ROWS);
  });
});
