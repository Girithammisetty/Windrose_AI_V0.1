/**
 * Error mapping (BFF-FR-050 / BFF-FR-051).
 *
 * Downstream services speak the master error envelope
 *   { error: { code, message, details?, trace_id } }
 * The BFF preserves the machine-readable `code` verbatim in
 * `extensions.code` and attaches { details, traceId, service, httpStatus }.
 * The BFF invents no codes and makes no authz decisions — a downstream 403
 * simply becomes PERMISSION_DENIED.
 */
import { GraphQLError } from "graphql";

/** Stable GraphQL error codes surfaced to the UI (BFF-FR-051). */
export const ErrorCode = {
  VALIDATION_FAILED: "VALIDATION_FAILED",
  UNAUTHENTICATED: "UNAUTHENTICATED",
  PERMISSION_DENIED: "PERMISSION_DENIED",
  NOT_FOUND: "NOT_FOUND",
  BUDGET_EXHAUSTED: "BUDGET_EXHAUSTED",
  CONFLICT: "CONFLICT",
  CONNECTION_TEST_FAILED: "CONNECTION_TEST_FAILED",
  RATE_LIMITED: "RATE_LIMITED",
  SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
  PERSISTED_QUERY_REQUIRED: "PERSISTED_QUERY_REQUIRED",
  QUERY_TOO_COMPLEX: "QUERY_TOO_COMPLEX",
  INTERNAL: "INTERNAL",
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];

/** Master error envelope as returned by every domain service. */
export interface DownstreamEnvelope {
  error: {
    code?: string;
    message?: string;
    details?: unknown;
    trace_id?: string;
  };
}

/**
 * Raised by the HTTP clients when a downstream returns a non-2xx response or
 * the call fails at the transport level (timeout / connection refused).
 */
export class DownstreamError extends Error {
  constructor(
    /** Name of the downstream service (e.g. "case-service"). */
    public readonly service: string,
    /** HTTP status; 0 for transport failures (timeout/connection). */
    public readonly httpStatus: number,
    /** Machine-readable code from the downstream envelope, if any. */
    public readonly downstreamCode: string | undefined,
    message: string,
    public readonly details?: unknown,
    public readonly traceId?: string,
    /**
     * The raw parsed non-2xx response body, when present. Most downstreams speak
     * the master error envelope (surfaced via `details`), but a few return a
     * domain payload alongside a non-2xx status — e.g. pipeline-orchestrator's
     * POST /pipelines/validate answers 422 with the validation report under
     * `data`. Preserved verbatim so a client can recover that payload without
     * re-parsing; never used for error mapping.
     */
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "DownstreamError";
  }
}

/**
 * Map an HTTP status (+ downstream code) to a stable GraphQL error code.
 * The mapping is total: anything unrecognised becomes INTERNAL.
 */
export function statusToCode(
  httpStatus: number,
  downstreamCode?: string,
): ErrorCodeValue {
  // Budget exhaustion can arrive as 402 or 429 with an explicit code.
  if (downstreamCode === "BUDGET_EXHAUSTED") return ErrorCode.BUDGET_EXHAUSTED;
  if (downstreamCode === "RATE_LIMITED") return ErrorCode.RATE_LIMITED;
  // ingestion create/update aborts with 424 when the pre-persist probe fails;
  // preserve that verbatim so the UI can show the categorized cause (AUTH_FAILED…).
  if (downstreamCode === "CONNECTION_TEST_FAILED") return ErrorCode.CONNECTION_TEST_FAILED;

  switch (httpStatus) {
    case 424:
      return ErrorCode.CONNECTION_TEST_FAILED;
    case 400:
    case 422:
      return ErrorCode.VALIDATION_FAILED;
    case 401:
      return ErrorCode.UNAUTHENTICATED;
    case 403:
      return ErrorCode.PERMISSION_DENIED;
    case 404:
      return ErrorCode.NOT_FOUND;
    case 402:
      return ErrorCode.BUDGET_EXHAUSTED;
    case 409:
      return ErrorCode.CONFLICT;
    case 412:
      return ErrorCode.CONFLICT;
    case 429:
      return ErrorCode.RATE_LIMITED;
    case 0: // transport failure / timeout / open circuit
      return ErrorCode.SERVICE_UNAVAILABLE;
    default:
      if (httpStatus >= 500) return ErrorCode.SERVICE_UNAVAILABLE;
      return ErrorCode.INTERNAL;
  }
}

/** Convert a DownstreamError into a GraphQLError with BFF-FR-050 extensions. */
export function mapDownstreamError(err: DownstreamError): GraphQLError {
  const code = statusToCode(err.httpStatus, err.downstreamCode);
  return new GraphQLError(err.message || code, {
    extensions: {
      code,
      // For unmapped/INTERNAL we do not leak downstream detail (BFF-FR-051).
      details: code === ErrorCode.INTERNAL ? undefined : err.details,
      traceId: err.traceId,
      service: err.service,
      httpStatus: err.httpStatus,
    },
  });
}

/** Build a locally-originated GraphQLError (limits, persisted queries, auth edge). */
export function gqlError(code: ErrorCodeValue, message: string, extra: Record<string, unknown> = {}): GraphQLError {
  return new GraphQLError(message, { extensions: { code, ...extra } });
}
