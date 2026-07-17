import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * True if a single Tailwind token would hide/suppress an element (display,
 * visibility, opacity, screen-reader-only, or a zero box). Variant and important
 * prefixes are stripped before matching, so `md:hidden`, `hover:invisible`, and
 * `!hidden` are all caught.
 */
function isSuppressorToken(token: string): boolean {
  const leaf = (token.replace(/^!/, "").split(":").pop() ?? token).toLowerCase();
  if (leaf === "hidden" || leaf === "invisible" || leaf === "collapse" || leaf === "sr-only") return true;
  if (/^opacity-(0|\[0%?\]|\[0(\.0+)?\])$/.test(leaf)) return true;
  if (/^(w|h|size|min-w|min-h|max-w|max-h)-(0|\[0[a-z%]*\])$/.test(leaf)) return true;
  // Arbitrary properties, e.g. [display:none] / [visibility:hidden] / [opacity:0].
  if (/^\[(display:none|visibility:(hidden|collapse)|opacity:0(\.0+)?)\]$/.test(leaf)) return true;
  return false;
}

/**
 * Merge a caller className into a base, but STRIP any utility that would hide the
 * element. Used to make legally-mandated disclosure surfaces (AI labels, EU AI
 * Act Art. 50) non-suppressible BY CONSTRUCTION: no caller className, tenant
 * theme, or twMerge display-utility override can remove them. A hidden token in
 * the caller className is silently dropped rather than allowed to win the merge.
 */
export function nonSuppressibleClassName(base: string, className?: string): string {
  const safe = (className ?? "")
    .split(/\s+/)
    .filter((t) => t.length > 0 && !isSuppressorToken(t))
    .join(" ");
  return cn(base, safe);
}

/** Render a timestamp in the viewer's local time (BR-9); UTC exposed on hover. */
export function formatLocal(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

export function utcIso(value?: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString();
}

export function formatUsd(n?: number | null): string {
  if (n == null) return "—";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: n < 1 ? 4 : 2,
  }).format(n);
}

export function formatNumber(n?: number | null): string {
  if (n == null) return "—";
  return new Intl.NumberFormat().format(n);
}

/** Human-readable byte size (Tier 4a: execution scan cost, dataset versions). */
export function formatBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${formatNumber(n)} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let u = -1;
  do {
    v /= 1024;
    u += 1;
  } while (v >= 1024 && u < units.length - 1);
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[u]}`;
}

/** Parse the resource-type segment out of a Windrose URN: wr:<tenant>:<type>:<path>. */
export function urnParts(urn?: string | null): { tenant?: string; type?: string; path?: string } {
  if (!urn) return {};
  const [, tenant, type, ...rest] = urn.split(":");
  return { tenant, type, path: rest.join(":") };
}
