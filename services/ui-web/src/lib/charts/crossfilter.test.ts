import { describe, it, expect } from "vitest";
import {
  crossFilterField,
  selectedValueFor,
  toggleCrossFilter,
  toFilterVars,
  type CrossFilter,
} from "./crossfilter";

describe("crossFilterField", () => {
  it("prefers config.x.dimension", () => {
    expect(crossFilterField({ x: { dimension: "region" } }, ["region", "amount"])).toBe("region");
  });
  it("accepts a bare config.dimension", () => {
    expect(crossFilterField({ dimension: "state" }, null)).toBe("state");
  });
  it("falls back to the first shaped column", () => {
    expect(crossFilterField(null, ["carrier", "paid"])).toBe("carrier");
  });
  it("returns null when neither is available", () => {
    expect(crossFilterField(null, [])).toBeNull();
    expect(crossFilterField({}, undefined)).toBeNull();
  });
});

describe("toggleCrossFilter", () => {
  it("adds a predicate for a new origin", () => {
    const out = toggleCrossFilter([], "chartA", "region", "West");
    expect(out).toEqual([{ field: "region", op: "eq", value: "West", origin: "chartA" }]);
  });
  it("clears when the same value is re-clicked (toggle off)", () => {
    const base: CrossFilter[] = [{ field: "region", op: "eq", value: "West", origin: "chartA" }];
    expect(toggleCrossFilter(base, "chartA", "region", "West")).toEqual([]);
  });
  it("replaces the predicate when a different value on the same origin is clicked", () => {
    const base: CrossFilter[] = [{ field: "region", op: "eq", value: "West", origin: "chartA" }];
    expect(toggleCrossFilter(base, "chartA", "region", "East")).toEqual([
      { field: "region", op: "eq", value: "East", origin: "chartA" },
    ]);
  });
  it("keeps predicates from other origins independent", () => {
    const base: CrossFilter[] = [{ field: "region", op: "eq", value: "West", origin: "chartA" }];
    const out = toggleCrossFilter(base, "chartB", "carrier", "Acme");
    expect(out).toHaveLength(2);
    expect(out).toContainEqual({ field: "carrier", op: "eq", value: "Acme", origin: "chartB" });
  });
});

describe("selectedValueFor", () => {
  const filters: CrossFilter[] = [{ field: "region", op: "eq", value: "West", origin: "chartA" }];
  it("returns the active value for the origin", () => {
    expect(selectedValueFor(filters, "chartA")).toBe("West");
  });
  it("returns null for an origin with no selection", () => {
    expect(selectedValueFor(filters, "chartB")).toBeNull();
  });
});

describe("toFilterVars", () => {
  it("maps to the wire shape", () => {
    const filters: CrossFilter[] = [{ field: "region", op: "eq", value: "West", origin: "chartA" }];
    expect(toFilterVars(filters)).toEqual([{ field: "region", op: "eq", value: "West", origin: "chartA" }]);
  });
  it("returns undefined for an empty selection (omits the variable)", () => {
    expect(toFilterVars([])).toBeUndefined();
  });
});
