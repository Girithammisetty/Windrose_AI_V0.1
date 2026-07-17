import { describe, it, expect } from "vitest";
import { routeUrnFor } from "./urn";
import { urnParts } from "./utils";

describe("routeUrnFor (AC-3 copilot context)", () => {
  it("derives a dataset URN from the dataset detail route", () => {
    expect(routeUrnFor("/data/datasets/ds-9f2", "t-acme")).toBe("wr:t-acme:dataset:dataset/ds-9f2");
  });
  it("derives a case URN", () => {
    expect(routeUrnFor("/cases/case-1", "t-acme")).toBe("wr:t-acme:case:case/case-1");
  });
  it("returns null for a non-resource route", () => {
    expect(routeUrnFor("/admin/users", "t-acme")).toBeNull();
  });
});

describe("urnParts", () => {
  it("splits a windrose urn", () => {
    expect(urnParts("wr:t-acme:dataset:dataset/ds-9")).toMatchObject({
      tenant: "t-acme",
      type: "dataset",
      path: "dataset/ds-9",
    });
  });
});
