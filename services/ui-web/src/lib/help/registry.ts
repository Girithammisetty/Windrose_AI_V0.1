/**
 * Help Center registry — assembles articles, resolves slugs, maps a tenant's
 * installed pack → its overlay, and highlights the signed-in persona. Pure
 * functions + small helpers; no security decision here.
 */
import { PLATFORM_ARTICLES } from "./platform";
import { ADMIN_ARTICLES } from "./admin";
import { PACK_GUIDES, LIBRARY_PACKS } from "./packs";
import type { HelpArticle, HelpArea, PackGuide, PersonaGuide } from "./types";

export type { HelpArticle, PackGuide, PersonaGuide } from "./types";

/** Order areas appear on the help home. */
export const AREA_ORDER: HelpArea[] = [
  "getting-started",
  "casework",
  "insights",
  "data",
  "ml",
  "admin",
];

export const AREA_LABEL: Record<HelpArea, string> = {
  "getting-started": "Getting started",
  casework: "Casework",
  insights: "Insights",
  data: "Data",
  ml: "Machine learning",
  admin: "Platform administration",
};

/** Every non-pack article (platform capabilities + admin), stable order. */
export function baseArticles(): HelpArticle[] {
  return [...PLATFORM_ARTICLES, ...ADMIN_ARTICLES];
}

/** Platform capability articles for the given area (admin excluded here). */
export function platformArticles(): HelpArticle[] {
  return [...PLATFORM_ARTICLES].sort(byAreaThenOrder);
}

export function adminArticles(): HelpArticle[] {
  return [...ADMIN_ARTICLES].sort((a, b) => a.order - b.order);
}

function byAreaThenOrder(a: HelpArticle, b: HelpArticle): number {
  const ai = AREA_ORDER.indexOf(a.area);
  const bi = AREA_ORDER.indexOf(b.area);
  return ai !== bi ? ai - bi : a.order - b.order;
}

/** The overlay for a pack name, or null if not authored yet. */
export function packGuide(packName: string | null | undefined): PackGuide | null {
  if (!packName) return null;
  return PACK_GUIDES[packName] ?? null;
}

/**
 * Choose the tenant's headline pack from its installs. Prefers a non-library,
 * installed vertical pack. Returns the pack NAME (may have no overlay yet).
 */
export function primaryPackName(
  installs: { pack: string; status: string }[] | undefined | null,
): string | null {
  const rows = (installs ?? []).filter(
    (i) => i.status !== "uninstalled" && !LIBRARY_PACKS.has(i.pack),
  );
  if (rows.length === 0) return null;
  // A tenant almost always has exactly one vertical pack; if several, take the
  // first installed one deterministically (by name) so the choice is stable.
  const installed = rows.filter((i) => i.status === "installed");
  const pool = installed.length > 0 ? installed : rows;
  return [...pool].sort((a, b) => a.pack.localeCompare(b.pack))[0].pack;
}

/** Resolve a slug to an article across platform, admin, and pack overlays. */
export function resolveArticle(slug: string): HelpArticle | null {
  const base = baseArticles().find((a) => a.slug === slug);
  if (base) return base;
  for (const g of Object.values(PACK_GUIDES)) {
    const found = (g.articles ?? []).find((a) => a.slug === slug);
    if (found) return found;
  }
  return null;
}

/** Neighbouring articles for prev/next: same area, same admin-vs-end-user side. */
export function siblings(article: HelpArticle): { prev?: HelpArticle; next?: HelpArticle } {
  const isAdmin = article.audience === "admin";
  const inArea = baseArticles()
    .filter((a) => a.area === article.area && (a.audience === "admin") === isAdmin)
    .sort((a, b) => a.order - b.order);
  const i = inArea.findIndex((a) => a.slug === article.slug);
  return {
    prev: i > 0 ? inArea[i - 1] : undefined,
    next: i >= 0 && i < inArea.length - 1 ? inArea[i + 1] : undefined,
  };
}

/** Match a viewer role against a persona role name (case-insensitive, trimmed). */
export function roleMatches(roleName: string, viewerRoles: string[]): boolean {
  const want = roleName.trim().toLowerCase();
  return viewerRoles.some((r) => r.trim().toLowerCase() === want);
}

/**
 * The persona guide (if any) that matches the signed-in user's roles, so the
 * home page can highlight "you're a …" and deep-link their walkthrough.
 */
export function personaForViewer(
  guide: PackGuide | null,
  viewerRoles: string[],
): PersonaGuide | null {
  if (!guide) return null;
  return guide.personas.find((p) => roleMatches(p.roleName, viewerRoles)) ?? null;
}

/** Whether an article is relevant to a persona (audience "all" or lists the role). */
export function articleAppliesToRole(article: HelpArticle, viewerRoles: string[]): boolean {
  if (article.audience === "all") return true;
  if (article.audience === "admin") return false;
  return article.audience.some((a) => roleMatches(a, viewerRoles));
}

/** Slugify a persona role name for its in-page anchor / deep link. */
export function personaSlug(roleName: string): string {
  return roleName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
