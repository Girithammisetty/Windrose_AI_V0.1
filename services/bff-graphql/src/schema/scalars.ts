/** Custom scalar implementations. JSON is a verbatim passthrough of downstream
 * payloads; DateTime/Date are ISO-8601 strings passed through unchanged. */
import { GraphQLScalarType, Kind, type ValueNode } from "graphql";

function parseLiteral(ast: ValueNode): unknown {
  switch (ast.kind) {
    case Kind.STRING:
    case Kind.BOOLEAN:
      return ast.value;
    case Kind.INT:
    case Kind.FLOAT:
      return Number(ast.value);
    case Kind.NULL:
      return null;
    case Kind.LIST:
      return ast.values.map(parseLiteral);
    case Kind.OBJECT: {
      const obj: Record<string, unknown> = {};
      for (const f of ast.fields) obj[f.name.value] = parseLiteral(f.value);
      return obj;
    }
    default:
      return null;
  }
}

export const JSONScalar = new GraphQLScalarType({
  name: "JSON",
  description: "Arbitrary JSON passed through from a downstream service.",
  serialize: (v) => v,
  parseValue: (v) => v,
  parseLiteral,
});

export const DateTimeScalar = new GraphQLScalarType({
  name: "DateTime",
  description: "ISO-8601 UTC timestamp (MASTER-FR-026).",
  serialize: (v) => (v == null ? null : String(v)),
  parseValue: (v) => v,
});

export const DateScalar = new GraphQLScalarType({
  name: "Date",
  description: "ISO-8601 calendar date (YYYY-MM-DD).",
  serialize: (v) => (v == null ? null : String(v)),
  parseValue: (v) => v,
});
