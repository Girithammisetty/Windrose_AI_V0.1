/**
 * Apollo Server 4 assembly.
 *
 * Wires the SDL + resolvers, the static query-limit validation rules, the
 * persisted-query allowlist plugin, and a formatError that maps any downstream
 * failure to the master error codes (BFF-FR-050/051). Introspection follows the
 * mode (on in dev, off in prod — BFF-FR-041).
 */
import { ApolloServer, type GraphQLRequestContext } from "@apollo/server";
import { GraphQLError } from "graphql";
import type { Config } from "./config.js";
import type { GraphQLContext } from "./context.js";
import { typeDefs } from "./schema/typeDefs.js";
import { resolvers } from "./resolvers/index.js";
import { operationLimits } from "./validation/limits.js";
import { persistedQueriesPlugin, loadManifest, type PersistedManifest } from "./plugins/persistedQueries.js";
import { DownstreamError, mapDownstreamError } from "./errors/errors.js";

export interface ApolloOptions {
  manifest?: PersistedManifest;
}

/** Find a DownstreamError anywhere in an error's cause chain. */
function findDownstream(err: unknown): DownstreamError | undefined {
  let cur: any = err;
  for (let i = 0; i < 5 && cur; i++) {
    if (cur instanceof DownstreamError) return cur;
    cur = cur.originalError ?? cur.cause;
  }
  return undefined;
}

export function makeApolloServer(cfg: Config, opts: ApolloOptions = {}): ApolloServer<GraphQLContext> {
  const manifest = opts.manifest ?? loadManifest();
  return new ApolloServer<GraphQLContext>({
    typeDefs,
    resolvers,
    introspection: cfg.introspection,
    validationRules: [operationLimits(cfg.limits)],
    plugins: [persistedQueriesPlugin(cfg, manifest)],
    formatError: (formatted, error) => {
      const ds = findDownstream(error);
      if (ds) {
        const mapped = mapDownstreamError(ds);
        return {
          message: mapped.message,
          path: formatted.path,
          extensions: mapped.extensions,
        };
      }
      // Locally-thrown GraphQLErrors already carry a stable extensions.code.
      if (error instanceof GraphQLError && error.extensions?.code) {
        return { ...formatted, extensions: error.extensions };
      }
      return formatted;
    },
  });
}

export type { GraphQLRequestContext };
