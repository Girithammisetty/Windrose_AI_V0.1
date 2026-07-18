"use client";
import { use } from "react";
import { HelpArticleView } from "@/components/help/HelpArticleView";

/** A single platform/admin help article. Next 15 async params. */
export default function HelpArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <HelpArticleView slug={slug} />;
}
