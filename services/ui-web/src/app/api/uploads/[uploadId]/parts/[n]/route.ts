/**
 * Binary chunk-PUT proxy → ingestion-service PUT /uploads/{uploadId}/parts/{n}
 * (ING-FR-040..042). GraphQL is JSON-only (see clients/base.ts ServiceClient,
 * which always JSON-stringifies bodies) so the raw chunk body cannot go through
 * bff-graphql; it is streamed directly, browser -> this same-origin route ->
 * ingestion-service, with the caller's session forwarded exactly like
 * copilot/message/route.ts and graphql/route.ts do for their JSON proxies.
 *
 * The session lifecycle (createUpload/upload/completeUpload) IS JSON and goes
 * through bff-graphql normally — only this per-chunk PUT bypasses it.
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionToken } from "@/lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const INGESTION_URL = process.env.INGESTION_URL ?? "http://localhost:8303";

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ uploadId: string; n: string }> },
) {
  const { uploadId, n } = await params;
  const token = await getSessionToken();
  if (!token) return NextResponse.json({ error: "unauthenticated" }, { status: 401 });

  if (!req.body) return NextResponse.json({ error: "empty chunk body" }, { status: 400 });

  const contentSha256 = req.headers.get("content-sha256");
  let upstream: Response;
  try {
    upstream = await fetch(
      `${INGESTION_URL}/api/v1/uploads/${encodeURIComponent(uploadId)}/parts/${encodeURIComponent(n)}`,
      {
        method: "PUT",
        headers: {
          authorization: `Bearer ${token}`,
          "content-type": "application/octet-stream",
          ...(contentSha256 ? { "content-sha256": contentSha256 } : {}),
        },
        // Stream the chunk straight through — never buffered into ui-web memory
        // (mirrors ingestion-service's own request.stream() handling server-side).
        body: req.body,
        // Required by undici/fetch whenever `body` is a ReadableStream.
        duplex: "half",
      } as RequestInit & { duplex: "half" },
    );
  } catch {
    return NextResponse.json({ error: "ingestion service unreachable" }, { status: 502 });
  }

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
