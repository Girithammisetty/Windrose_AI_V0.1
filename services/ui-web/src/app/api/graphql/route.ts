/**
 * Same-origin GraphQL proxy → real bff-graphql (BFF_URL). Forwards the user's
 * Bearer JWT from the httpOnly session cookie; the BFF verifies it at the edge
 * and forwards it verbatim downstream (UI-FR-003/004). Zero business logic here.
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionToken } from "@/lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BFF_URL = process.env.BFF_URL ?? "http://localhost:4000/graphql";

export async function POST(req: NextRequest) {
  const token = await getSessionToken();
  if (!token) {
    return NextResponse.json(
      { errors: [{ message: "Not authenticated", extensions: { code: "UNAUTHENTICATED" } }] },
      { status: 401 },
    );
  }

  const body = await req.text();
  let upstream: Response;
  try {
    upstream = await fetch(BFF_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`,
        // Propagate a trace id if the browser supplied one (BR-10 / RUM).
        ...(req.headers.get("traceparent") ? { traceparent: req.headers.get("traceparent")! } : {}),
      },
      body,
    });
  } catch {
    return NextResponse.json(
      {
        errors: [
          {
            message: "The API is unreachable. Retry shortly.",
            extensions: { code: "UNAVAILABLE", service: "bff-graphql" },
          },
        ],
      },
      { status: 502 },
    );
  }

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
