"use client";
import { HelpHome } from "@/components/help/HelpHome";

/** Help Center home — pack-scoped, persona-highlighted guides. Public to every
 * signed-in user (see registry ROUTE_RULES: /help is unlisted = public). */
export default function HelpPage() {
  return <HelpHome />;
}
