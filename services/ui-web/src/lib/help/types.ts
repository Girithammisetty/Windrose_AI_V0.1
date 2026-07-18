/**
 * In-product Help Center — content model (UI help, bundled + client-filtered).
 *
 * Content is authored as structured data with Markdown bodies and filtered in the
 * browser by the tenant's installed pack (usePackInstalls) and the signed-in
 * persona (useCapabilities). No backend: nothing here is a security boundary —
 * the services still enforce every action.
 *
 * Two layers, so we never duplicate a guide 130 times:
 *  - PLATFORM capability articles — one per Core surface (worklist, cockpit,
 *    approvals/four-eyes, dashboards, datasets, pipelines, decision tables,
 *    copilot, entity resolution…). Identical across every pack.
 *  - PACK overlays — the pack's personas + what it ships + a domain
 *    day-in-the-life per persona that stitches the shared capabilities together.
 */

/** Which sidebar-ish grouping a capability article belongs to (for the index). */
export type HelpArea =
  | "getting-started"
  | "casework"
  | "insights"
  | "data"
  | "ml"
  | "admin";

/** Who an article is primarily for. `"all"` = every end user; `"admin"` = the
 * platform-admin guide; a string[] = specific persona/role names (matched
 * case-insensitively against the viewer's rbac roles for highlighting). */
export type Audience = "all" | "admin" | string[];

/** A single help article. `body` is Markdown (GFM: tables, checklists). */
export interface HelpArticle {
  /** URL slug under /help/<slug>. Stable — used for deep links + related refs. */
  slug: string;
  title: string;
  /** One-line summary shown on cards and in search. */
  summary: string;
  area: HelpArea;
  audience: Audience;
  /** Ordinal within its area (controls display + prev/next order). */
  order: number;
  /** Slugs of related articles to cross-link at the foot of the page. */
  related?: string[];
  /** Markdown body. */
  body: string;
}

/** A persona (role) inside a pack guide: a day-in-the-life walkthrough that
 * references the shared capability articles by slug. */
export interface PersonaGuide {
  /** Exact rbac role name from packs/<pack>/rbac/roles.yaml (used for
   * highlighting the signed-in user's role). */
  roleName: string;
  /** One-line "what this person does". */
  tagline: string;
  /** Ordered capability-article slugs this persona relies on, most-used first. */
  usesCapabilities: string[];
  /** Markdown: the persona's step-by-step day-in-the-life. */
  steps: string;
}

/** The overlay for one installed pack. */
export interface PackGuide {
  /** Pack manifest name, e.g. "card-disputes" — matches PackInstall.pack. */
  packName: string;
  /** Human display, e.g. "Card Disputes". */
  displayName: string;
  /** What the pack does (Markdown, a paragraph or two). */
  summary: string;
  /** What the pack ships (for the overview), keyed by kind → human list. */
  ships: { label: string; items: string[] }[];
  /** The pack's personas, in workflow order (intake → … → auditor). */
  personas: PersonaGuide[];
  /** Optional extra pack-specific articles (e.g. a regulatory-clock primer). */
  articles?: HelpArticle[];
}
