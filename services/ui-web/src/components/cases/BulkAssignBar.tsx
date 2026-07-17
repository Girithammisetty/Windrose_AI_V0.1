"use client";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { useSelection, useToasts } from "@/stores/ui";
import { BULK_APPROVE_CAP } from "@/lib/agentic/proposals";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useUsers, useBulkAssignCases } from "@/lib/graphql/hooks";
import { t } from "@/lib/i18n/messages";

/**
 * Bulk operations bar for the case list (UI-FR-045, BR-8). Selection lives in
 * Zustand keyed by filter signature. Real server-side bulk via case-service's
 * POST /cases/bulk (operation=assign) — id-based only (≤BULK_APPROVE_CAP per
 * action); "select all matching filter" (the server's async filter-based path)
 * is not wired here. Reports the REAL per-case succeeded/failed result, never
 * an optimistic "queued" toast.
 */
export function BulkAssignBar({ caseCount }: { caseCount: number }) {
  const { ids, clear } = useSelection();
  const push = useToasts((s) => s.push);
  const { can } = useCapabilities();
  const [confirm, setConfirm] = useState(false);
  const [assigneeId, setAssigneeId] = useState("");
  const usersQuery = useUsers();
  const users = useMemo(() => usersQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [usersQuery.data]);
  const bulkAssign = useBulkAssignCases();
  // Bulk assign is a write the server gates on case.case.assign; hide the
  // control for personas (e.g. adjuster) who can't perform it.
  if (ids.size === 0 || !can(FEATURE_GATES.bulkAssignCases)) return null;

  const capped = ids.size > BULK_APPROVE_CAP;
  const batch = Array.from(ids).slice(0, BULK_APPROVE_CAP);

  const runBulkAssign = () => {
    if (!assigneeId) return;
    bulkAssign.mutate(
      { caseIds: batch, assigneeId },
      {
        onSuccess: (data) => {
          setConfirm(false);
          const { succeededIds, failed } = data.bulkAssignCases;
          push({
            title: t("cases.bulk.result", { succeeded: succeededIds.length, failed: failed.length }),
            variant: failed.length === 0 ? "success" : succeededIds.length === 0 ? "error" : "default",
          });
          clear();
          setAssigneeId("");
        },
        onError: () => {
          setConfirm(false);
          push({ title: t("cases.bulk.allFailed"), variant: "error" });
        },
      },
    );
  };

  return (
    <div className="mb-3 flex items-center gap-3 rounded-md border bg-accent/40 px-3 py-2 text-sm">
      <span className="font-medium">{ids.size} selected</span>
      <Button size="sm" variant="secondary" onClick={() => setConfirm(true)}>
        {t("cases.bulk.assign")}
      </Button>
      <Button size="sm" variant="ghost" onClick={clear}>
        {t("cases.bulk.clear")}
      </Button>
      {capped && <span className="text-xs text-muted-foreground">{t("cases.bulk.cap", { count: BULK_APPROVE_CAP })}</span>}
      <span className="ml-auto text-xs text-muted-foreground">{t("cases.bulk.loaded", { count: caseCount })}</span>

      <ConfirmDialog
        open={confirm}
        onOpenChange={(o) => {
          setConfirm(o);
          if (!o) setAssigneeId("");
        }}
        title={t("cases.bulk.confirmTitle", { count: batch.length })}
        description={t("cases.bulk.confirmDesc")}
        confirmLabel={t("cases.bulk.apply")}
        onConfirm={runBulkAssign}
      >
        <div className="mt-3 space-y-1.5">
          <label htmlFor="bulk-assignee" className="text-xs text-muted-foreground">
            {t("cases.bulk.assignTo")}
          </label>
          <select
            id="bulk-assignee"
            value={assigneeId}
            onChange={(e) => setAssigneeId(e.target.value)}
            disabled={bulkAssign.isPending}
            className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="">{t("cases.bulk.pickAssignee")}</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.fullName || u.email}
              </option>
            ))}
          </select>
        </div>
      </ConfirmDialog>
    </div>
  );
}
