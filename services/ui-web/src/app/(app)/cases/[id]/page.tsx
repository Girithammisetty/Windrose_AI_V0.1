"use client";
import { use, useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { Clock } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { UrnLink } from "@/components/primitives/UrnLink";
import { ProvenanceBadge } from "@/components/primitives/ProvenanceBadge";
import { AiLabel } from "@/components/primitives/AiLabel";
import { DiffView } from "@/components/primitives/DiffView";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { Card, CardContent, CardHeader, CardTitle, Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import {
  useCaseDetail, useUpdateCase, useUsers, useDispositions, useCaseTimeline,
  useAssignCase, useUnassignCase, useStartCase, useResolveCase, useReopenCase,
  useCloseCase, useEscalateCase, useAddCaseComment, useUpdateCaseComment, useDeleteCaseComment,
  useConnections, useCreateWriteback,
} from "@/lib/graphql/hooks";
import { useHubTopics } from "@/lib/realtime/useHubTopics";
import { useSession } from "@/lib/session/SessionContext";
import { useToasts } from "@/stores/ui";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { formatLocal } from "@/lib/utils";
import type { Case, CaseActivity, Severity } from "@/lib/graphql/types";

export default function CaseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useCaseDetail(id);
  const c = query.data?.case;
  // Task #78: "case.status"/"case.assigned" aren't a valid realtime-hub topic
  // (grammar is scheme:identifier) — this always 422'd. The real topic for a
  // single case's lifecycle events is run-status:<case-urn>; case-service's
  // events all carry resource_urn = the case's own URN (see routing.go's
  // "case" rule). Subscribe only once the case's urn is loaded.
  useHubTopics(c?.urn ? [`run-status:${c.urn}`] : []);

  return (
    <div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && !c}
        emptyTitle="Case not found"
        onRetry={() => query.refetch()}
      >
        {c && (
          <>
            <PageHeader
              title={c.title ?? `Case #${c.caseNumber}`}
              description={c.urn}
              actions={
                <div className="flex items-center gap-2">
                  <SlaChip due={c.dueDate} />
                  <StatusChip status={c.status} live />
                  <StatusChip status={c.severity} />
                </div>
              }
            />

            <CaseActionsBar c={c} />

            <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
              <Tabs.Root defaultValue="overview">
                <Tabs.List className="mb-3 flex gap-1 border-b" aria-label="Case sections">
                  {["overview", "details", "activity", "proposals"].map((v) => (
                    <Tabs.Trigger
                      key={v}
                      value={v}
                      className="border-b-2 border-transparent px-3 py-2 text-sm font-medium capitalize text-muted-foreground data-[state=active]:border-primary data-[state=active]:text-foreground"
                    >
                      {v}
                      {v === "proposals" && c.proposals.length > 0 && (
                        <span className="ml-1 rounded-full bg-ai px-1.5 text-xs text-ai-foreground">
                          {c.proposals.length}
                        </span>
                      )}
                    </Tabs.Trigger>
                  ))}
                </Tabs.List>

                <Tabs.Content value="overview">
                  <Card>
                    <CardContent className="grid grid-cols-2 gap-4 pt-4 text-sm">
                      <Field label="Case number" value={`#${c.caseNumber ?? "—"}`} />
                      <Field label="Created" value={formatLocal(c.createdAt)} />
                      <Field label="Assignee" value={c.assignee?.fullName ?? c.assignee?.email ?? "Unassigned"} />
                      <Field label="Due" value={formatLocal(c.dueDate)} />
                      {c.resolvedAt && <Field label="Resolved" value={formatLocal(c.resolvedAt)} />}
                      {c.closedAt && <Field label="Closed" value={formatLocal(c.closedAt)} />}
                      {c.resolutionNote && (
                        <div className="col-span-2">
                          <p className="mb-1 text-muted-foreground">Resolution note</p>
                          <p className="font-medium">{c.resolutionNote}</p>
                        </div>
                      )}
                      <div className="col-span-2">
                        <p className="mb-1 text-muted-foreground">Source dataset</p>
                        {c.sourceDataset ? <UrnLink urn={c.sourceDataset.urn} label={c.sourceDataset.name} /> : "—"}
                      </div>
                    </CardContent>
                  </Card>
                </Tabs.Content>

                <Tabs.Content value="details">
                  <CaseEditForm caseId={id} severity={c.severity} description={c.description} dueDate={c.dueDate} />
                </Tabs.Content>

                <Tabs.Content value="activity">
                  <ActivityPanel caseId={id} />
                </Tabs.Content>

                <Tabs.Content value="proposals">
                  <div className="space-y-3">
                    {c.proposals.length === 0 && (
                      <p className="text-sm text-muted-foreground">No triage suggestions yet.</p>
                    )}
                    {c.proposals.map((p) => (
                      <Card key={p.id}>
                        <CardHeader className="flex-row items-center gap-2">
                          <AiLabel />
                          <CardTitle className="text-sm">{p.tool}</CardTitle>
                          <ProvenanceBadge
                            provenance={{ agentKey: p.agentKey ?? undefined, sourceRunId: undefined, createdAt: p.createdAt ?? undefined }}
                            className="ml-auto"
                          />
                        </CardHeader>
                        <CardContent className="space-y-2">
                          {p.rationale && <p className="text-sm text-muted-foreground">{p.rationale}</p>}
                          <DiffView argsDiff={p.argsDiff} />
                          <Button asChild size="sm" variant="ai">
                            <a href={`/inbox?p=${p.id}`}>Review in inbox</a>
                          </Button>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                </Tabs.Content>
              </Tabs.Root>

              <Card className="h-fit">
                <CardHeader>
                  <CardTitle className="text-sm">Row reference</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">
                  {c.sourceDataset ? (
                    <p>
                      Backed by <UrnLink urn={c.sourceDataset.urn} label={c.sourceDataset.name} />
                      {c.sourceDataset.rowCount != null && ` · ${c.sourceDataset.rowCount} rows`}
                    </p>
                  ) : (
                    <p>No linked dataset.</p>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}

/** How long after resolvedAt the backend still accepts a reopen (CASE-FR). */
const REOPEN_WINDOW_MS = 30 * 86_400_000;

/**
 * Lifecycle actions derived EXACTLY from case-service's state machine
 * (domain/statemachine.go): assign from unassigned/draft/in_progress; unassign
 * from draft/in_progress; start from draft; resolve from in_progress; reopen
 * from resolved (≤30 days after resolvedAt); close from resolved; escalate has
 * no status guard but is hidden on the terminal closed state. An illegal
 * transition still 409s server-side — these buttons just never offer one.
 */
function CaseActionsBar({ c }: { c: Case }) {
  const push = useToasts((s) => s.push);
  const [dialog, setDialog] = useState<null | "assign" | "resolve" | "close" | "escalate" | "sync">(null);

  const toastError = (title: string) => (err: unknown) => {
    const g = err instanceof GraphQLRequestError ? err : null;
    push({ title, description: g?.message ?? String(err), traceId: g?.traceId, variant: "error" });
  };
  const toastOk = (title: string) => () => push({ title, variant: "success" });

  const assign = useAssignCase(c.id);
  const unassign = useUnassignCase(c.id);
  const start = useStartCase(c.id);
  const resolve = useResolveCase(c.id);
  const reopen = useReopenCase(c.id);
  const close = useCloseCase(c.id);
  const escalate = useEscalateCase(c.id);

  const status = c.status;
  const canAssign = status === "UNASSIGNED" || status === "DRAFT" || status === "IN_PROGRESS";
  const canUnassign = status === "DRAFT" || status === "IN_PROGRESS";
  const canStart = status === "DRAFT";
  const canResolve = status === "IN_PROGRESS";
  const canReopen = status === "RESOLVED";
  const canClose = status === "RESOLVED";
  const canEscalate = status !== "CLOSED";
  // Decision write-back (INS-FR-061): only meaningful once a case has a real
  // outcome to sync — matches the design doc's "on a resolved case" framing.
  const canSync = status === "RESOLVED";
  // Reopen is only legal within 30 days of resolvedAt — offer it disabled with
  // the reason, matching the server's own guard.
  const reopenExpired =
    canReopen && !!c.resolvedAt && Date.now() - new Date(c.resolvedAt).getTime() > REOPEN_WINDOW_MS;

  // Assign dialog state.
  const [assigneeId, setAssigneeId] = useState("");
  const usersQuery = useUsers();
  const users = useMemo(() => usersQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [usersQuery.data]);

  // Resolve dialog state — dispositions come from the real workspace catalog.
  const [dispositionId, setDispositionId] = useState("");
  const [note, setNote] = useState("");
  const dispositionsQuery = useDispositions();
  const activeDispositions = useMemo(
    () => (dispositionsQuery.data ?? []).filter((d) => d.active),
    [dispositionsQuery.data],
  );
  const chosenDisposition = activeDispositions.find((d) => d.id === dispositionId);
  const noteMissing = !!chosenDisposition?.requiresNote && note.trim() === "";

  // Escalate dialog state.
  const [reason, setReason] = useState("");

  // Sync-to-SoR dialog state (INS-FR-061). Only postgres/http_api connections
  // have a real write-back executor server-side, so only `outgoing`/`both`
  // connections of those types are offered as targets.
  const connectionsQuery = useConnections();
  const outgoingConnections = useMemo(
    () =>
      (connectionsQuery.data?.pages.flatMap((p) => p.nodes) ?? []).filter(
        (conn) =>
          (conn.trafficDirection === "outgoing" || conn.trafficDirection === "both") &&
          (conn.connectorType === "postgres" || conn.connectorType === "http_api"),
      ),
    [connectionsQuery.data],
  );
  const [syncConnectionId, setSyncConnectionId] = useState("");
  const [syncTarget, setSyncTarget] = useState("{}");
  const [syncPayload, setSyncPayload] = useState(() =>
    JSON.stringify({ case_id: c.id, case_number: c.caseNumber, disposition_id: c.dispositionId ?? null,
      severity: c.severity, resolution_note: c.resolutionNote ?? null, resolved_at: c.resolvedAt ?? null }, null, 2),
  );
  const [syncJsonError, setSyncJsonError] = useState<string | null>(null);
  const createWriteback = useCreateWriteback();
  const syncError = createWriteback.error instanceof GraphQLRequestError ? createWriteback.error : null;

  if (status === "CLOSED") return null;

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {canAssign && (
        <Can gate={FEATURE_GATES.assignCase}>
          <Button size="sm" variant="secondary" onClick={() => setDialog("assign")}>
            {c.assignee ? "Reassign" : "Assign"}
          </Button>
        </Can>
      )}
      {canUnassign && (
        <Can gate={FEATURE_GATES.assignCase}>
          <Button
            size="sm"
            variant="outline"
            disabled={unassign.isPending}
            onClick={() =>
              unassign.mutate(undefined, {
                onSuccess: toastOk("Case unassigned"),
                onError: toastError("Unassign failed"),
              })
            }
          >
            Unassign
          </Button>
        </Can>
      )}
      {canStart && (
        <Can gate={FEATURE_GATES.startCase}>
          <Button
            size="sm"
            disabled={start.isPending}
            onClick={() =>
              start.mutate(undefined, {
                onSuccess: toastOk("Case started"),
                onError: toastError("Start failed"),
              })
            }
          >
            Start
          </Button>
        </Can>
      )}
      {canResolve && (
        <Can gate={FEATURE_GATES.manageCase}>
          <Button size="sm" onClick={() => setDialog("resolve")}>
            Resolve
          </Button>
        </Can>
      )}
      {canReopen && (
        <Can gate={FEATURE_GATES.manageCase}>
          <Button
            size="sm"
            variant="secondary"
            disabled={reopen.isPending || reopenExpired}
            title={
              reopenExpired
                ? `Reopen window expired — cases can only be reopened within 30 days of resolution (resolved ${formatLocal(c.resolvedAt)})`
                : undefined
            }
            onClick={() =>
              reopen.mutate(undefined, {
                onSuccess: toastOk("Case reopened"),
                onError: toastError("Reopen failed"),
              })
            }
          >
            Reopen
          </Button>
        </Can>
      )}
      {canClose && (
        <Can gate={FEATURE_GATES.manageCase}>
          <Button size="sm" variant="destructive" onClick={() => setDialog("close")}>
            Close
          </Button>
        </Can>
      )}
      {canEscalate && (
        <Can gate={FEATURE_GATES.manageCase}>
          <Button size="sm" variant="outline" onClick={() => setDialog("escalate")}>
            Escalate
          </Button>
        </Can>
      )}
      {canSync && (
        <Can gate={FEATURE_GATES.createWriteback}>
          <Button size="sm" variant="outline" onClick={() => setDialog("sync")}>
            Sync to system of record
          </Button>
        </Can>
      )}

      {/* Assign — real user directory, real transition. */}
      <ConfirmDialog
        open={dialog === "assign"}
        onOpenChange={(o) => {
          if (!o) setAssigneeId("");
          setDialog(o ? "assign" : null);
        }}
        title={c.assignee ? "Reassign case" : "Assign case"}
        description="The assignee becomes responsible for working this case to resolution."
        confirmLabel={assign.isPending ? "Assigning…" : "Assign"}
        onConfirm={() => {
          if (!assigneeId || assign.isPending) return;
          assign.mutate(assigneeId, {
            onSuccess: () => {
              setDialog(null);
              setAssigneeId("");
              push({ title: "Case assigned", variant: "success" });
            },
            onError: toastError("Assign failed"),
          });
        }}
      >
        <div className="mt-3 space-y-1.5">
          <label htmlFor="case-assignee" className="text-xs text-muted-foreground">
            Assign to
          </label>
          <select
            id="case-assignee"
            value={assigneeId}
            onChange={(e) => setAssigneeId(e.target.value)}
            disabled={assign.isPending}
            className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="">Pick a user…</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.fullName || u.email}
              </option>
            ))}
          </select>
        </div>
      </ConfirmDialog>

      {/* Resolve — disposition from the real catalog; note enforced when the
          chosen disposition requires one (the server 422s regardless). */}
      <ConfirmDialog
        open={dialog === "resolve"}
        onOpenChange={(o) => {
          if (!o) {
            setDispositionId("");
            setNote("");
          }
          setDialog(o ? "resolve" : null);
        }}
        title="Resolve case"
        description="Pick the disposition that describes the outcome. Resolved cases can be reopened for 30 days, then closed."
        confirmLabel={resolve.isPending ? "Resolving…" : "Resolve"}
        onConfirm={() => {
          if (!dispositionId || noteMissing || resolve.isPending) return;
          resolve.mutate(
            { dispositionId, resolutionNote: note.trim() || undefined },
            {
              onSuccess: () => {
                setDialog(null);
                setDispositionId("");
                setNote("");
                push({ title: "Case resolved", variant: "success" });
              },
              onError: toastError("Resolve failed"),
            },
          );
        }}
      >
        <div className="mt-3 space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="case-disposition" className="text-xs text-muted-foreground">
              Disposition
            </label>
            <select
              id="case-disposition"
              value={dispositionId}
              onChange={(e) => setDispositionId(e.target.value)}
              disabled={resolve.isPending}
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              <option value="">Pick a disposition…</option>
              {activeDispositions.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.label} ({d.category?.replaceAll("_", " ")})
                </option>
              ))}
            </select>
            {dispositionsQuery.data && activeDispositions.length === 0 && (
              <p className="text-xs text-destructive">
                No active dispositions in this workspace — create one under Cases → Settings first.
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <label htmlFor="case-resolution-note" className="text-xs text-muted-foreground">
              Resolution note{chosenDisposition?.requiresNote ? " (required for this disposition)" : " (optional)"}
            </label>
            <Textarea
              id="case-resolution-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={resolve.isPending}
              placeholder="What was found and why this disposition applies…"
            />
            {noteMissing && (
              <p className="text-xs text-destructive">This disposition requires a resolution note.</p>
            )}
          </div>
        </div>
      </ConfirmDialog>

      {/* Close — terminal, so a proportionate destructive confirm. */}
      <ConfirmDialog
        open={dialog === "close"}
        onOpenChange={(o) => setDialog(o ? "close" : null)}
        title="Close case"
        description="Closing is terminal — a closed case can never be reopened or edited."
        confirmLabel={close.isPending ? "Closing…" : "Close case"}
        destructive
        onConfirm={() => {
          if (close.isPending) return;
          close.mutate(undefined, {
            onSuccess: () => {
              setDialog(null);
              push({ title: "Case closed", variant: "success" });
            },
            onError: toastError("Close failed"),
          });
        }}
      />

      {/* Escalate — bumps severity one level; status is unchanged. */}
      <ConfirmDialog
        open={dialog === "escalate"}
        onOpenChange={(o) => {
          if (!o) setReason("");
          setDialog(o ? "escalate" : null);
        }}
        title="Escalate case"
        description="Escalating bumps the severity one level and records who asked and why."
        confirmLabel={escalate.isPending ? "Escalating…" : "Escalate"}
        onConfirm={() => {
          if (escalate.isPending) return;
          escalate.mutate(
            { reason: reason.trim() || undefined },
            {
              onSuccess: () => {
                setDialog(null);
                setReason("");
                push({ title: "Case escalated", variant: "success" });
              },
              onError: toastError("Escalate failed"),
            },
          );
        }}
      >
        <div className="mt-3 space-y-1.5">
          <label htmlFor="case-escalate-reason" className="text-xs text-muted-foreground">
            Reason
          </label>
          <Textarea
            id="case-escalate-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={escalate.isPending}
            placeholder="Why this needs more urgency…"
          />
        </div>
      </ConfirmDialog>

      {/* Sync to system of record — enqueues a governed, four-eyes write-back
          (INS-FR-061); this only REQUESTS delivery, a different principal must
          approve it under Admin → Decision write-backs before it actually syncs. */}
      <ConfirmDialog
        open={dialog === "sync"}
        onOpenChange={(o) => {
          if (!o) {
            setSyncConnectionId("");
            setSyncTarget("{}");
            setSyncJsonError(null);
          }
          setDialog(o ? "sync" : null);
        }}
        title="Sync to system of record"
        description="Enqueues a decision write-back for four-eyes approval — a different user must approve it (Admin → Decision write-backs) before anything is actually delivered."
        confirmLabel={createWriteback.isPending ? "Enqueuing…" : "Enqueue write-back"}
        onConfirm={() => {
          if (!syncConnectionId || createWriteback.isPending) return;
          let target: Record<string, unknown>;
          let payload: Record<string, unknown>;
          try {
            target = JSON.parse(syncTarget);
            payload = JSON.parse(syncPayload);
          } catch {
            setSyncJsonError("Target and payload must be valid JSON.");
            return;
          }
          setSyncJsonError(null);
          createWriteback.mutate(
            { connectionId: syncConnectionId, decisionKind: "case.disposition", decisionRef: c.urn, target, payload },
            {
              onSuccess: () => {
                setDialog(null);
                setSyncConnectionId("");
                setSyncTarget("{}");
                push({ title: "Write-back enqueued — awaiting four-eyes approval", variant: "success" });
              },
              onError: toastError("Sync failed"),
            },
          );
        }}
      >
        <div className="mt-3 space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="sync-connection" className="text-xs text-muted-foreground">
              Target connection
            </label>
            <select
              id="sync-connection"
              value={syncConnectionId}
              onChange={(e) => setSyncConnectionId(e.target.value)}
              disabled={createWriteback.isPending}
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              <option value="">Pick an outgoing connection…</option>
              {outgoingConnections.map((conn) => (
                <option key={conn.id} value={conn.id}>
                  {conn.name} ({conn.connectorType})
                </option>
              ))}
            </select>
            {connectionsQuery.data && outgoingConnections.length === 0 && (
              <p className="text-xs text-destructive">
                No outgoing connection configured yet — create one under Data → Data Sources
                (traffic direction: outgoing or both; postgres or http_api only).
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <label htmlFor="sync-target" className="text-xs text-muted-foreground">
              Target routing (postgres: {"{schema, table, key_column}"}; http_api: {"{path?, method?}"})
            </label>
            <Textarea
              id="sync-target"
              value={syncTarget}
              onChange={(e) => setSyncTarget(e.target.value)}
              disabled={createWriteback.isPending}
              className="font-mono text-xs"
              rows={3}
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="sync-payload" className="text-xs text-muted-foreground">
              Payload (the decision snapshot delivered to the system of record)
            </label>
            <Textarea
              id="sync-payload"
              value={syncPayload}
              onChange={(e) => setSyncPayload(e.target.value)}
              disabled={createWriteback.isPending}
              className="font-mono text-xs"
              rows={6}
            />
          </div>
          {syncJsonError && <p className="text-xs text-destructive">{syncJsonError}</p>}
          {syncError && (
            <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
              {syncError.message}
            </p>
          )}
        </div>
      </ConfirmDialog>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-muted-foreground">{label}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}

function SlaChip({ due }: { due?: string | null }) {
  if (!due) return null;
  const overdue = new Date(due).getTime() < Date.now();
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
        overdue ? "bg-destructive/15 text-destructive" : "bg-muted text-muted-foreground"
      }`}
    >
      <Clock className="size-3" aria-hidden />
      {overdue ? "Overdue" : `Due ${formatLocal(due)}`}
    </span>
  );
}

/**
 * Severity/description editing via PATCH /cases/{id} (updateCase). Resolution
 * is deliberately NOT here — it goes through the Resolve action above (the
 * real resolveCase transition with a disposition), never a description PATCH.
 */
function CaseEditForm({
  caseId,
  severity,
  description,
  dueDate,
}: {
  caseId: string;
  severity?: Severity | null;
  description?: string | null;
  dueDate?: string | null;
}) {
  const update = useUpdateCase(caseId);
  const push = useToasts((s) => s.push);
  const [sev, setSev] = useState<Severity>(severity ?? "MEDIUM");
  const [desc, setDesc] = useState(description ?? "");
  const [due, setDue] = useState(dueDate ? dueDate.slice(0, 10) : "");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    update.mutate(
      { severity: sev, description: desc || undefined, dueDate: due || undefined },
      {
        onError: (err) => {
          const g = err instanceof GraphQLRequestError ? err : null;
          push({
            title: "Update failed — reverted",
            description: g?.message,
            traceId: g?.traceId,
            variant: "error",
          });
        },
        onSuccess: () => push({ title: "Case updated", variant: "success" }),
      },
    );
  }

  return (
    <Card>
      <CardContent className="pt-4">
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <Label>Severity</Label>
            <select
              value={sev}
              onChange={(e) => setSev(e.target.value as Severity)}
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as Severity[]).map((s) => (
                <option key={s} value={s}>
                  {s.toLowerCase()}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label>Description</Label>
            <Textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Describe the case…" />
          </div>
          <div className="space-y-1">
            <Label>Due date</Label>
            <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </div>
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save changes"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

/** How long the backend lets an author edit/delete their own comment. */
const COMMENT_EDIT_WINDOW_MS = 15 * 60_000;

/**
 * The real merged event+comment timeline (GET /cases/{id}/timeline), newest-
 * first, plus a comment composer. CONTRACT GAP (documented in the BFF SDL):
 * case-service has NO list-comments route — a comment body is only readable on
 * the create/edit response, and the timeline row carries just {comment_id}. So
 * bodies for comments posted in THIS session are cached client-side keyed by
 * comment id; older comments honestly show a placeholder instead of a
 * fabricated body.
 */
function ActivityPanel({ caseId }: { caseId: string }) {
  const session = useSession();
  const push = useToasts((s) => s.push);
  const timeline = useCaseTimeline(caseId);
  const addComment = useAddCaseComment(caseId);
  const updateComment = useUpdateCaseComment(caseId);
  const deleteComment = useDeleteCaseComment(caseId);

  const [draft, setDraft] = useState("");
  // Session-local comment bodies keyed by comment id (see doc comment above).
  const [bodies, setBodies] = useState<Record<string, string>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");

  const activities = useMemo(
    () => timeline.data?.pages.flatMap((p) => p.nodes) ?? [],
    [timeline.data],
  );

  const toastError = (title: string) => (err: unknown) => {
    const g = err instanceof GraphQLRequestError ? err : null;
    push({ title, description: g?.message ?? String(err), traceId: g?.traceId, variant: "error" });
  };

  function submitComment(e: React.FormEvent) {
    e.preventDefault();
    const body = draft.trim();
    if (!body || addComment.isPending) return;
    addComment.mutate(body, {
      onSuccess: (r) => {
        setBodies((prev) => ({ ...prev, [r.addCaseComment.id]: body }));
        setDraft("");
      },
      onError: toastError("Comment failed"),
    });
  }

  function commentIdOf(a: CaseActivity): string | null {
    const nv = a.newValue as { comment_id?: string } | null | undefined;
    return nv && typeof nv === "object" ? (nv.comment_id ?? null) : null;
  }

  return (
    <div className="space-y-3">
      <Can gate={FEATURE_GATES.manageCase}>
        <Card>
          <CardContent className="pt-4">
            <form onSubmit={submitComment} className="space-y-2">
              <Label htmlFor="case-comment">Add a comment</Label>
              <Textarea
                id="case-comment"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={8192}
                placeholder="Share findings with the team…"
              />
              <Button type="submit" size="sm" disabled={addComment.isPending || !draft.trim()}>
                {addComment.isPending ? "Posting…" : "Comment"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </Can>

      <AsyncBoundary
        isLoading={timeline.isLoading}
        isError={timeline.isError}
        error={timeline.error}
        isEmpty={!timeline.isLoading && activities.length === 0}
        emptyTitle="No activity yet"
        onRetry={() => timeline.refetch()}
      >
        <ul className="space-y-2" aria-label="Case activity">
          {activities.map((a) => {
            const isComment = a.eventType === "comment.added";
            const commentId = isComment ? commentIdOf(a) : null;
            const cachedBody = commentId ? bodies[commentId] : undefined;
            const mine = a.actorType === "user" && a.actorId === session.userId;
            const withinWindow =
              !!a.occurredAt && Date.now() - new Date(a.occurredAt).getTime() <= COMMENT_EDIT_WINDOW_MS;
            const editable = isComment && !!commentId && mine && withinWindow;

            return (
              <li key={a.id}>
                <Card>
                  <CardContent className="space-y-1 pt-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{a.eventType?.replaceAll(/[._]/g, " ") ?? "event"}</span>
                      <span className="text-xs text-muted-foreground">
                        {a.actor?.fullName ?? a.actor?.email ?? a.actorId ?? a.actorType ?? "system"}
                        {a.actorType === "agent" && " (agent)"}
                      </span>
                      <span className="ml-auto text-xs text-muted-foreground">{formatLocal(a.occurredAt)}</span>
                    </div>

                    {isComment ? (
                      editingId === commentId ? (
                        <div className="space-y-2">
                          <Textarea
                            value={editDraft}
                            onChange={(e) => setEditDraft(e.target.value)}
                            maxLength={8192}
                            aria-label="Edit comment"
                          />
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              disabled={updateComment.isPending || !editDraft.trim()}
                              onClick={() =>
                                updateComment.mutate(
                                  { id: commentId!, body: editDraft.trim() },
                                  {
                                    onSuccess: () => {
                                      setBodies((prev) => ({ ...prev, [commentId!]: editDraft.trim() }));
                                      setEditingId(null);
                                    },
                                    onError: toastError("Edit failed"),
                                  },
                                )
                              }
                            >
                              Save
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <div className="space-y-1">
                          {cachedBody !== undefined ? (
                            <p className="whitespace-pre-wrap">{cachedBody}</p>
                          ) : (
                            <p className="italic text-muted-foreground">
                              Comment (body available at creation time only)
                            </p>
                          )}
                          {editable && (
                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => {
                                  setEditingId(commentId);
                                  setEditDraft(cachedBody ?? "");
                                }}
                              >
                                Edit
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={deleteComment.isPending}
                                onClick={() =>
                                  deleteComment.mutate(commentId!, {
                                    onSuccess: () => {
                                      setBodies((prev) => {
                                        const next = { ...prev };
                                        delete next[commentId!];
                                        return next;
                                      });
                                      push({ title: "Comment deleted", variant: "success" });
                                    },
                                    onError: toastError("Delete failed"),
                                  })
                                }
                              >
                                Delete
                              </Button>
                            </div>
                          )}
                        </div>
                      )
                    ) : (
                      (a.oldValue != null || a.newValue != null) && (
                        <p className="text-xs text-muted-foreground">
                          {a.oldValue != null && <>from <span className="font-mono">{summarize(a.oldValue)}</span> </>}
                          {a.newValue != null && <>to <span className="font-mono">{summarize(a.newValue)}</span></>}
                        </p>
                      )
                    )}
                  </CardContent>
                </Card>
              </li>
            );
          })}
        </ul>
        {timeline.hasNextPage && (
          <Button
            variant="outline"
            size="sm"
            disabled={timeline.isFetchingNextPage}
            onClick={() => timeline.fetchNextPage()}
          >
            {timeline.isFetchingNextPage ? "Loading…" : "Load older activity"}
          </Button>
        )}
      </AsyncBoundary>
    </div>
  );
}

/** Compact one-line rendering of an activity's old/new JSON value. */
function summarize(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  const s = JSON.stringify(v);
  return s.length > 120 ? `${s.slice(0, 117)}…` : s;
}
