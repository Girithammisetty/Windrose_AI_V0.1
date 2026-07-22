import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { print } from "graphql";
import { typeDefs } from "../../src/schema/typeDefs.js";

/**
 * WS5 (BRD 58): the schema-snapshot CI gate. The checked-in schema.graphql is
 * the reviewable, diffable record of the BFF's public contract — a change to
 * typeDefs.ts that isn't reflected here means either an unreviewed schema
 * drift (this test should fail) or a forgotten `pnpm run schema:snapshot`
 * before committing.
 */
describe("GraphQL schema snapshot", () => {
  it("matches the checked-in schema.graphql", () => {
    const live = print(typeDefs) + "\n";
    const snapshotPath = fileURLToPath(new URL("../../schema.graphql", import.meta.url));
    const checkedIn = readFileSync(snapshotPath, "utf8");
    expect(live, "typeDefs.ts has drifted from schema.graphql — run `pnpm run schema:snapshot` and commit the diff").toBe(
      checkedIn,
    );
  });
});
