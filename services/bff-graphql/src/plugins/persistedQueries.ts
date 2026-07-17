/**
 * Persisted-operation allowlist (BFF-FR-040, AC-4).
 *
 * In production the graph accepts ONLY operations whose sha256 hash is in the
 * published manifest (extracted from ui-web's build). An ad-hoc document, or a
 * hash not in the manifest, is rejected with PERSISTED_QUERY_REQUIRED and never
 * executed. In dev/test the allowlist is disabled so arbitrary queries run.
 *
 * The manifest itself is an artifact (hash -> document) loaded at boot; the
 * enforcement mechanism here is real regardless of manifest contents.
 */
import type { ApolloServerPlugin } from "@apollo/server";
import type { Config } from "../config.js";
import type { GraphQLContext } from "../context.js";
import { gqlError, ErrorCode } from "../errors/errors.js";

export type PersistedManifest = Map<string, string>;

export function loadManifest(json: Record<string, string> = {}): PersistedManifest {
  return new Map(Object.entries(json));
}

export function persistedQueriesPlugin(
  cfg: Config,
  manifest: PersistedManifest,
): ApolloServerPlugin<GraphQLContext> {
  return {
    async requestDidStart() {
      return {
        async didResolveOperation(rc) {
          if (!cfg.persistedQueriesOnly) return;
          const hash =
            (rc.request.extensions as any)?.persistedQuery?.sha256Hash as string | undefined;
          if (hash && manifest.has(hash)) return; // allowlisted persisted op
          throw gqlError(
            ErrorCode.PERSISTED_QUERY_REQUIRED,
            "Only persisted operations are accepted in production",
            { http: { status: 400 } },
          );
        },
      };
    },
  };
}
