"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Search, CornerDownLeft, ArrowUp, ArrowDown, Database, LayoutDashboard,
  TableProperties, type LucideIcon,
} from "lucide-react";
import { NAV_ITEMS, FEATURE_GATES, cap, type Gate } from "@/lib/authz/registry";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { useSession } from "@/lib/session/SessionContext";
import { t } from "@/lib/i18n/messages";
import { graphqlRequest } from "@/lib/graphql/client";
import * as ops from "@/lib/graphql/operations";

/** The custom event the TopBar (or any surface) dispatches to open the palette. */
export const CMDK_EVENT = "windrose:cmdk";

interface Item {
  id: string;
  label: string;
  hint?: string;
  section: string;
  icon: LucideIcon;
  run: () => void;
}

const norm = (s: string) => s.toLowerCase().trim();
const matches = (text: string, q: string) => norm(text).includes(norm(q));

/**
 * ⌘K command palette + global search. Keyboard-first: open with ⌘K / Ctrl+K,
 * type to filter navigation and quick actions, and (2+ chars) search across
 * datasets, dashboards and decision tables. Every entry is capability-gated —
 * the palette only ever offers what the viewer can actually reach, and the
 * search only queries services the viewer can read.
 */
export function CommandPalette() {
  const router = useRouter();
  const { can } = useCapabilities();
  const { workspaceId } = useSession();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => { setOpen(false); setQuery(""); setActive(0); }, []);
  const go = useCallback((href: string) => { close(); router.push(href); }, [close, router]);

  // Global open shortcut + the TopBar's open event.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(CMDK_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(CMDK_EVENT, onOpen);
    };
  }, []);

  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  // ---- capability gates for the searchable services -------------------------
  const canData = can(cap("dataset.dataset.list"));
  const canDash = can(cap("chart.dashboard.read"));
  const canDec = can(cap("case.disposition.read"));

  const q = query.trim();
  const search = useQuery({
    queryKey: ["cmdk", "search", workspaceId, q],
    enabled: open && q.length >= 2,
    staleTime: 15_000,
    queryFn: async () => {
      const [ds, dsh, dm] = await Promise.all([
        canData ? graphqlRequest<ops.DatasetsResult>(ops.DATASETS, { first: 6, q }) : null,
        canDash ? graphqlRequest<ops.DashboardsResult>(ops.DASHBOARDS, { workspaceId, first: 50 }) : null,
        canDec ? graphqlRequest<ops.DecisionModelsResult>(ops.DECISION_MODELS) : null,
      ]);
      return {
        datasets: (ds?.datasets.nodes ?? []).slice(0, 6),
        dashboards: (dsh?.dashboards.nodes ?? []).filter((d) => matches(d.title ?? "", q)).slice(0, 6),
        decisions: (dm?.decisionModels ?? []).filter((m) => matches(m.name ?? "", q)).slice(0, 6),
      };
    },
  });

  // ---- build the flat, ordered item list ------------------------------------
  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];

    // Navigation — capability-gated, filtered by the query.
    for (const nav of NAV_ITEMS) {
      if (!can(nav.gate)) continue;
      const label = t(nav.label);
      if (q && !matches(label, q)) continue;
      out.push({ id: `nav:${nav.key}`, label, section: "Go to", icon: nav.icon, run: () => go(nav.href) });
    }

    // Quick actions — each gated on the capability that unlocks it.
    for (const a of QUICK_ACTIONS) {
      if (!can(a.gate)) continue;
      if (q && !matches(a.label, q)) continue;
      out.push({ id: `act:${a.href}`, label: a.label, hint: "Action", section: "Actions", icon: a.icon, run: () => go(a.href) });
    }

    // Live search results (2+ chars).
    if (q.length >= 2 && search.data) {
      for (const d of search.data.datasets) {
        out.push({ id: `ds:${d.id}`, label: d.name, hint: "Dataset", section: "Datasets", icon: Database, run: () => go(`/data/datasets/${d.id}`) });
      }
      for (const d of search.data.dashboards) {
        out.push({ id: `dsh:${d.id}`, label: d.title ?? d.id, hint: "Dashboard", section: "Dashboards", icon: LayoutDashboard, run: () => go(`/dashboards/${d.id}`) });
      }
      for (const m of search.data.decisions) {
        out.push({ id: `dm:${m.id}`, label: m.name, hint: "Decision table", section: "Decision tables", icon: TableProperties, run: () => go(`/decisions`) });
      }
    }
    return out;
  }, [can, q, search.data, go]);

  useEffect(() => { setActive(0); }, [q, search.data]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); items[active]?.run(); }
  };

  // Keep the active row scrolled into view.
  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`);
    row?.scrollIntoView?.({ block: "nearest" });
  }, [active, open]);

  if (!open) return null;

  // Group items by section for rendering, preserving flat indices for nav.
  let idx = -1;
  const sections: { name: string; rows: { item: Item; i: number }[] }[] = [];
  for (const item of items) {
    idx += 1;
    const last = sections[sections.length - 1];
    if (!last || last.name !== item.section) sections.push({ name: item.section, rows: [{ item, i: idx }] });
    else last.rows.push({ item, i: idx });
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-background/70 p-4 pt-[12vh] backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}
    >
      <div className="w-full max-w-xl overflow-hidden rounded-xl border bg-card shadow-2xl" onKeyDown={onKeyDown}>
        <div className="flex items-center gap-2 border-b px-3">
          <Search className="size-4 text-muted-foreground" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search or jump to…  (datasets, dashboards, decision tables)"
            className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            aria-label="Command palette search"
            aria-controls="cmdk-list"
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className="hidden rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline">esc</kbd>
        </div>

        <div id="cmdk-list" ref={listRef} className="max-h-[52vh] overflow-y-auto p-1.5" role="listbox">
          {items.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-muted-foreground">
              {search.isFetching ? "Searching…" : q ? `No matches for “${q}”.` : "Type to search."}
            </p>
          ) : (
            sections.map((sec) => (
              <div key={sec.name} className="mb-1">
                <p className="px-2.5 pb-1 pt-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{sec.name}</p>
                {sec.rows.map(({ item, i }) => {
                  const Icon = item.icon;
                  const isActive = i === active;
                  return (
                    <button
                      key={item.id}
                      data-idx={i}
                      role="option"
                      aria-selected={isActive}
                      onMouseMove={() => setActive(i)}
                      onClick={item.run}
                      className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm ${
                        isActive ? "bg-accent text-accent-foreground" : "text-foreground"
                      }`}
                    >
                      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      {item.hint && <span className="shrink-0 text-xs text-muted-foreground">{item.hint}</span>}
                      {isActive && <CornerDownLeft className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="flex items-center gap-3 border-t px-3 py-2 font-mono text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1"><ArrowUp className="size-3" /><ArrowDown className="size-3" /> navigate</span>
          <span className="flex items-center gap-1"><CornerDownLeft className="size-3" /> open</span>
          <span className="ml-auto">⌘K</span>
        </div>
      </div>
    </div>
  );
}

/** Curated create/act shortcuts, each gated on the capability that unlocks it. */
const QUICK_ACTIONS: { label: string; href: string; icon: LucideIcon; gate: Gate }[] = [
  { label: "Upload data", href: "/data/upload", icon: Database, gate: cap("ingestion.ingestion.create") },
  { label: "New dashboard", href: "/dashboards", icon: LayoutDashboard, gate: FEATURE_GATES.createDashboard },
  { label: "New decision table", href: "/decisions", icon: TableProperties, gate: cap("case.disposition.create") },
  { label: "New semantic model", href: "/data/semantic-models/new", icon: Database, gate: cap("semantic.model.create") },
  { label: "Run entity resolution", href: "/data/entity-resolution", icon: Database, gate: FEATURE_GATES.runEntityResolution },
  { label: "New pipeline", href: "/data/pipelines/new", icon: Database, gate: cap("pipeline.template.create") },
];
