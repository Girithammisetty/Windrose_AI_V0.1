/**
 * Publishes the dev signing key's public JWKS so a locally-booted bff-graphql
 * can verify the user JWTs this app mints (AUTH_MODE=dev). Real RS256 public
 * keys — the same verification path the BFF uses against identity-service.
 */
import { NextResponse } from "next/server";
import { jwks } from "@/lib/auth/keys";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  if ((process.env.AUTH_MODE ?? "dev") !== "dev") {
    return NextResponse.json({ keys: [] }, { status: 404 });
  }
  return NextResponse.json(await jwks(), {
    headers: { "cache-control": "public, max-age=300" },
  });
}
