"use client";
import { useMemo } from "react";
import type { ConnectorType } from "@/lib/graphql/types";
import { CATEGORY_ORDER, CATEGORY_LABELS } from "@/lib/connections/form";
import { CategoryIcon } from "./icons";
import { cn } from "@/lib/utils";

/**
 * Step 1 of the New Connection flow: pick a connector type, grouped by category
 * with an icon per group. Driven entirely by the bff catalog (no hardcoded list).
 */
export function ConnectorTypePicker({
  types,
  onPick,
  selected,
}: {
  types: ConnectorType[];
  onPick: (t: ConnectorType) => void;
  selected?: string;
}) {
  const groups = useMemo(() => {
    const byCat = new Map<string, ConnectorType[]>();
    for (const t of types) {
      const list = byCat.get(t.category) ?? [];
      list.push(t);
      byCat.set(t.category, list);
    }
    const known = CATEGORY_ORDER.filter((c) => byCat.has(c));
    const extra = [...byCat.keys()].filter((c) => !CATEGORY_ORDER.includes(c as never));
    return [...known, ...extra].map((c) => ({ category: c, items: byCat.get(c)! }));
  }, [types]);

  return (
    <div className="space-y-6" data-testid="connector-picker">
      {groups.map(({ category, items }) => (
        <section key={category}>
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <CategoryIcon category={category} className="size-4" />
            <span>{CATEGORY_LABELS[category] ?? category}</span>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {items.map((t) => (
              <button
                key={t.connectorType}
                type="button"
                data-connector={t.connectorType}
                aria-pressed={selected === t.connectorType}
                onClick={() => onPick(t)}
                className={cn(
                  "flex items-center gap-2 rounded-lg border p-3 text-left text-sm transition-colors hover:border-primary hover:bg-accent/50",
                  selected === t.connectorType && "border-primary bg-primary/10",
                )}
              >
                <CategoryIcon category={t.category} className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate font-medium">{t.displayName}</span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
