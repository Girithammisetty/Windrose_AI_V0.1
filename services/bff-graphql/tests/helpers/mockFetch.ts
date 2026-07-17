/** A boundary double for UNIT tests only: a real-signature `fetch` that routes
 * to canned OpenAPI-shaped responses and records every outbound request so
 * tests can assert JWT passthrough / batching. Never used in the runtime path. */
export interface CapturedRequest {
  method: string;
  url: string;
  path: string;
  search: URLSearchParams;
  headers: Record<string, string>;
  body: any;
}

export interface MockResponse {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}

export type Handler = (req: CapturedRequest) => MockResponse | Promise<MockResponse>;

export function mockFetch(handler: Handler): {
  fetchImpl: typeof fetch;
  requests: CapturedRequest[];
} {
  const requests: CapturedRequest[] = [];
  const fetchImpl = (async (input: any, init: any = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url);
    const headers: Record<string, string> = {};
    const h = init.headers ?? {};
    for (const [k, v] of Object.entries(h)) headers[k.toLowerCase()] = String(v);
    const captured: CapturedRequest = {
      method: init.method ?? "GET",
      url: url.toString(),
      path: url.pathname,
      search: url.searchParams,
      headers,
      body: init.body ? JSON.parse(init.body) : undefined,
    };
    requests.push(captured);
    const r = await handler(captured);
    const status = r.status ?? 200;
    // 204/205/304 are null-body statuses — a non-null body is a TypeError.
    const nullBody = r.body === undefined || [204, 205, 304].includes(status);
    return new Response(nullBody ? null : JSON.stringify(r.body), {
      status,
      headers: { "content-type": "application/json", ...(r.headers ?? {}) },
    });
  }) as unknown as typeof fetch;
  return { fetchImpl, requests };
}
