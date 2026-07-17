/**
 * Cursor pagination glue (BFF-FR-020, AC-13).
 *
 * GraphQL `(first, after)` maps 1:1 onto the REST `(limit, cursor)` contract.
 * `first` is capped at 200; a larger value is rejected with VALIDATION_FAILED
 * exactly like the REST layer — the BFF adds no pagination semantics of its own.
 */
import type { Limits } from "./config.js";
import { gqlError, ErrorCode } from "./errors/errors.js";
import type { Page } from "./clients/types.js";

export interface ConnectionArgs {
  first?: number | null;
  after?: string | null;
}

export function toLimitCursor(
  args: ConnectionArgs,
  limits: Limits,
): { limit: number; cursor?: string } {
  const first = args.first ?? limits.defaultPageSize;
  if (first < 1 || first > limits.maxPageSize) {
    throw gqlError(
      ErrorCode.VALIDATION_FAILED,
      `first must be between 1 and ${limits.maxPageSize}`,
      { details: { field: "first", max: limits.maxPageSize } },
    );
  }
  return { limit: first, cursor: args.after ?? undefined };
}

export interface Connection<T> {
  nodes: T[];
  edges: { cursor: string | null; node: T }[];
  pageInfo: { nextCursor: string | null; hasMore: boolean };
}

/** Wrap a REST page envelope in the GraphQL Connection shape.
 * hasMore is only honest when a cursor exists to fetch the next page with: a
 * downstream that flags has_more without a next_cursor (e.g. one that ignores
 * cursors entirely) yields hasMore=false so the UI caps at the first page
 * gracefully instead of looping on an unfetchable "more". */
export function toConnection<A, B>(page: Page<A>, mapNode: (a: A) => B): Connection<B> {
  const nextCursor = page.page?.next_cursor ?? null;
  const nodes = (page.data ?? []).map(mapNode);
  return {
    nodes,
    edges: nodes.map((node) => ({ cursor: nextCursor, node })),
    pageInfo: { nextCursor, hasMore: nextCursor != null && (page.page?.has_more ?? false) },
  };
}
