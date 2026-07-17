"use client";
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { BookCheck, Plus, Search } from "lucide-react";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/primitives";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { useSession } from "@/lib/session/SessionContext";
import {
  useVerifiedQueries,
  useVerifiedQuerySearch,
  useCreateVerifiedQuery,
  useUpdateVerifiedQuery,
  useSubmitVerifiedQuery,
  useApproveVerifiedQuery,
  useRejectVerifiedQuery,
  useArchiveVerifiedQuery,
} from "@/lib/graphql/hooks";
import type { VerifiedQuery, VerifiedQuerySearchHit } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

// semantic-service stores statuses lowercase; the BFF filter arg passes through.
const VQ_STATUSES = ["draft", "pending_review", "approved", "rejected", "archived"] as const;
const STATUS_TO_CHIP: Record<string, string> = {
  DRAFT: "DRAFT",
  PENDING_REVIEW: "PENDING",
  APPROVED: "SUCCEEDED",
  REJECTED: "FAILED",
  ARCHIVED: "CANCELLED",
};
const EDITABLE = new Set(["DRAFT", "REJECTED"]);

/**
 * Verified NL↔SQL pairs (semantic-service /verified-queries, SEM-FR-040):
 * authoring + four-eyes review lifecycle. Approve/reject are hidden for the
 * pair's own author on top of the capability gate — the server enforces the
 * rule regardless; this only avoids a guaranteed-403 click.
 */
