/** Unit-test helpers: craft a decodable (unsigned) JWT and build a real
 * GraphQLContext whose clients use a boundary-double fetch. */
import { loadConfig, type Config } from "../../src/config.js";
import { buildContext, type GraphQLContext } from "../../src/context.js";

function b64url(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj)).toString("base64url");
}

/** A structurally-valid JWT that `decodeJwt` can read (no signature; used only
 * when VERIFY_JWT=false in unit tests). */
export function fakeJwt(claims: Record<string, unknown>): string {
  return `${b64url({ alg: "none", typ: "JWT" })}.${b64url(claims)}.sig`;
}

export function testConfig(overrides: Partial<Config> = {}): Config {
  return loadConfig({ verifyJwt: false, mode: "test", introspection: true, persistedQueriesOnly: false, ...overrides });
}

export async function makeTestContext(
  fetchImpl: typeof fetch,
  claims: Record<string, unknown> = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["case.case.read"] },
  cfg: Config = testConfig(),
): Promise<GraphQLContext> {
  return buildContext(
    { config: cfg, jwks: undefined, fetchImpl },
    { authorization: `Bearer ${fakeJwt(claims)}`, "x-trace-id": "trace-test" },
  );
}
