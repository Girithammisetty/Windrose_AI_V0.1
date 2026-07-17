"use client";
import * as Dialog from "@radix-ui/react-dialog";
import { Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { useSavedQueryVersions } from "@/lib/graphql/hooks";
import type { SavedQuery, SavedQueryVersion } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/**
 * Immutable version history of a saved query (query-service GET
 * /queries/{id}/versions). Each row shows its SQL; "Load into editor" hands
 * the version's SQL back to the console for a re-run or a new edit.
 */
export function VersionsDialog({
  query,
  onOpenChange,
  onLoad,
}: {
  query: SavedQuery | null;
  onOpenChange: (o: boolean) => void;
  onLoad: (v: SavedQueryVersion) => void;
}) {
  const versions = useSavedQueryVersions(query?.id ?? "", !!query);
  const rows = versions.data?.pages.flatMap((p) => p.nodes) ?? [];

  return (
    <Dialog.Root open={!!query} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">
            {t("queries.versionsTitle")}
            {query && <span className="ml-2 text-sm font-normal text-muted-foreground">{query.name}</span>}
          </Dialog.Title>
          <div className="mt-4">
            <AsyncBoundary
              isLoading={versions.isLoading}
              isError={versions.isError}
              error={versions.error}
              isEmpty={rows.length === 0}
              emptyTitle={t("queries.versionsEmpty")}
              onRetry={() => versions.refetch()}
            >
              <ul className="space-y-3" aria-label={t("queries.versionsTitle")}>
                {rows.map((v) => (
                  <li key={v.id} className="rounded-md border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="flex items-center gap-2 text-sm">
                        <Badge variant={v.versionNo === query?.versionNo ? "default" : "secondary"}>
                          v{v.versionNo}
                        </Badge>
                        <span className="text-xs text-muted-foreground">{formatLocal(v.createdAt)}</span>
                      </p>
                      <Button size="sm" variant="outline" onClick={() => onLoad(v)}>
                        {t("queries.loadVersion")}
                      </Button>
                    </div>
                    {v.sqlText && (
                      <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted/40 p-2 font-mono text-xs">
                        {v.sqlText}
                      </pre>
                    )}
                  </li>
                ))}
              </ul>
              {versions.hasNextPage && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-2"
                  disabled={versions.isFetchingNextPage}
                  onClick={() => versions.fetchNextPage()}
                >
                  {t("action.loadMore")}
                </Button>
              )}
            </AsyncBoundary>
          </div>
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
