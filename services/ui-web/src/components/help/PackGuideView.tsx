"use client";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/primitives";
import { useSession } from "@/lib/session/SessionContext";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { usePackInstalls } from "@/lib/graphql/hooks";
import {
  packGuide,
  personaSlug,
  primaryPackName,
  resolveArticle,
  roleMatches,
} from "@/lib/help/registry";
import { MarkdownView } from "./MarkdownView";
import { PersonaBadge } from "./PersonaBadge";

/**
 * The tenant's pack guide: overview + what it ships + a day-in-the-life per
 * persona (anchored so the home banner can deep-link the signed-in role).
 */
export function PackGuideView() {
  const { workspaceId } = useSession();
  const caps = useCapabilities();
  const installs = usePackInstalls(workspaceId);
  const packName = primaryPackName(installs.data);
  const guide = packGuide(packName);

  return (
    <div className="max-w-3xl">
      <Button asChild variant="ghost" size="sm" className="mb-2 -ml-2">
        <Link href="/help"><ArrowLeft /> Help &amp; guides</Link>
      </Button>

      <AsyncBoundary
        isLoading={installs.isLoading || caps.isLoading}
        isError={installs.isError}
        error={installs.error}
        isEmpty={!guide}
        emptyTitle={
          packName
            ? `A tailored guide for your ${packName} solution is coming soon — the platform guides cover every capability meanwhile.`
            : "No solution pack is installed in this workspace yet."
        }
        onRetry={() => installs.refetch()}
      >
        {guide && (
          <>
            <PageHeader
              title={`${guide.displayName} — solution guide`}
              description="How each role works day-to-day, and everything this solution ships."
            />

            <section className="mb-6">
              <MarkdownView>{guide.summary}</MarkdownView>
            </section>

            <section className="mb-6">
              <h2 className="mb-2 text-sm font-semibold text-muted-foreground">What&apos;s included</h2>
              <div className="grid gap-3 sm:grid-cols-3">
                {guide.ships.map((s) => (
                  <Card key={s.label}>
                    <CardContent className="p-4">
                      <h3 className="mb-1.5 text-sm font-semibold">{s.label}</h3>
                      <ul className="ml-4 list-disc space-y-1 text-xs text-muted-foreground">
                        {s.items.map((it) => <li key={it}>{it}</li>)}
                      </ul>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </section>

            <section>
              <h2 className="mb-1 text-sm font-semibold text-muted-foreground">Roles &amp; day-to-day</h2>
              <p className="mb-4 text-xs text-muted-foreground">
                Jump to your role — the one you&apos;re signed in as is highlighted.
              </p>
              <div className="mb-4 flex flex-wrap gap-1.5">
                {guide.personas.map((p) => (
                  <a key={p.roleName} href={`#${personaSlug(p.roleName)}`}>
                    <PersonaBadge
                      role={p.roleName}
                      active={roleMatches(p.roleName, caps.roles)}
                    />
                  </a>
                ))}
              </div>

              <div className="space-y-8">
                {guide.personas.map((p) => {
                  const mine = roleMatches(p.roleName, caps.roles);
                  return (
                    <div
                      key={p.roleName}
                      id={personaSlug(p.roleName)}
                      className={`scroll-mt-20 rounded-lg border p-4 ${mine ? "border-primary/40 bg-primary/5" : ""}`}
                    >
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <h3 className="text-base font-semibold">{p.roleName}</h3>
                        {mine && <PersonaBadge role="Your role" active />}
                      </div>
                      <p className="mb-3 text-sm text-muted-foreground">{p.tagline}</p>
                      <MarkdownView>{p.steps}</MarkdownView>

                      {p.usesCapabilities.length > 0 && (
                        <div className="mt-3 border-t pt-3">
                          <p className="mb-1 text-xs font-semibold text-muted-foreground">Capabilities you&apos;ll use</p>
                          <div className="flex flex-wrap gap-x-3 gap-y-1">
                            {p.usesCapabilities.map((slug) => {
                              const a = resolveArticle(slug);
                              return a ? (
                                <Link key={slug} href={`/help/${slug}`} className="text-xs text-primary underline underline-offset-2">
                                  {a.title}
                                </Link>
                              ) : null;
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}
