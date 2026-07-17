"use client";
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateCases } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { CaseRowInput } from "@/lib/graphql/types";

const SEVERITIES = ["low", "medium", "high", "critical"] as const;

/**
 * Create a worklist of cases from selected rows (case worklist model: each row
 * becomes one case anchored to (datasetUrn, rowPk), dedup-keyed so re-running
 * records a recurrence rather than a duplicate). `provenance` records where the
 * rows came from — a saved query (`queryUrn`) or a dashboard (`dashboardUrn`).
 * The backend's real 422/403 is surfaced verbatim.
 */
export function CreateCasesDialog({
  open,
  onOpenChange,
  datasetUrn,
  datasetVersion,
  queryUrn,
  dashboardUrn,
  rows,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  datasetUrn: string;
  datasetVersion?: string;
  queryUrn?: string;
  dashboardUrn?: string;
  /** The selected rows, already shaped as case rows (rowPk + projection). */
  rows: CaseRowInput[];
  onCreated?: (created: number, deduplicated: number) => void;
}) {
  const [severity, setSeverity] = useState<(typeof SEVERITIES)[number]>("medium");
  const [dueDate, setDueDate] = useState("");
  const [assignedToId, setAssignedToId] = useState("");
  const [description, setDescription] = useState("");
  const [banner, setBanner] = useState<string | null>(null);
  const [result, setResult] = useState<{ created: number; deduplicated: number } | null>(null);
  const createMutation = useCreateCases();

  useEffect(() => {
    if (open) {
      setSeverity("medium");
      // default due date: 14 days out (must be in the future — backend BR-12)
      const d = new Date();
      d.setDate(d.getDate() + 14);
      setDueDate(d.toISOString().slice(0, 10));
      setAssignedToId("");
      setDescription("");
      setBanner(null);
      setResult(null);
    }
  }, [open]);

  const submit = () => {
    setBanner(null);
    if (rows.length === 0) {
      setBanner("Select at least one row.");
      return;
    }
    if (!dueDate) {
      setBanner("A due date is required.");
      return;
    }
    createMutation.mutate(
      {
        input: {
          datasetUrn,
          datasetVersion,
          queryUrn,
          dashboardUrn,
          // send an end-of-day UTC timestamp so "today" still reads as future
          dueDate: `${dueDate}T23:59:59Z`,
          severity,
          assignedToId: assignedToId.trim() || undefined,
          description: description.trim() || undefined,
          rows,
        },
      },
      {
        onSuccess: (r) => {
          setResult({ created: r.created.length, deduplicated: r.deduplicated.length });
          onCreated?.(r.created.length, r.deduplicated.length);
        },
        onError: (e) => {
          setBanner(
            e instanceof GraphQLRequestError ? e.message : "Could not create cases.",
          );
        },
      },
    );
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[92vw] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-background p-5 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">Create cases</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-muted-foreground">
            {rows.length} row{rows.length === 1 ? "" : "s"} → {rows.length} case
            {rows.length === 1 ? "" : "s"}. Rows already tracked by a case are recorded as a
            recurrence, not duplicated.
          </Dialog.Description>

          {result ? (
            <div className="mt-4 space-y-3">
              <div className="rounded-md border bg-muted/40 p-3 text-sm">
                <p>
                  <span className="font-medium text-foreground">{result.created}</span> case
                  {result.created === 1 ? "" : "s"} created
                  {result.deduplicated > 0 && (
                    <>
                      {" · "}
                      <span className="font-medium text-foreground">{result.deduplicated}</span>{" "}
                      already tracked (recurrence)
                    </>
                  )}
                  .
                </p>
              </div>
              <div className="flex justify-end">
                <Button onClick={() => onOpenChange(false)}>Done</Button>
              </div>
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="cc-severity">Severity</Label>
                  <select
                    id="cc-severity"
                    value={severity}
                    onChange={(e) => setSeverity(e.target.value as (typeof SEVERITIES)[number])}
                    className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
                  >
                    {SEVERITIES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="cc-due">Due date</Label>
                  <Input
                    id="cc-due"
                    type="date"
                    value={dueDate}
                    onChange={(e) => setDueDate(e.target.value)}
                  />
                </div>
              </div>
              <div className="space-y-1">
                <Label htmlFor="cc-assignee">Assign to (user id, optional)</Label>
                <Input
                  id="cc-assignee"
                  value={assignedToId}
                  onChange={(e) => setAssignedToId(e.target.value)}
                  placeholder="leave blank for an unassigned queue"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="cc-desc">Description (optional)</Label>
                <Textarea
                  id="cc-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  placeholder="e.g. Q2 denied-claims review"
                />
              </div>

              {banner && (
                <p role="alert" className="text-sm text-destructive">
                  {banner}
                </p>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <Button variant="ghost" onClick={() => onOpenChange(false)}>
                  Cancel
                </Button>
                <Button onClick={submit} disabled={createMutation.isPending}>
                  {createMutation.isPending ? "Creating…" : `Create ${rows.length} case${rows.length === 1 ? "" : "s"}`}
                </Button>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
