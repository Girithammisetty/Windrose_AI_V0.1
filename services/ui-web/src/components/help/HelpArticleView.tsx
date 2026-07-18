"use client";
import Link from "next/link";
import { ArrowLeft, ArrowRight, ArrowLeft as Back } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/primitives";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import {
  articleAppliesToRole,
  resolveArticle,
  siblings,
  type HelpArticle,
} from "@/lib/help/registry";
import { MarkdownView } from "./MarkdownView";

/** Renders one platform/admin help article by slug, with persona badges,
 * related links, and prev/next within its area. */
export function HelpArticleView({ slug }: { slug: string }) {
  const article = resolveArticle(slug);
  const caps = useCapabilities();

  if (!article) {
    return (
      <div>
        <PageHeader title="Article not found" description="This guide doesn't exist." />
        <Button asChild variant="ghost" size="sm">
          <Link href="/help"><Back /> Back to Help</Link>
        </Button>
      </div>
    );
  }

  const { prev, next } = siblings(article);
  const related = (article.related ?? [])
    .map((s) => resolveArticle(s))
    .filter((a): a is HelpArticle => !!a);
  const forYou = articleAppliesToRole(article, caps.roles);

  return (
    <div className="max-w-3xl">
      <Button asChild variant="ghost" size="sm" className="mb-2 -ml-2">
        <Link href="/help"><ArrowLeft /> Help &amp; guides</Link>
      </Button>

      <PageHeader title={article.title} description={article.summary} />

      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {article.audience === "admin" ? (
          <Badge variant="secondary">Admin</Badge>
        ) : article.audience === "all" ? (
          <Badge variant="secondary">Everyone</Badge>
        ) : (
          article.audience.map((r) => <Badge key={r} variant="secondary">{r}</Badge>)
        )}
        {forYou && article.audience !== "all" && (
          <Badge className="bg-primary text-primary-foreground">Applies to you</Badge>
        )}
      </div>

      <article>
        <MarkdownView>{article.body}</MarkdownView>
      </article>

      {related.length > 0 && (
        <div className="mt-8 border-t pt-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Related</h3>
          <ul className="flex flex-col gap-1">
            {related.map((r) => (
              <li key={r.slug}>
                <Link href={`/help/${r.slug}`} className="text-sm text-primary underline underline-offset-2">
                  {r.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6 flex items-center justify-between gap-2 border-t pt-4">
        {prev ? (
          <Button asChild variant="ghost" size="sm">
            <Link href={`/help/${prev.slug}`}><ArrowLeft /> {prev.title}</Link>
          </Button>
        ) : <span />}
        {next ? (
          <Button asChild variant="ghost" size="sm">
            <Link href={`/help/${next.slug}`}>{next.title} <ArrowRight /></Link>
          </Button>
        ) : <span />}
      </div>
    </div>
  );
}
