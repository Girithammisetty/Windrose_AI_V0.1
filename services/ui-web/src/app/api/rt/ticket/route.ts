/**
 * Mints a single-use realtime-hub connect ticket (RTH-FR-011). The browser can't
 * set an Authorization header on EventSource, so we mint a ticket server-side
 * (with the user's Bearer JWT) and hand the browser {hubUrl, ticket}; it then
 * opens GET {hubUrl}/api/v1/stream?ticket=... directly (real SSE, not proxied).
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionToken } from "@/lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HUB_URL =
  process.env.NEXT_PUBLIC_REALTIME_HUB_URL ?? process.env.REALTIME_HUB_URL ?? "http://localhost:8305";

export async function POST(req: NextRequest) {
  const token = await getSessionToken();
  if (!token) return NextResponse.json({ error: "unauthenticated" }, { status: 401 });

  const { topics } = (await req.json().catch(() => ({}))) as { topics?: string[] };
  if (!Array.isArray(topics) || topics.length === 0) {
    return NextResponse.json({ error: "topics required" }, { status: 400 });
  }

  try {
    const res = await fetch(`${HUB_URL}/api/v1/stream-tickets`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
      body: JSON.stringify({ topics }),
    });
    if (!res.ok) {
      return NextResponse.json({ error: "ticket mint failed", status: res.status }, { status: 502 });
    }
    // realtime-hub wraps the ticket in a `{ data: { ticket } }` envelope.
    const json = (await res.json()) as {
      data?: { ticket?: string; id?: string };
      ticket?: string;
      id?: string;
    };
    const d = json.data ?? json;
    const ticket = d.ticket ?? d.id;
    return NextResponse.json({ hubUrl: HUB_URL, ticket });
  } catch {
    return NextResponse.json({ error: "hub unreachable" }, { status: 502 });
  }
}
