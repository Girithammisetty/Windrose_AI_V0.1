"use client";
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import {
  useSemanticModelVersions,
  useSemanticModelVersion,
  useSemanticModelDetail,
} from "@/lib/graphql/hooks";
import { formatLocal } from "@/lib/utils";
import type { SemanticModelVersion, JSONValue } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

const STATUS_TO_CHIP: Record<string, string> = {
  DRAFT: "DRAFT",
  IN_REVIEW: "PENDING",
  PUBLISHED: "SUCCEEDED",
  REJECTED: "FAILED",
  SUPERSEDED: "CANCELLED",
};

/** Mirror of semantic-service's compute_diff (app/domain/definition.py): per
 * object kind, name-set adds/removes plus "same name, different content".
 * Used ONLY when a version carries no stored diff (the service records `diff`
 * at approve time — that stored value is surfaced verbatim when present). */
export function computeDefinitionDiff(
  old: Record<string, unknown> | null | undefined,
  next: Record<string, unknown> | null | undefined,
): Record<string, Record<string, string[]>> {
  const diff: Record<string, Record<string, string[]>> = { added: {}, removed: {}, changed: {} };
  const kinds = ["entities", "dimensions", "measures", "join_paths"] as const;
  for (const kind of kinds) {
    const items = (side: Record<string, unknown> | null | undefined) => {
      const list = (side?.[kind] ?? []) as { name?: string }[];
      const m = new Map<string, unknown>();
      for (const it of list) if (it?.name) m.set(it.name, it);
      return m;
    };
    const o = items(old);
    const n = items(next);
    const added = [...n.keys()].filter((k) => !o.has(k)).sort();
    const removed = [...o.keys()].filter((k) => !n.has(k)).sort();
    const changed = [...n.keys()]
      .filter((k) => o.has(k) && JSON.stringify(o.get(k)) !== JSON.stringify(n.get(k)))
      .sort();
    if (added.length) diff.added[kind] = added;
    if (removed.length) diff.removed[kind] = removed;
    if (changed.length) diff.changed[kind] = changed;
  }
  return diff;
}

