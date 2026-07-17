/**
 * Authed case-export download proxy → case-service GET
 * /operations/{id}/download (CASE-FR-044). The downstream route requires the
 * caller's Bearer JWT, which a plain browser <a href> cannot carry — so the
 * gzipped CSV is streamed through this same-origin route with the httpOnly
 * session forwarded, exactly like uploads/[uploadId]/parts/[n]/route.ts does
 * for chunk PUTs. Zero business logic here.
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionToken } from "@/lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CASE_URL = process.env.CASE_URL ?? "http://localhost:8308";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ operationId: string }> },
) {
  const { operationId } = await params;
  const token = await getSessionToken();
  if (!token) return NextResponse.json({ error: "unauthenticated" }, { status: 401 });

  let upstream: Response;
  try {
    upstream = await fetch(
      `${CASE_URL}/api/v1/operations/${encodeURIComponent(operationId)}/download`,
      { headers: { authorization: `Bearer ${token}` } },
    );
  } catch {
    return NextResponse.json({ error: "case service unreachable" }, { status: 502 });
  }

  if (!upstream.ok) {
    // Surface the downstream failure (e.g. 422 "export not ready", 403, 404)
    // as JSON with its real status — never a fake file.
    const text = await upstream.text();
    return new NextResponse(text || JSON.stringify({ error: `case service returned ${upstream.status}` }), {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  }

  // Stream the gzipped CSV straight through — never buffered into ui-web
  // memory — preserving the attachment filename the service set.
  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/gzip",
      "content-disposition":
        upstream.headers.get("content-disposition") ??
        `attachment; filename="cases-${operationId}.csv.gz"`,
    },
  });
}
