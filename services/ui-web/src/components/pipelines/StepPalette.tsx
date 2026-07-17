"use client";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { PipelineStepType } from "@/lib/graphql/types";
import { CATEGORY_ORDER, CATEGORY_LABELS } from "@/lib/pipelines/form";
import { Input } from "@/components/ui/primitives";
import { StepCategoryIcon } from "./icons";
import { cn } from "@/lib/utils";

/**
 * Left palette of the builder: every component from the `pipelineStepTypes`
 * catalog, grouped by category (io / data_prep / algorithm / utility) and
 * filterable. Algorithm-training components (`xgboost-train`, …) and
 * `hyperparameter-search` are `algorithm`-category entries of the SAME catalog,
 * so they add as ordinary nodes via nodeFromStep and serialize with real
 * component names. Click adds the node; the entry is also draggable onto the
 * canvas (drop places it at the cursor).
 */
export function StepPalette({
  steps,
  onAdd,
}: {
  steps: PipelineStepType[];
  onAdd: (step: PipelineStepType) => void;
}) {
  const [q, setQ] = useState("");

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const filtered = needle
      ? steps.filter(
          (s) =>
            s.displayName.toLowerCase().includes(needle) ||
            s.name.toLowerCase().includes(needle) ||
            s.category.toLowerCase().includes(needle),
        )
      : steps;

    const byCat = new Map<string, PipelineStepType[]>();
    for (const s of filtered) {
      const list = byCat.get(s.category) ?? [];
      list.push(s);
      byCat.set(s.category, list);
    }
    const known = CATEGORY_ORDER.filter((c) => byCat.has(c));
    const extra = [...byCat.keys()].filter((c) => !CATEGORY_ORDER.includes(c as never));
    return [...known, ...extra].map((c) => ({ category: c, items: byCat.get(c)! }));
  }, [steps, q]);

  return (
    <div className="flex h-full w-60 shrink-0 flex-col border-r" data-testid="step-palette">
      <div className="border-b p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            placeholder="Search steps…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search steps"
            className="pl-8"
          />
        </div>
      </div>
      <div className="flex-1 space-y-4 overflow-auto p-2">
        {groups.map(({ category, items }) => (
          <section key={category}>
            <div className="mb-1.5 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <StepCategoryIcon category={category} className="size-3.5" />
              <span>{CATEGORY_LABELS[category] ?? category}</span>
            </div>
            <div className="space-y-1">
              {items.map((s) => (
                <button
                  key={s.name}
                  type="button"
                  draggable
                  data-entry={s.name}
                  onDragStart={(ev) => ev.dataTransfer.setData("text/plain", s.name)}
                  onClick={() => onAdd(s)}
                  title={s.description ?? undefined}
                  className={cn(
                    "flex w-full flex-col gap-0.5 rounded-md border p-2 text-left text-sm transition-colors hover:border-primary hover:bg-accent/50",
                  )}
                >
                  <span className="truncate font-medium">{s.displayName}</span>
                  {s.description && <span className="truncate text-xs text-muted-foreground">{s.description}</span>}
                </button>
              ))}
            </div>
          </section>
        ))}
        {groups.length === 0 && <p className="px-1 text-sm text-muted-foreground">No steps match.</p>}
      </div>
    </div>
  );
}
