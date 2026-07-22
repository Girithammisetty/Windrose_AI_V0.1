// Regenerates the checked-in SDL snapshot (schema.graphql) from the live
// typeDefs (WS5, BRD 58: "GraphQL schema-snapshot ... as a CI gate"). Run this
// after an intentional schema change; the schema-snapshot test fails if the
// checked-in file goes stale, so an unreviewed schema drift can't slip through.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { print } from "graphql";
import { typeDefs } from "../src/schema/typeDefs.js";

const out = fileURLToPath(new URL("../schema.graphql", import.meta.url));
writeFileSync(out, print(typeDefs) + "\n");
console.log(`wrote ${out}`);
