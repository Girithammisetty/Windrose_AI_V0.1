import { describe, it, expect } from "vitest";
import { buildASTSchema, parse, validate } from "graphql";
import { typeDefs } from "../../src/schema/typeDefs.js";
import { operationLimits } from "../../src/validation/limits.js";
import { loadConfig } from "../../src/config.js";
import { makeApolloServer } from "../../src/server.js";
import { ErrorCode } from "../../src/errors/errors.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch } from "../helpers/mockFetch.js";

const schema = buildASTSchema(typeDefs);
const limits = loadConfig().limits;

function codes(query: string): string[] {
  return validate(schema, parse(query), [operationLimits(limits)]).map(
    (e) => e.extensions?.code as string,
  );
}

describe("static query limits (BFF-FR-041 / AC-5)", () => {
  it("rejects a query deeper than 10 with QUERY_TOO_COMPLEX", () => {
    const deep =
      "{ __schema { types { fields { type { ofType { ofType { ofType { ofType { ofType { ofType { ofType { name } } } } } } } } } } } }";
    expect(codes(deep)).toContain(ErrorCode.QUERY_TOO_COMPLEX);
  });

  it("rejects a query exceeding the 5000 cost budget", () => {
    const field = "nodes { id urn caseNumber title status severity dueDate createdAt }";
    const costly = `{
      a: caseSearch(first: 200) { ${field} }
      b: caseSearch(first: 200) { ${field} }
      c: caseSearch(first: 200) { ${field} }
      d: caseSearch(first: 200) { ${field} }
    }`;
    expect(codes(costly)).toContain(ErrorCode.QUERY_TOO_COMPLEX);
  });

  it("rejects more than 5 root fields", () => {
    const q = "{ a: me { userId } b: me { userId } c: me { userId } d: me { userId } e: me { userId } f: me { userId } }";
    expect(codes(q)).toContain(ErrorCode.QUERY_TOO_COMPLEX);
  });

  it("allows a normal page query", () => {
    expect(codes("{ caseSearch(first: 50) { nodes { id status } pageInfo { hasMore } } }")).toEqual([]);
  });
});

describe("persisted-query allowlist (BFF-FR-040 / AC-4)", () => {
  it("rejects an ad-hoc document in production mode", async () => {
    const cfg = testConfig({ mode: "production", persistedQueriesOnly: true, introspection: false });
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch(() => ({ status: 200, body: {} }));
    const ctx = await makeTestContext(fetchImpl, undefined, cfg);
    const res = await server.executeOperation(
      { query: "{ me { userId } }" },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe(ErrorCode.PERSISTED_QUERY_REQUIRED);
  });
});
