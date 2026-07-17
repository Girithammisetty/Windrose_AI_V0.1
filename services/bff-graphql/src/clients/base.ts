/**
 * Real HTTP client used by every resolver to reach a downstream domain service.
 *
 * This is the ONLY place the BFF talks to the outside world. It is a real
 * `fetch` (Node 20 built-in undici) client — there is no fake/stub in the
 * runtime path. Key behaviours:
 *  - JWT passthrough: the caller's `Authorization: Bearer <jwt>` header is
 *    forwarded verbatim on every call (BFF-FR-010). The BFF holds no
 *    credentials of its own and mints no tokens.
 *  - Trace propagation: `traceparent` and `X-Trace-Id` are forwarded so a
 *    GraphQL operation is one distributed trace (BFF-FR-012).
 *  - Timeout: per-downstream 10s cap via AbortController (BFF-FR-032 / BR-4).
 *  - Error envelope parsing: non-2xx bodies are parsed as the master envelope
 *    and re-thrown as DownstreamError, which resolvers let bubble to the
 *    error-mapping formatter.
 *
 * The `fetchImpl` is injectable so UNIT tests can supply a boundary double;
 * production and integration use the real global fetch.
 */
import { DownstreamError, type DownstreamEnvelope } from "../errors/errors.js";

export type FetchImpl = typeof fetch;

/** Cap on a downstream response body the BFF will buffer. Every resolver's data
 * is bounded (pagination ≤200, dataset browse ≤500 rows), so a body past this
 * signals a mis-scoped/pathological response — reject it instead of buffering
 * an unbounded string into the BFF heap (OOM guard). */
const DOWNSTREAM_MAX_BYTES = 32 * 1024 * 1024;

async function readBodyCapped(
  res: Response,
  service: string,
  traceId: string | undefined,
): Promise<string> {
  const declared = Number(res.headers.get("content-length") ?? "0");
  if (declared > DOWNSTREAM_MAX_BYTES) {
    throw new DownstreamError(
      service, res.status, "RESPONSE_TOO_LARGE",
      `${service} response exceeds ${DOWNSTREAM_MAX_BYTES} bytes`, undefined, traceId,
    );
  }
  const body = res.body as ReadableStream<Uint8Array> | null;
  if (!body) return res.text();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > DOWNSTREAM_MAX_BYTES) {
      await reader.cancel().catch(() => {});
      throw new DownstreamError(
        service, res.status, "RESPONSE_TOO_LARGE",
        `${service} response exceeds ${DOWNSTREAM_MAX_BYTES} bytes`, undefined, traceId,
      );
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString("utf8");
}

export interface ClientRequestContext {
  /** The raw inbound `Authorization` header value, forwarded verbatim. */
  authorization?: string;
  /** Inbound W3C traceparent (propagated). */
  traceparent?: string;
  /** Inbound / generated X-Trace-Id (propagated). */
  traceId?: string;
}

export interface ServiceClientOptions {
  service: string;
  baseUrl: string;
  ctx: ClientRequestContext;
  fetchImpl?: FetchImpl;
  timeoutMs?: number;
}

export interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined | null | string[]>;
  body?: unknown;
  headers?: Record<string, string>;
  /** Idempotency-Key forwarded on side-effecting POSTs (MASTER-FR-025). */
  idempotencyKey?: string;
  /** Per-request timeout override for calls the downstream deliberately runs
   * synchronously (e.g. ingestion-service run_now with inline execution in
   * dev) — the default per-downstream cap still applies everywhere else. */
  timeoutMs?: number;
}

export class ServiceClient {
  readonly service: string;
  private readonly baseUrl: string;
  private readonly ctx: ClientRequestContext;
  private readonly fetchImpl: FetchImpl;
  private readonly timeoutMs: number;

  constructor(opts: ServiceClientOptions) {
    this.service = opts.service;
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.ctx = opts.ctx;
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
  }

  get<T = any>(path: string, opts: RequestOptions = {}): Promise<T> {
    return this.request<T>("GET", path, opts);
  }

  post<T = any>(path: string, opts: RequestOptions = {}): Promise<T> {
    return this.request<T>("POST", path, opts);
  }

  patch<T = any>(path: string, opts: RequestOptions = {}): Promise<T> {
    return this.request<T>("PATCH", path, opts);
  }

  put<T = any>(path: string, opts: RequestOptions = {}): Promise<T> {
    return this.request<T>("PUT", path, opts);
  }

  delete<T = any>(path: string, opts: RequestOptions = {}): Promise<T> {
    return this.request<T>("DELETE", path, opts);
  }

  private buildUrl(path: string, query?: RequestOptions["query"]): string {
    const url = new URL(this.baseUrl + path);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v === undefined || v === null) continue;
        if (Array.isArray(v)) {
          for (const item of v) url.searchParams.append(k, String(item));
        } else {
          url.searchParams.set(k, String(v));
        }
      }
    }
    return url.toString();
  }

  private headers(opts: RequestOptions): Record<string, string> {
    const h: Record<string, string> = {
      "content-type": "application/json",
      accept: "application/json",
    };
    // --- JWT passthrough: forward the user's token untouched (BFF-FR-010).
    if (this.ctx.authorization) h["authorization"] = this.ctx.authorization;
    // --- trace propagation (BFF-FR-012).
    if (this.ctx.traceparent) h["traceparent"] = this.ctx.traceparent;
    if (this.ctx.traceId) h["x-trace-id"] = this.ctx.traceId;
    if (opts.idempotencyKey) h["idempotency-key"] = opts.idempotencyKey;
    return { ...h, ...(opts.headers ?? {}) };
  }

  private async request<T>(method: string, path: string, opts: RequestOptions): Promise<T> {
    const url = this.buildUrl(path, opts.query);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? this.timeoutMs);
    let res: Response;
    try {
      res = await this.fetchImpl(url, {
        method,
        headers: this.headers(opts),
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
    } catch (e) {
      // Transport failure (timeout / connection refused) -> SERVICE_UNAVAILABLE.
      const msg = (e as Error)?.name === "AbortError" ? `${this.service} timed out` : `${this.service} unreachable`;
      throw new DownstreamError(this.service, 0, undefined, msg, undefined, this.ctx.traceId);
    } finally {
      clearTimeout(timer);
    }

    const traceId = res.headers.get("x-trace-id") ?? this.ctx.traceId;

    if (res.status === 204) return undefined as T;

    const text = await readBodyCapped(res, this.service, traceId ?? undefined);
    const parsed = text ? safeJson(text) : undefined;

    if (!res.ok) {
      const envelope = parsed as DownstreamEnvelope | undefined;
      const de = envelope?.error;
      throw new DownstreamError(
        this.service,
        res.status,
        de?.code,
        de?.message ?? `${this.service} returned ${res.status}`,
        de?.details,
        de?.trace_id ?? traceId ?? undefined,
        parsed,
      );
    }
    return parsed as T;
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
