import { redirect } from "next/navigation";
import { getSessionClaims } from "@/lib/auth/session";
import { AppShell } from "@/components/shell/AppShell";

/**
 * App-group layout (server). Reads the verified session from the httpOnly cookie
 * and hands it to the client shell. Fail-closed: no session → /login.
 */
export default async function AppGroupLayout({ children }: { children: React.ReactNode }) {
  const claims = await getSessionClaims();
  if (!claims) redirect("/login");

  return (
    <AppShell
      session={{
        userId: claims.sub,
        tenantId: claims.tenantId,
        workspaceId: claims.workspaceId,
        scopes: claims.scopes,
      }}
    >
      {children}
    </AppShell>
  );
}
