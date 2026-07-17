import { describe, it, expect } from "vitest";
import {
  DIM_TYPES,
  TIME_GRAINS,
  AGG_FNS,
  JOIN_TYPES,
  CARDINALITIES,
  EXPR_FUNCS,
  isValidName,
  emptyDefinition,
  isDefinitionEmpty,
  newEntity,
  newDimension,
  newMeasure,
  newJoinPath,
  parseValidationDetails,
  groupProblemsByObject,
  datasetIdFromUrn,
} from "./definition";

describe("semantic-service vocabulary constants (must mirror app/domain/definition.py + expr.py exactly)", () => {
  it("dimension types match DIM_TYPES", () => {
    expect(DIM_TYPES).toEqual(["categorical", "time", "numeric", "boolean", "geo"]);
  });
  it("time grains match TIME_GRAINS", () => {
    expect(TIME_GRAINS).toEqual(["hour", "day", "week", "month", "quarter", "year"]);
  });
  it("aggregations match AGG_WHITELIST", () => {
    expect(AGG_FNS).toEqual(["sum", "avg", "min", "max", "count", "count_distinct", "first"]);
  });
  it("join types + cardinalities match JOIN_TYPES/CARDINALITIES", () => {
    expect(JOIN_TYPES).toEqual(["left", "inner"]);
    expect(CARDINALITIES).toEqual(["many_to_one", "one_to_one"]);
  });
  it("expr funcs match the FUNC_WHITELIST", () => {
    expect(EXPR_FUNCS).toEqual([
      "coalesce", "nullif", "cast", "date_trunc", "extract",
      "lower", "upper", "trim", "concat", "abs", "round",
    ]);
  });
});

describe("isValidName", () => {
  it("accepts lowercase snake_case starting with a letter", () => {
    expect(isValidName("claim_type")).toBe(true);
    expect(isValidName("a")).toBe(true);
    expect(isValidName("claims2")).toBe(true);
  });
  it("rejects names the backend's NAME_RE would reject", () => {
    expect(isValidName("")).toBe(false);
    expect(isValidName("2claims")).toBe(false);
    expect(isValidName("Claim_Type")).toBe(false);
    expect(isValidName("claim-type")).toBe(false);
    expect(isValidName("claim type")).toBe(false);
  });
});

describe("stub builders", () => {
  it("emptyDefinition has all four empty arrays", () => {
    expect(emptyDefinition()).toEqual({ entities: [], dimensions: [], measures: [], join_paths: [] });
  });
  it("isDefinitionEmpty", () => {
    expect(isDefinitionEmpty(null)).toBe(true);
    expect(isDefinitionEmpty(emptyDefinition())).toBe(true);
    expect(isDefinitionEmpty({ ...emptyDefinition(), entities: [newEntity()] })).toBe(false);
  });
  it("newEntity defaults to a latest-policy stub", () => {
    expect(newEntity()).toEqual({
      name: "", dataset_urn: "", table: "", primary_key: [],
      dataset_version_policy: { policy: "latest" },
    });
  });
  it("newDimension seeds the owning entity and defaults to categorical", () => {
    expect(newDimension("claims")).toMatchObject({ entity: "claims", type: "categorical", column: "" });
  });
  it("newMeasure defaults to count (the only agg that may omit expr)", () => {
    expect(newMeasure("claims")).toMatchObject({ entity: "claims", agg: "count" });
  });
  it("newJoinPath seeds one empty on-clause pair", () => {
    const jp = newJoinPath();
    expect(jp.on).toEqual([{ from_column: "", to_column: "" }]);
    expect(jp.join_type).toBe("left");
    expect(jp.cardinality).toBe("many_to_one");
  });
});

describe("parseValidationDetails (submit-time full-validation [{object,problem}] list)", () => {
  it("splits 'kind/name' objects", () => {
    const problems = parseValidationDetails([
      { object: "dimension/bogus_dim", problem: "column 'nope' not in dataset schema of entity 'claims'" },
      { object: "measure/claim_count", problem: "name collision" },
    ]);
    expect(problems).toEqual([
      { kind: "dimension", name: "bogus_dim", problem: "column 'nope' not in dataset schema of entity 'claims'" },
      { kind: "measure", name: "claim_count", problem: "name collision" },
    ]);
  });
  it("handles a collection-level object with no slash (e.g. 'join_paths')", () => {
    const problems = parseValidationDetails([{ object: "join_paths", problem: "cycle detected" }]);
    expect(problems).toEqual([{ kind: "join_paths", name: "", problem: "cycle detected" }]);
  });
  it("non-array input yields no problems", () => {
    expect(parseValidationDetails(undefined)).toEqual([]);
    expect(parseValidationDetails("not an array")).toEqual([]);
  });
  it("falls back to kind 'unknown' when the object field is missing or non-string", () => {
    const problems = parseValidationDetails([
      { problem: "no object field" },
      { object: 5, problem: "bad object type" },
      { object: "dimension/x" }, // missing problem -> skipped entirely
    ]);
    expect(problems).toEqual([
      { kind: "unknown", name: "", problem: "no object field" },
      { kind: "unknown", name: "", problem: "bad object type" },
    ]);
  });
});

describe("groupProblemsByObject", () => {
  it("groups by kind/name key and preserves multiple problems per object", () => {
    const grouped = groupProblemsByObject([
      { kind: "dimension", name: "claim_type", problem: "issue A" },
      { kind: "dimension", name: "claim_type", problem: "issue B" },
      { kind: "measure", name: "claim_count", problem: "issue C" },
      { kind: "join_paths", name: "", problem: "cycle" },
    ]);
    expect(grouped.get("dimension/claim_type")).toEqual(["issue A", "issue B"]);
    expect(grouped.get("measure/claim_count")).toEqual(["issue C"]);
    expect(grouped.get("join_paths")).toEqual(["cycle"]);
    expect(grouped.get("entity/nope")).toBeUndefined();
  });
});

describe("datasetIdFromUrn", () => {
  it("extracts the trailing id from a dataset URN", () => {
    expect(datasetIdFromUrn("wr:t-42:dataset:dataset/019f5489-40b1-7c39-8c54-67195a419148")).toBe(
      "019f5489-40b1-7c39-8c54-67195a419148",
    );
  });
  it("returns undefined for missing/malformed input", () => {
    expect(datasetIdFromUrn(undefined)).toBeUndefined();
    expect(datasetIdFromUrn(null)).toBeUndefined();
    expect(datasetIdFromUrn("")).toBeUndefined();
  });
});