function DiffSection({ label, entries }: { label: string; entries: Record<string, string[]> }) {
  const kinds = Object.keys(entries);
  if (kinds.length === 0) return null;
  return (
    <div className="text-sm">
      <p className="font-medium">{label}</p>
      <ul className="mt-1 space-y-1">
        {kinds.map((kind) => (
          <li key={kind} className="flex flex-wrap items-center gap-1">
            <span className="text-xs text-muted-foreground">{kind}:</span>
            {entries[kind].map((name) => (
              <Badge key={name} variant="secondary" className="font-mono text-[10px]">
                {name}
              </Badge>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function VersionRow({ v, onView }: { v: SemanticModelVersion; onView?: (v: SemanticModelVersion) => void }) {
  return (
    <div className="flex items-center justify-between rounded-md border p-3 text-sm">
      <div>
        <p className="font-medium">v{v.versionNo}</p>
        <p className="text-xs text-muted-foreground">{formatLocal(v.createdAt)}</p>
        {v.submittedBy && <p className="text-xs text-muted-foreground">{t("semantic.submittedBy", { who: v.submittedBy })}</p>}
        {v.decisionNote && <p className="text-xs text-muted-foreground">{t("semantic.decisionNote", { note: v.decisionNote })}</p>}
      </div>
      <div className="flex items-center gap-2">
        {onView && (
          <Button variant="outline" size="sm" onClick={() => onView(v)}>
            {t("semantic.version.view")}
          </Button>
        )}
        <StatusChip status={STATUS_TO_CHIP[v.status] ?? v.status} />
      </div>
    </div>
  );
}

export function VersionsPanel({ modelId }: { modelId: string }) {
  const query = useSemanticModelVersions(modelId);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [viewing, setViewing] = useState<SemanticModelVersion | null>(null);

  if (query.isLoading) return <p className="text-sm text-muted-foreground">{t("state.loading")}</p>;
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{t("state.empty")}</p>;

  return (
    <div className="space-y-2">
      {rows.map((v) => (
        <VersionRow key={v.id} v={v} onView={setViewing} />
      ))}
      {query.hasNextPage && (
        <Badge variant="outline" className="cursor-pointer" onClick={() => query.fetchNextPage()}>
          {t("action.loadMore")}
        </Badge>
      )}
      <VersionDetailDialog
        modelId={modelId}
        versionNo={viewing?.versionNo ?? null}
        onOpenChange={(o) => !o && setViewing(null)}
      />
    </div>
  );
}

/**
 * Full definition view + structured diff for one version. The stored `diff`
 * (recorded by the service at approve time vs the previously published
 * definition) is shown verbatim when present; otherwise the diff is computed
 * client-side against the CURRENTLY published version's definition.
 */
function VersionDetailDialog({
  modelId,
  versionNo,
  onOpenChange,
}: {
  modelId: string;
  versionNo: number | null;
  onOpenChange: (o: boolean) => void;
}) {
  const version = useSemanticModelVersion(modelId, versionNo);
  const model = useSemanticModelDetail(modelId);
  const publishedNo = model.data?.publishedVersionNo ?? null;
  const needsComputed =
    versionNo != null && !versionHasStoredDiff(version.data) && publishedNo != null && publishedNo !== versionNo;
  const published = useSemanticModelVersion(modelId, needsComputed ? publishedNo : null);

  const v = version.data;
  const storedDiff = versionHasStoredDiff(v) ? (v?.diff as Record<string, Record<string, string[]>>) : null;
  const computedDiff = useMemo(() => {
    if (storedDiff || !needsComputed || !v?.definitionJson || !published.data?.definitionJson) return null;
    return computeDefinitionDiff(
      published.data.definitionJson as Record<string, unknown>,
      v.definitionJson as Record<string, unknown>,
    );
  }, [storedDiff, needsComputed, v, published.data]);

  const diff = storedDiff ?? computedDiff;
  const diffEmpty =
    !diff ||
    (Object.keys(diff.added ?? {}).length === 0 &&
      Object.keys(diff.removed ?? {}).length === 0 &&
      Object.keys(diff.changed ?? {}).length === 0);

  return (
    <Dialog.Root open={versionNo != null} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">
            {t("semantic.version.definitionTitle", { version: versionNo ?? "" })}
          </Dialog.Title>

          {version.isLoading ? (
            <p className="mt-4 text-sm text-muted-foreground">{t("state.loading")}</p>
          ) : (
            <div className="mt-4 space-y-4">
              <div className="rounded-md border p-3" data-testid="version-diff">
                <p className="mb-2 text-sm font-medium">
                  {storedDiff
                    ? t("semantic.version.diffTitle", { version: versionNo ?? "" })
                    : t("semantic.version.comparePublished")}
                </p>
                {diffEmpty ? (
                  <p className="text-sm text-muted-foreground">{t("semantic.version.diffEmpty")}</p>
                ) : (
                  <div className="space-y-2">
                    <DiffSection label={t("semantic.version.diffAdded")} entries={diff.added ?? {}} />
                    <DiffSection label={t("semantic.version.diffRemoved")} entries={diff.removed ?? {}} />
                    <DiffSection label={t("semantic.version.diffChanged")} entries={diff.changed ?? {}} />
                  </div>
                )}
              </div>

              <pre className="max-h-96 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs" data-testid="version-definition">
                {JSON.stringify(v?.definitionJson ?? null, null, 2)}
              </pre>
            </div>
          )}

          <div className="mt-4 flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t("semantic.version.close")}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function versionHasStoredDiff(v?: { diff?: JSONValue } | null): boolean {
  return !!v && v.diff != null && typeof v.diff === "object" && Object.keys(v.diff as object).length > 0;
}
