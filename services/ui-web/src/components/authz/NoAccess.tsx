"use client";
import Link from "next/link";
import { Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n/messages";

/**
 * Non-leaking "you don't have access" state (UI-FR-014). Shown by the route
 * guard when the viewer lacks the capability a route requires — we render this
 * instead of the page, never the page's data. Mirrors the PERMISSION_DENIED
 * copy so a client-side gate and a server 403 look identical to the user.
 */
export function NoAccess() {
  return (
    <div
      role="alert"
      className="flex min-h-[60vh] flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-8 text-center"
      data-testid="no-access"
    >
      <Lock className="size-8 text-muted-foreground" aria-hidden />
      <div>
        <p className="font-medium">{t("state.permissionDenied")}</p>
        <p className="mt-1 text-sm text-muted-foreground">{t("state.noAccessHint")}</p>
      </div>
      <Button asChild variant="outline" size="sm">
        <Link href="/">{t("action.backHome")}</Link>
      </Button>
    </div>
  );
}
