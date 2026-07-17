import { describe, it, expect } from "vitest";
import {
  DownstreamError,
  ErrorCode,
  statusToCode,
  mapDownstreamError,
} from "../../src/errors/errors.js";

describe("error code mapping (BFF-FR-051)", () => {
  it("maps every documented status/code to a stable GraphQL code", () => {
    expect(statusToCode(400)).toBe(ErrorCode.VALIDATION_FAILED);
    expect(statusToCode(422)).toBe(ErrorCode.VALIDATION_FAILED);
    expect(statusToCode(401)).toBe(ErrorCode.UNAUTHENTICATED);
    expect(statusToCode(403)).toBe(ErrorCode.PERMISSION_DENIED);
    expect(statusToCode(404)).toBe(ErrorCode.NOT_FOUND);
    expect(statusToCode(402)).toBe(ErrorCode.BUDGET_EXHAUSTED);
    expect(statusToCode(409)).toBe(ErrorCode.CONFLICT);
    expect(statusToCode(412)).toBe(ErrorCode.CONFLICT);
    expect(statusToCode(429)).toBe(ErrorCode.RATE_LIMITED);
    expect(statusToCode(500)).toBe(ErrorCode.SERVICE_UNAVAILABLE);
    expect(statusToCode(503)).toBe(ErrorCode.SERVICE_UNAVAILABLE);
    expect(statusToCode(0)).toBe(ErrorCode.SERVICE_UNAVAILABLE); // transport/timeout
    expect(statusToCode(418)).toBe(ErrorCode.INTERNAL); // unmapped
  });

  it("honours explicit budget/rate codes regardless of status", () => {
    expect(statusToCode(429, "BUDGET_EXHAUSTED")).toBe(ErrorCode.BUDGET_EXHAUSTED);
    expect(statusToCode(429, "RATE_LIMITED")).toBe(ErrorCode.RATE_LIMITED);
  });

  it("preserves service/trace/details in extensions and hides detail on INTERNAL", () => {
    const permErr = mapDownstreamError(
      new DownstreamError("case-service", 403, "PERMISSION_DENIED", "nope", { f: 1 }, "trace-1"),
    );
    expect(permErr.extensions.code).toBe(ErrorCode.PERMISSION_DENIED);
    expect(permErr.extensions.service).toBe("case-service");
    expect(permErr.extensions.traceId).toBe("trace-1");
    expect(permErr.extensions.httpStatus).toBe(403);

    const internal = mapDownstreamError(
      new DownstreamError("x-service", 418, undefined, "weird", { secret: "leak" }, "t"),
    );
    expect(internal.extensions.code).toBe(ErrorCode.INTERNAL);
    expect(internal.extensions.details).toBeUndefined(); // no leakage (BFF-FR-051)
  });
});
