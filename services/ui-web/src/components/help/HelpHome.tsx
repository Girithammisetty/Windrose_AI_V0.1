"use client";
import Link from "next/link";
import { BookOpen, Shield, Sparkles } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Card, CardContent } from "@/components/ui/primitives";
import { useSession } from "@/lib/session/SessionContext";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { usePackInstalls } from "@/lib/graphql/hooks";
import {
  AREA_LABEL,
  AREA_ORDER,
  adminArticles,
  packGuide,
  personaForViewer,
  personaSlug,
  platformArticles,
  primaryPackName,
} from "@/lib/help/registry";
import { PersonaBadge } from "./PersonaBadge";

/**
 * Help Center home. Auto-scopes to the tenant's installed pack (usePackInstalls)
 * and highlights the signed-in persona (useCapabilities). Shows: a persona
 * banner, the pack guide card, the shared platform-capability guide, and — for
 * admins — the platform-admin guide.
 */
export function HelpHome() {
  const { workspaceId } = useSession();
  const caps = useCapabilities();
  const installs = usePackInstalls(workspaceId);

  const packName = primaryPackName(installs.data);
  const guide = packGuide(packName);
  const persona = personaForViewer(guide, caps.roles);

  const platform = platformArticles();
  const byArea = AREA_ORDER.map((area) => ({
    area,
    articles: platform.filter((a) => a.area === area),
  })).filter((g) => g.articles.length > 0);

  return (
    <div>
      <PageHeader
        title="Help & guides"
        description="Step-by-step guidance for your workspace — scoped to your solution and your role."
      />

      <AsyncBoundary
        isLoading={installs.isLoading || caps.isLoading}
        isError={installs.isError}
        error={installs.error}
        onRetry={() => installs.refetch()}
      >
        {/* Persona banner */}
        {persona && (
          <div
            className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3"
            data-testid="help-persona-banner"
          >
            <Sparkles className="size-4 text-primary" />
            <span className="text-sm">
              You&apos;re signed in as <strong>{persona.roleName}</strong> — {persona.tagline}
            </span>
            <Link
              href={`/help/pack#${personaSlug(persona.roleName)}`}
              className="ml-auto text-sm font-medium text-primary underline underline-offset-2"
            >
              Open your guide →
            </Link>
          </div>
        )}

        {/* Pack guide card */}
        <Card className="mb-6" data-testid="help-pack-card">
          <CardContent className="p-5">
            <div className="mb-1 flex items-center gap-2">
              <BookOpen className="size-5 text-primary" />
              <h2 className="text-base font-semibold">
                {guide ? `${guide.displayName} — your solution guide` : "Your solution guide"}
              </h2>
            </div>
            {guide ? (
              <>
                <p className="mb-3 max-w-3xl text-sm text-muted-foreground">
                  A role-by-role walkthrough of how your team works in Windrose.
                </p>
                <div className="mb-4 flex flex-wrap gap-1.5" data-testid="help-personas">
                  {guide.personas.map((p) => (
                    <PersonaBadge
                      key={p.roleName}
                      role={p.roleName}
                      active={persona?.roleName === p.roleName}
                    />
                  ))}
                </div>
                <Link
                  href="/help/pack"
                  className="text-sm font-medium text-primary underline underline-offset-2"
                >
                  Open the {guide.displayName} guide →
                </Link>
              </>
            ) : (
              <p className="max-w-3xl text-sm text-muted-foreground" data-testid="help-pack-missing">
                {packName
                  ? `A tailored guide for your ${packName} solution is coming soon. In the meantime, the platform guides below cover every capability you'll use.`
                  : "The platform guides below cover every capability in your workspace."}
              </p>
            )}
          </CardContent>
        </Card>

        {/* Platform capability guide */}
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">Using the platform</h2>
        <div className="space-y-5">
          {byArea.map((g) => (
            <section key={g.area}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {AREA_LABEL[g.area]}
              </h3>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {g.articles.map((a) => (
                  <ArticleCard key={a.slug} slug={a.slug} title={a.title} summary={a.summary} />
                ))}
              </div>
            </section>
          ))}
        </div>

        {/* Admin guide */}
        {caps.isAdmin && (
          <div className="mt-8" data-testid="help-admin-section">
            <div className="mb-2 flex items-center gap-2">
              <Shield className="size-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold text-muted-foreground">Platform administration</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {adminArticles().map((a) => (
                <ArticleCard key={a.slug} slug={a.slug} title={a.title} summary={a.summary} />
              ))}
            </div>
          </div>
        )}
      </AsyncBoundary>
    </div>
  );
}

function ArticleCard({ slug, title, summary }: { slug: string; title: string; summary: string }) {
  return (
    <Link href={`/help/${slug}`} className="group block" data-testid="help-article-card">
      <Card className="h-full transition-colors group-hover:border-primary/50">
        <CardContent className="p-4">
          <h4 className="mb-1 text-sm font-semibold group-hover:text-primary">{title}</h4>
          <p className="text-xs text-muted-foreground">{summary}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
