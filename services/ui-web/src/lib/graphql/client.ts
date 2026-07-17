/**
 * GraphQL fetcher. The browser posts to the SAME-ORIGIN Next route /api/graphql,
 * which forwards to the real bff-graphql (BFF_URL) with the user's Bearer JWT
 * read from the httpOnly session cookie (UI-FR-003/004: token never in JS).
 *
 * In production the operation hash (persisted-operation manifest, UI-FR-046) is
 * sent alongside the document; dev/test send the ad-hoc document (the BFF accepts
 * ad-hoc outside production).
 */

export interface GraphQLErrorEntry {
  message: string;
  path?: (string | number)[];
  extensions?: {
    code?: string;
    traceId?: string;
    service?: string;
    httpStatus?: number;
    details?: unknown;
  };
}

/** A shaped error the AsyncBoundary/error panel can render (BR-10). */
export class GraphQLRequestError extends Error {
  code: string;
  traceId?: string;
  service?: string;
  httpStatus?: number;
  raw: GraphQLErrorEntry[];

  constructor(errors: GraphQLErrorEntry[], httpStatus?: number) {
    const first = errors[0];
    super(first?.message ?? "GraphQL request failed");
    this.name = "GraphQLRequestError";
    this.code = first?.extensions?.code ?? "INTERNAL";
    this.traceId = first?.extensions?.traceId;
    this.service = first?.extensions?.service;
    this.httpStatus = httpStatus ?? first?.extensions?.httpStatus;
    this.raw = errors;
  }
}

export interface GraphQLResponse<T> {
  data?: T | null;
  errors?: GraphQLErrorEntry[];
}

const ENDPOINT = "/api/graphql";

export async function graphqlRequest<TData, TVars extends Record<string, unknown> = Record<string, unknown>>(
  document: string,
  variables?: TVars,
  signal?: AbortSignal,
): Promise<TData> {
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query: document, variables: variables ?? {} }),
    signal,
    credentials: "same-origin",
  });

  if (res.status === 401) {
    throw new GraphQLRequestError(
      [{ message: "Your session expired. Please sign in again.", extensions: { code: "UNAUTHENTICATED" } }],
      401,
    );
  }

  let body: GraphQLResponse<TData>;
  try {
    body = (await res.json()) as GraphQLResponse<TData>;
  } catch {
    throw new GraphQLRequestError(
      [{ message: "Malformed response from the API.", extensions: { code: "INTERNAL" } }],
      res.status,
    );
  }

  if (body.errors && body.errors.length > 0) {
    // A nullable field masked by tenant isolation returns data + no error; only
    // throw when the top-level errors array is populated.
    throw new GraphQLRequestError(body.errors, res.status);
  }
  if (body.data == null) {
    throw new GraphQLRequestError(
      [{ message: "No data returned.", extensions: { code: "INTERNAL" } }],
      res.status,
    );
  }
  return body.data;
}
