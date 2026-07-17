import { describe, it, expect } from "vitest";
import {
  toChartModel,
  toNumber,
  maxValue,
  scaleToHeight,
  pieSlices,
  arcPath,
  linePoints,
  paletteColor,
  toHeatmapModel,
} from "./geometry";

/**
 * Uses REAL-shaped chart data (chart-service ShapedResult): columns are a
 * string[], rows are any[][]. Fixture is the claims-by-type aggregate:
 *   columns = ["claim_type", "claim_count"]
 *   rows    = [["auto", 9], ["property", 3], ["health", 2]]
 */
const COLUMNS = ["claim_type", "claim_count"];
const ROWS: unknown[][] = [
  ["auto", 9],
  ["property", 3],
  ["health", 2],
];

describe("toChartModel (shaped columns/rows → categories + series)", () => {
  it("splits col[0] as the category axis and col[1..] as numeric series", () => {
    const model = toChartModel(COLUMNS, ROWS);
    expect(model.categories).toEqual(["auto", "property", "health"]);
    expect(model.series).toHaveLength(1);
    expect(model.series[0]).toEqual({ name: "claim_count", values: [9, 3, 2] });
  });

  it("coerces non-numeric cells to 0 and stringifies labels", () => {
    const model = toChartModel(["k", "v"], [["a", "x"], [1, "5"]]);
    expect(model.categories).toEqual(["a", "1"]);
    expect(model.series[0].values).toEqual([0, 5]);
  });

  it("supports a two-series axis shape (x + two measures)", () => {
    const model = toChartModel(
      ["month", "open", "closed"],
      [["jan", 5, 2], ["feb", 6, 4]],
    );
    expect(model.series.map((s) => s.name)).toEqual(["open", "closed"]);
    expect(model.series[1].values).toEqual([2, 4]);
    expect(maxValue(model)).toBe(6);
  });

  it("pivots a [x, series, value] long-format shape (dataseries set — stacked bar / combination) into wide series", () => {
    // chart-service's Shape() emits this 3-column order when an axis config
    // sets `dataseries`: [x_dim, series_dim, measure]. col[1] is categorical
    // (a dimension value), not numeric, which is how the pivot is detected.
    const model = toChartModel(
      ["region", "product_line", "sum_revenue"],
      [
        ["EMEA", "auto", 100],
        ["EMEA", "home", 40],
        ["APAC", "auto", 80],
        ["APAC", "home", 10],
      ],
    );
    expect(model.categories).toEqual(["EMEA", "APAC"]);
    expect(model.series.map((s) => s.name)).toEqual(["auto", "home"]);
    expect(model.series[0].values).toEqual([100, 80]); // "auto" per region
    expect(model.series[1].values).toEqual([40, 10]); // "home" per region
  });

  it("pads a pivoted series with 0 when a (category, series) combination is missing", () => {
    const model = toChartModel(
      ["region", "product_line", "sum_revenue"],
      [
        ["EMEA", "auto", 100],
        ["APAC", "home", 10], // no APAC/auto or EMEA/home rows
      ],
    );
    expect(model.categories).toEqual(["EMEA", "APAC"]);
    const auto = model.series.find((s) => s.name === "auto")!;
    const home = model.series.find((s) => s.name === "home")!;
    expect(auto.values).toEqual([100, 0]);
    expect(home.values).toEqual([0, 10]);
  });

  it("does NOT pivot a 3-column WIDE shape (col[1] is numeric — two measures, not a series split)", () => {
    const model = toChartModel(["x", "a", "b"], [["cat1", 1, 2], ["cat2", 3, 4]]);
    expect(model.series.map((s) => s.name)).toEqual(["a", "b"]);
    expect(model.categories).toEqual(["cat1", "cat2"]);
  });

  it("reads a y_only single-measure, no-x preview correctly (chart-service declares 2 column names but each row is really ONE cell)", () => {
    // Captured verbatim from a live gauge_chart preview (sum of total_amount,
    // no x dimension picked) — see the chart-builder report.
    const model = toChartModel(["label", "sum_total_amount"], [[117503.15]]);
    expect(model.series).toHaveLength(1);
    expect(model.series[0]).toEqual({ name: "sum_total_amount", values: [117503.15] });
    expect(model.categories).toEqual(["sum_total_amount"]);
  });
});