export function VerifiedQueriesPanel() {
  const { workspaceId, userId } = useSession();
  const { can } = useCapabilities();
  const [status, setStatus] = useState("");
  const vars = useMemo(
    () => ({ workspaceId: workspaceId || undefined, status: status || undefined }),
    [workspaceId, status],
  );
  const query = useVerifiedQueries(vars);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const submitMutation = useSubmitVerifiedQuery();
  const approveMutation = useApproveVerifiedQuery();
  const rejectMutation = useRejectVerifiedQuery();
  const archiveMutation = useArchiveVerifiedQuery();

  const [banner, setBanner] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<VerifiedQuery | null>(null);
  const [rejecting, setRejecting] = useState<VerifiedQuery | null>(null);
  const [rejectNote, setRejectNote] = useState("");
  const [toArchive, setToArchive] = useState<VerifiedQuery | null>(null);

  const notice = (msg: string) => setBanner(msg);
  const onErr = (e: unknown) => setBanner((e as Error).message);

  const columns: Column<VerifiedQuery>[] = [
    {
      id: "nl",
      header: t("vq.nl"),
      cell: (v) => <span className="truncate text-sm font-medium">{v.nlText}</span>,
    },
    {
      id: "status",
      header: t("vq.status"),
      width: 140,
      cell: (v) => <StatusChip status={STATUS_TO_CHIP[v.status] ?? v.status} />,
    },
    { id: "updated", header: t("vq.updatedAt"), width: 160, cell: (v) => formatLocal(v.updatedAt) },
    {
      id: "actions",
      header: "",
      width: 330,
      cell: (v) => {
        const isAuthor = !!v.submittedBy && v.submittedBy === userId;
        return (
          <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
            {EDITABLE.has(v.status) && (
              <Can gate={FEATURE_GATES.updateVerifiedQuery}>
                <Button variant="ghost" size="sm" onClick={() => { setEditing(v); setFormOpen(true); }}>
                  {t("vq.edit")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={submitMutation.isPending}
                  onClick={() =>
                    submitMutation.mutate(v.id, { onSuccess: () => notice(t("vq.submitted")), onError: onErr })
                  }
                >
                  {t("vq.submit")}
                </Button>
              </Can>
            )}
            {v.status === "PENDING_REVIEW" &&
              (isAuthor ? (
                can(FEATURE_GATES.approveVerifiedQuery) && (
                  <span className="self-center text-xs text-muted-foreground" role="status">
                    {t("vq.fourEyes")}
                  </span>
                )
              ) : (
                <Can gate={FEATURE_GATES.approveVerifiedQuery}>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={approveMutation.isPending}
                    onClick={() =>
                      approveMutation.mutate(v.id, { onSuccess: () => notice(t("vq.approved")), onError: onErr })
                    }
                  >
                    {t("vq.approve")}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => { setRejecting(v); setRejectNote(""); }}>
                    {t("vq.reject")}
                  </Button>
                </Can>
              ))}
            {v.status !== "ARCHIVED" && (
              <Can gate={FEATURE_GATES.updateVerifiedQuery}>
                <Button variant="ghost" size="sm" onClick={() => setToArchive(v)}>
                  {t("vq.archive")}
                </Button>
              </Can>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <label className="flex items-center gap-1 text-sm">
          <span className="text-muted-foreground">{t("vq.filterStatus")}</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by verified-query status"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="">all</option>
            {VQ_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <Can gate={FEATURE_GATES.createVerifiedQuery}>
          <Button size="sm" onClick={() => { setEditing(null); setFormOpen(true); }}>
            <Plus /> {t("vq.new")}
          </Button>
        </Can>
      </div>

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="vq-banner">
          {banner}
        </div>
      )}

      <Can gate={FEATURE_GATES.viewVerifiedQueries}>
        <VerifiedQuerySearch workspaceId={workspaceId || undefined} />
      </Can>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("vq.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.createVerifiedQuery}>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => { setEditing(null); setFormOpen(true); }}>
              <Plus /> {t("vq.new")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("vq.title")}
          rows={rows}
          columns={columns}
          rowId={(v) => v.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          estimateRowHeight={52}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <BookCheck className="size-8" />
              <p>{t("vq.emptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>

      <VerifiedQueryDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editing={editing}
        onSaved={(msg) => {
          setFormOpen(false);
          setEditing(null);
          notice(msg);
        }}
      />

      {/* Reject with an optional note (four-eyes on the server too). */}
      <ConfirmDialog
        open={!!rejecting}
        onOpenChange={(o) => !o && setRejecting(null)}
        title={t("vq.reject")}
        description={rejecting?.nlText}
        confirmLabel={t("vq.reject")}
        destructive
        onConfirm={() => {
          if (rejecting)
            rejectMutation.mutate(
              { id: rejecting.id, note: rejectNote.trim() || undefined },
              {
                onSuccess: () => notice(t("vq.rejected")),
                onError: onErr,
                onSettled: () => setRejecting(null),
              },
            );
        }}
      >
        <div className="mt-3 space-y-1.5">
          <Label htmlFor="vq-reject-note">{t("vq.rejectNote")}</Label>
          <Textarea id="vq-reject-note" rows={2} value={rejectNote} onChange={(e) => setRejectNote(e.target.value)} />
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={!!toArchive}
        onOpenChange={(o) => !o && setToArchive(null)}
        title={t("vq.archive")}
        description={t("vq.archiveConfirm")}
        confirmLabel={t("vq.archive")}
        destructive
        onConfirm={() => {
          if (toArchive)
            archiveMutation.mutate(toArchive.id, {
              onSuccess: () => notice(t("vq.archived")),
              onError: onErr,
              onSettled: () => setToArchive(null),
            });
        }}
      />
    </div>
  );
}

const SEARCH_DEBOUNCE_MS = 300;

/** Semantic search over APPROVED pairs (SEM-FR-041): a debounced question box +
 * ranked results (NL question + SQL + ANN score). Read-capability gated by the
 * caller; hard tenant+workspace scoped server-side. */
function VerifiedQuerySearch({ workspaceId }: { workspaceId?: string }) {
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(term), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [term]);

  const search = useVerifiedQuerySearch({ query: debounced, workspaceId, topK: 5 });
  const hits = search.data ?? [];
  const active = !!workspaceId && debounced.trim().length > 0;

  return (
    <div className="mb-4 rounded-md border bg-muted/20 p-3">
      <Label htmlFor="vq-search" className="text-sm font-medium">
        {t("vq.search")}
      </Label>
      <div className="relative mt-1.5">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          id="vq-search"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          placeholder={t("vq.searchPlaceholder")}
          className="pl-8"
          disabled={!workspaceId}
        />
      </div>
      {!workspaceId ? (
        <p className="mt-2 text-xs text-muted-foreground">{t("vq.searchNoWorkspace")}</p>
      ) : !active ? (
        <p className="mt-2 text-xs text-muted-foreground">{t("vq.searchHint")}</p>
      ) : search.isError ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {(search.error as Error).message}
        </p>
      ) : search.isLoading ? (
        <p className="mt-2 text-xs text-muted-foreground">{t("vq.searchHint")}</p>
      ) : hits.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">{t("vq.searchEmpty")}</p>
      ) : (
        <ul className="mt-2 space-y-2" data-testid="vq-search-results">
          {hits.map((hit) => (
            <VerifiedQuerySearchResult key={hit.id} hit={hit} />
          ))}
        </ul>
      )}
    </div>
  );
}

function VerifiedQuerySearchResult({ hit }: { hit: VerifiedQuerySearchHit }) {
  return (
    <li className="rounded-md border bg-background p-2.5">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium">{hit.nlText}</span>
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground">
          {t("vq.searchScore", { score: hit.score.toFixed(3) })}
        </span>
      </div>
      <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 font-mono text-xs">
        {hit.sqlText}
      </pre>
      {hit.tags.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {hit.tags.map((tag) => (
            <span key={tag} className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {tag}
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

/** Author/edit a pair. Edit is only offered for draft/rejected (a rejected pair
 * returns to draft on save — semantic-service state machine). */
function VerifiedQueryDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  editing: VerifiedQuery | null;
  onSaved: (msg: string) => void;
}) {
  const [nl, setNl] = useState("");
  const [sql, setSql] = useState("");
  const [model, setModel] = useState("");
  const [tags, setTags] = useState("");
  const [banner, setBanner] = useState<string | null>(null);

  const createMutation = useCreateVerifiedQuery();
  const updateMutation = useUpdateVerifiedQuery();
  const pending = createMutation.isPending || updateMutation.isPending;

  useEffect(() => {
    if (!open) return;
    setBanner(null);
    createMutation.reset();
    updateMutation.reset();
    setNl(editing?.nlText ?? "");
    setSql(editing?.sqlText ?? "");
    setModel("");
    setTags((editing?.tags ?? []).join(", "));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open/editing change
  }, [open, editing]);

  const submit = () => {
    setBanner(null);
    if (!nl.trim()) {
      setBanner(t("vq.nlRequired"));
      return;
    }
    if (!sql.trim()) {
      setBanner(t("vq.sqlRequired"));
      return;
    }
    const tagList = tags
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    if (editing) {
      updateMutation.mutate(
        { id: editing.id, input: { nlText: nl.trim(), sqlText: sql, tags: tagList } },
        { onSuccess: () => onSaved(t("vq.updated")) },
      );
    } else {
      createMutation.mutate(
        { nlText: nl.trim(), sqlText: sql, model: model.trim() || undefined, tags: tagList },
        { onSuccess: () => onSaved(t("vq.created")) },
      );
    }
  };

  const error = (createMutation.error ?? updateMutation.error) as Error | null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">
            {editing ? t("vq.editTitle") : t("vq.new")}
          </Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="vq-nl">{t("vq.nl")}</Label>
              <Textarea
                id="vq-nl"
                rows={2}
                value={nl}
                onChange={(e) => setNl(e.target.value)}
                placeholder={t("vq.nlPlaceholder")}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="vq-sql">{t("vq.sql")}</Label>
              <Textarea
                id="vq-sql"
                rows={4}
                className="font-mono text-xs"
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                spellCheck={false}
              />
            </div>
            {!editing && (
              <div className="space-y-1.5">
                <Label htmlFor="vq-model">{t("vq.model")}</Label>
                <Input id="vq-model" value={model} onChange={(e) => setModel(e.target.value)} />
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="vq-tags">{t("vq.tags")}</Label>
              <Input id="vq-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Comma-separated" />
            </div>
            {banner && <p className="text-xs text-destructive">{banner}</p>}
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("action.cancel")}
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? t("vq.creating") : editing ? t("vq.save") : t("vq.create")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
