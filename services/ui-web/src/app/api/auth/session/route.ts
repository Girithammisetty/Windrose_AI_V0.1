import { NextResponse } from "next/server";
import { getSessionClaims } from "@/lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const claims = await getSessionClaims();
  if (!claims) return NextResponse.json({ authenticated: false }, { status: 401 });
  return NextResponse.json({
    authenticated: true,
    userId: claims.sub,
    tenantId: claims.tenantId,
    workspaceId: claims.workspaceId,
    scopes: claims.scopes,
  });
}