describe("toHeatmapModel (shaped [x, y, value] rows → grid cells)", () => {
  it("builds distinct x/y category lists and one cell per row", () => {
    const model = toHeatmapModel(
      ["region", "product", "value"],
      [
        ["EMEA", "auto", 5],
        ["EMEA", "home", 2],
        ["APAC", "auto", 9],
      ],
    );
    expect(model.xCategories).toEqual(["EMEA", "APAC"]);
    expect(model.yCategories).toEqual(["auto", "home"]);
    expect(model.cells).toHaveLength(3);
    expect(model.max).toBe(9);
    const emeaAuto = model.cells.find((c) => c.xi === 0 && c.yi === 0)!;
    expect(emeaAuto.value).toBe(5);
  });

  it("returns an empty model for no rows", () => {
    const model = toHeatmapModel([], []);
    expect(model.cells).toEqual([]);
    expect(model.max).toBe(0);
  });
});

describe("toNumber", () => {
  it("passes finite numbers, parses numeric strings, zeroes the rest", () => {
    expect(toNumber(9)).toBe(9);
    expect(toNumber("3")).toBe(3);
    expect(toNumber("nope")).toBe(0);
    expect(toNumber(null)).toBe(0);
    expect(toNumber(Infinity)).toBe(0);
  });
});

describe("scaleToHeight (bar heights proportional to values)", () => {
  it("maps the max value to the full plot height and scales the rest linearly", () => {
    const heights = scaleToHeight([9, 3, 2], 90);
    expect(heights[0]).toBeCloseTo(90); // 9 is the max → full height
    expect(heights[1]).toBeCloseTo(30); // 3/9 * 90
    expect(heights[2]).toBeCloseTo(20); // 2/9 * 90
    // strictly proportional: ratio of heights == ratio of values
    expect(heights[1] / heights[0]).toBeCloseTo(3 / 9);
    expect(heights[2] / heights[0]).toBeCloseTo(2 / 9);
  });

  it("returns all-zero heights for an empty or all-zero set", () => {
    expect(scaleToHeight([], 100)).toEqual([]);
    expect(scaleToHeight([0, 0], 100)).toEqual([0, 0]);
  });
});

describe("pieSlices (slice angles sum to 360)", () => {
  it("assigns each slice a sweep proportional to its value, summing to 360°", () => {
    const slices = pieSlices([9, 3, 2]); // total 14
    expect(slices).toHaveLength(3);
    const sweeps = slices.map((s) => s.endAngle - s.startAngle);
    expect(sweeps[0]).toBeCloseTo((9 / 14) * 360);
    expect(sweeps[1]).toBeCloseTo((3 / 14) * 360);
    expect(sweeps[2]).toBeCloseTo((2 / 14) * 360);
    // the sweeps sum to a full circle and the arcs are contiguous
    expect(sweeps.reduce((a, b) => a + b, 0)).toBeCloseTo(360);
    expect(slices[0].startAngle).toBe(0);
    expect(slices[2].endAngle).toBeCloseTo(360);
    expect(slices[0].fraction).toBeCloseTo(9 / 14);
  });

  it("yields no slices for an empty/all-zero set", () => {
    expect(pieSlices([])).toEqual([]);
    expect(pieSlices([0, 0])).toEqual([]);
  });
});

describe("arcPath / linePoints geometry", () => {
  it("produces a closed wedge path from the centre", () => {
    const d = arcPath(100, 100, 90, 0, 120);
    expect(d.startsWith("M 100 100")).toBe(true);
    expect(d.endsWith("Z")).toBe(true);
    expect(d).toContain("A 90 90");
  });

  it("spaces line points evenly across the plot width and scales y to the max", () => {
    const pts = linePoints([9, 3, 2], { plotWidth: 100, plotHeight: 100, max: 9 });
    expect(pts.map((p) => p.x)).toEqual([0, 50, 100]);
    expect(pts[0].y).toBeCloseTo(0); // max value sits at the top
    expect(pts[1].y).toBeCloseTo(100 - (3 / 9) * 100);
  });
});

describe("paletteColor", () => {
  it("wraps around the categorical palette", () => {
    expect(paletteColor(0)).toBe(paletteColor(6));
    expect(paletteColor(0)).not.toBe(paletteColor(1));
  });
});
