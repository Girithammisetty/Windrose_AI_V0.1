"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import { NAV_ITEMS, NAV_GROUP_LABEL } from "@/lib/authz/registry";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { WindroseLogo } from "@/components/brand/WindroseLogo";

/**
 * Primary navigation. Renders ONLY the items the viewer's capabilities/roles
 * unlock (UI-FR-004): the nav is derived from the registry + the backend
 * viewer.capabilities, so each persona sees a different sidebar. Fail-safe —
 * gated items stay hidden until the viewer's capabilities are known. Items are
 * grouped into sections (registry NavGroup): a section header is emitted when
 * the group changes between adjacent VISIBLE items, so empty sections never
 * show (an adjuster sees no "Data" heading).
 */
export function Sidebar({ pendingCount }: { pendingCount?: number }) {
  const pathname = usePathname();
  const { can, capsDegraded } = useCapabilities();
  const items = NAV_ITEMS.filter((item) => can(item.gate));

  return (
    <nav
      aria-label="Primary"
      className="hidden w-52 shrink-0 flex-col gap-0.5 border-r bg-card/50 p-3 md:flex"
    >
      <div className="mb-4 flex items-center gap-2 px-2">
        <WindroseLogo className="size-7 shrink-0" />
        <span className="text-lg font-bold tracking-tight">{t("app.name")}</span>
      </div>
      {items.map(({ key, href, icon: Icon, label, group }, i) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        // Section header: the first visible item of a new group opens a section.
        const showHeader = group && group !== items[i - 1]?.group;
        return (
          <div key={key} className="contents">
            {showHeader && (
              <div
                data-nav-group={group}
                className="mt-3 px-2 pb-1 text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground/70"
              >
                {t(NAV_GROUP_LABEL[group])}
              </div>
            )}
            <Link
              href={href}
              aria-current={active ? "page" : undefined}
              data-nav={key}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-2 text-sm font-medium transition-colors",
                active ? "bg-primary/10 text-primary" : "text-foreground/70 hover:bg-accent hover:text-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden />
              <span>{t(label)}</span>
              {key === "inbox" && pendingCount ? (
                <span className="ml-auto rounded-full bg-ai px-1.5 text-xs font-semibold text-ai-foreground">
                  {pendingCount}
                </span>
              ) : null}
            </Link>
          </div>
        );
      })}
      {capsDegraded && (
        // The rbac lookup failed: the nav stays fail-closed (nothing gated shows)
        // but we say WHY, instead of presenting the outage as "no access".
        <div
          role="status"
          data-caps-degraded
          className="mt-2 flex items-start gap-2 rounded-md border border-dashed border-[hsl(var(--warning))]/50 bg-[hsl(var(--warning))]/10 p-2 text-xs text-muted-foreground"
        >
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>Permissions unavailable — some navigation is hidden. Retry shortly.</span>
        </div>
      )}
    </nav>
  );
}
