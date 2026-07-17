"use client";
import { useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { Badge, Card, CardContent, CardHeader, CardTitle, Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import {
  useDispositions, useCreateDisposition, useUpdateDisposition,
  useCaseFields, useCreateCaseField, useUpdateCaseField, useDeleteCaseField,
  usePutCaseSlaPolicy, useUsers,
} from "@/lib/graphql/hooks";
import { useToasts } from "@/stores/ui";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type {
  Disposition, DispositionCategory, CaseField, CaseFieldDataType, CaseFieldPurpose, SlaOnBreach,
} from "@/lib/graphql/types";

const CATEGORIES: DispositionCategory[] = [
  "true_positive", "false_positive", "benign", "inconclusive", "other",
];
const DATA_TYPES: CaseFieldDataType[] = ["string", "text", "integer", "float", "boolean", "date", "enum"];
const PURPOSES: CaseFieldPurpose[] = ["create", "update", "both"];
const ON_BREACH: SlaOnBreach[] = ["auto_unassign", "escalate", "notify_only"];

/**
 * Cases settings: the workspace disposition catalog, custom case-field configs
 * and the SLA policy — every control is a real case-service call (see each
 * panel). Reached from the cases list header; each tab re-gates per action.
 */
export default function CaseSettingsPage() {
  return (
    <div>
      <PageHeader
        title="Case settings"
        description="Dispositions, custom fields and the SLA policy for this workspace."
      />
      <Tabs.Root defaultValue="dispositions">
        <Tabs.List className="mb-3 flex gap-1 border-b" aria-label="Case settings sections">
          {[
            ["dispositions", "Dispositions"],
            ["fields", "Case fields"],
            ["sla", "SLA policy"],
          ].map(([v, label]) => (
            <Tabs.Trigger
              key={v}
              value={v}
              className="border-b-2 border-transparent px-3 py-2 text-sm font-medium text-muted-foreground data-[state=active]:border-primary data-[state=active]:text-foreground"
            >
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        <Tabs.Content value="dispositions">
          <DispositionsPanel />
        </Tabs.Content>
        <Tabs.Content value="fields">
          <CaseFieldsPanel />
        </Tabs.Content>
        <Tabs.Content value="sla">
          <SlaPolicyPanel />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}

function useErrorToast() {
  const push = useToasts((s) => s.push);
  return (title: string) => (err: unknown) => {
    const g = err instanceof GraphQLRequestError ? err : null;
    push({ title, description: g?.message ?? String(err), traceId: g?.traceId, variant: "error" });
  };
}

/* ---------------------------- dispositions -------------------------------- */

/** The real workspace catalog (GET /dispositions) + create (POST, gated
 * case.disposition.create) + edit (PATCH, gated case.disposition.update). */
function DispositionsPanel() {
  const query = useDispositions();
  const push = useToasts((s) => s.push);
  const toastError = useErrorToast();
  const create = useCreateDisposition();
  const update = useUpdateDisposition();

  const [createOpen, setCreateOpen] = useState(false);
  const [code, setCode] = useState("");
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState<DispositionCategory>("true_positive");
  const [requiresNote, setRequiresNote] = useState(false);

  const [editing, setEditing] = useState<Disposition | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [editCategory, setEditCategory] = useState<DispositionCategory>("true_positive");
  const [editRequiresNote, setEditRequiresNote] = useState(false);
  const [editActive, setEditActive] = useState(true);

  const rows = query.data ?? [];

  const columns: Column<Disposition>[] = [
    { id: "code", header: "Code", width: 180, cell: (d) => <span className="font-mono">{d.code}</span> },
    { id: "label", header: "Label", cell: (d) => <span className="font-medium">{d.label}</span> },
    { id: "category", header: "Category", width: 140, cell: (d) => d.category?.replaceAll("_", " ") ?? "—" },
    { id: "note", header: "Note", width: 110, cell: (d) => (d.requiresNote ? "required" : "optional") },
    {
      id: "active", header: "Active", width: 90,
      cell: (d) => <Badge variant={d.active ? "success" : "outline"}>{d.active ? "active" : "inactive"}</Badge>,
    },
    {
      id: "actions", header: "", width: 80,
      cell: (d) => (
        <Can gate={FEATURE_GATES.updateDisposition}>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setEditing(d);
              setEditLabel(d.label ?? "");
              setEditCategory(d.category ?? "other");
              setEditRequiresNote(d.requiresNote);
              setEditActive(d.active);
            }}
          >
            Edit
          </Button>
        </Can>
      ),
    },
  ];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <p className="text-sm text-muted-foreground">
          The closed outcome vocabulary the Resolve action draws from.
        </p>
        <Can gate={FEATURE_GATES.manageDispositions}>
          <Button size="sm" className="ml-auto" onClick={() => setCreateOpen(true)}>
            New disposition
          </Button>
        </Can>
      </div>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && rows.length === 0}
        emptyTitle="No dispositions yet"
        onRetry={() => query.refetch()}
      >
        <DataTable ariaLabel="Dispositions" rows={rows} columns={columns} rowId={(d) => d.id} />
      </AsyncBoundary>

      <ConfirmDialog
        open={createOpen}
        onOpenChange={(o) => {
          setCreateOpen(o);
          if (!o) {
            setCode("");
            setLabel("");
            setCategory("true_positive");
            setRequiresNote(false);
          }
        }}
        title="New disposition"
        description="Code is the stable identifier (duplicates are rejected by the service); label is what adjusters pick."
        confirmLabel={create.isPending ? "Creating…" : "Create"}
        onConfirm={() => {
          if (!code.trim() || !label.trim() || create.isPending) return;
          create.mutate(
            { code: code.trim(), label: label.trim(), category, requiresNote },
            {
              onSuccess: () => {
                setCreateOpen(false);
                setCode("");
                setLabel("");
                setRequiresNote(false);
                push({ title: "Disposition created", variant: "success" });
              },
              onError: toastError("Create failed"),
            },
          );
        }}
      >
        <div className="mt-3 space-y-3">
          <div className="space-y-1">
            <Label htmlFor="disp-code">Code</Label>
            <Input
              id="disp-code" value={code} onChange={(e) => setCode(e.target.value)}
              placeholder="fraud_confirmed"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="disp-label">Label</Label>
            <Input
              id="disp-label" value={label} onChange={(e) => setLabel(e.target.value)}
              placeholder="Fraud confirmed"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="disp-category">Category</Label>
            <select
              id="disp-category"
              value={category}
              onChange={(e) => setCategory(e.target.value as DispositionCategory)}
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {CATEGORIES.map((cat) => (
                <option key={cat} value={cat}>
                  {cat.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={requiresNote}
              onChange={(e) => setRequiresNote(e.target.checked)}
            />
            Require a resolution note
          </label>
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={!!editing}
        onOpenChange={(o) => {
          if (!o) setEditing(null);
        }}
        title={`Edit ${editing?.code ?? "disposition"}`}
        confirmLabel={update.isPending ? "Saving…" : "Save"}
        onConfirm={() => {
          if (!editing || update.isPending) return;
          update.mutate(
            {
              id: editing.id,
              // requiresNote is ALWAYS sent: the service PATCH overwrites it
              // unconditionally, so omitting it would silently reset to false.
              input: { label: editLabel.trim() || undefined, category: editCategory, requiresNote: editRequiresNote, active: editActive },
            },
            {
              onSuccess: () => {
                setEditing(null);
                push({ title: "Disposition updated", variant: "success" });
              },
              onError: toastError("Update failed"),
            },
          );
        }}
      >
        <div className="mt-3 space-y-3">
          <div className="space-y-1">
            <Label htmlFor="disp-edit-label">Label</Label>
            <Input id="disp-edit-label" value={editLabel} onChange={(e) => setEditLabel(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="disp-edit-category">Category</Label>
            <select
              id="disp-edit-category"
              value={editCategory}
              onChange={(e) => setEditCategory(e.target.value as DispositionCategory)}
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {CATEGORIES.map((cat) => (
                <option key={cat} value={cat}>
                  {cat.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={editRequiresNote}
              onChange={(e) => setEditRequiresNote(e.target.checked)}
            />
            Require a resolution note
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={editActive} onChange={(e) => setEditActive(e.target.checked)} />
            Active (inactive dispositions are hidden from the Resolve dialog)
          </label>
        </div>
      </ConfirmDialog>
    </div>
  );
}

/* ---------------------------- case fields --------------------------------- */

/** Custom case-field configs (GET/POST/DELETE /case-fields). Delete explains
 * the service's 409 FIELD_IN_USE and offers the real orphan=true retry. */
function CaseFieldsPanel() {
  const query = useCaseFields();
  const push = useToasts((s) => s.push);
  const toastError = useErrorToast();
  const create = useCreateCaseField();
  const update = useUpdateCaseField();
  const del = useDeleteCaseField();

  const [name, setName] = useState("");
  const [dataType, setDataType] = useState<CaseFieldDataType>("string");
  const [purpose, setPurpose] = useState<CaseFieldPurpose>("both");
  const [queryUrn, setQueryUrn] = useState("");

  // Edit-in-place: the same field form reused against an existing field. name +
  // dataType are immutable (rendered read-only); only purpose + fieldMeta commit
  // via the updateCaseField PATCH. fieldMeta is edited as JSON text.
  const [editing, setEditing] = useState<CaseField | null>(null);
  const [fieldMetaText, setFieldMetaText] = useState("");
  const [fieldMetaErr, setFieldMetaErr] = useState<string | null>(null);

  const [deleting, setDeleting] = useState<CaseField | null>(null);
  const [orphan, setOrphan] = useState(false);

  const rows = query.data ?? [];

  const resetForm = () => {
    setEditing(null);
    setName("");
    setDataType("string");
    setPurpose("both");
    setQueryUrn("");
    setFieldMetaText("");
    setFieldMetaErr(null);
  };

  const startEdit = (f: CaseField) => {
    setEditing(f);
    setName(f.name ?? "");
    setDataType(f.dataType ?? "string");
    setPurpose(f.purpose ?? "both");
    setQueryUrn(f.queryUrn ?? "");
    setFieldMetaText(f.fieldMeta != null ? JSON.stringify(f.fieldMeta, null, 2) : "");
    setFieldMetaErr(null);
  };

  const columns: Column<CaseField>[] = [
    { id: "name", header: "Name", cell: (f) => <span className="font-medium">{f.name}</span> },
    { id: "type", header: "Type", width: 100, cell: (f) => f.dataType ?? "—" },
    { id: "purpose", header: "Purpose", width: 100, cell: (f) => f.purpose ?? "—" },
    {
      id: "scope", header: "Scope",
      cell: (f) => (f.queryUrn ? <span className="font-mono text-xs">{f.queryUrn}</span> : "workspace-wide"),
    },
    {
      id: "actions", header: "", width: 150,
      cell: (f) => (
        <Can gate={FEATURE_GATES.manageCaseFields}>
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => startEdit(f)}>
              Edit
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setDeleting(f);
                setOrphan(false);
              }}
            >
              Delete
            </Button>
          </div>
        </Can>
      ),
    },
  ];

  return (
    <div className="space-y-3">
      <Can gate={FEATURE_GATES.manageCaseFields}>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{editing ? "Edit case field" : "New case field"}</CardTitle>
          </CardHeader>
          <CardContent>
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (editing) {
                  if (update.isPending) return;
                  let fieldMeta: unknown;
                  if (fieldMetaText.trim()) {
                    try {
                      fieldMeta = JSON.parse(fieldMetaText);
                    } catch {
                      setFieldMetaErr("Field meta must be valid JSON.");
                      return;
                    }
                  }
                  setFieldMetaErr(null);
                  update.mutate(
                    { id: editing.id, purpose, fieldMeta },
                    {
                      onSuccess: () => {
                        resetForm();
                        push({ title: "Case field updated", variant: "success" });
                      },
                      onError: toastError("Update failed"),
                    },
                  );
                  return;
                }
                if (!name.trim() || create.isPending) return;
                create.mutate(
                  {
                    name: name.trim(),
                    dataType,
                    purpose,
                    queryUrn: queryUrn.trim() || undefined,
                  },
                  {
                    onSuccess: () => {
                      setName("");
                      setQueryUrn("");
                      push({ title: "Case field created", variant: "success" });
                    },
                    onError: toastError("Create failed"),
                  },
                );
              }}
            >
              <div className="space-y-1">
                <Label htmlFor="field-name">Name</Label>
                <Input
                  id="field-name" value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="siu_referral" className="w-44" disabled={!!editing}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="field-type">Data type</Label>
                <select
                  id="field-type"
                  value={dataType}
                  onChange={(e) => setDataType(e.target.value as CaseFieldDataType)}
                  disabled={!!editing}
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-60"
                >
                  {DATA_TYPES.map((dt) => (
                    <option key={dt} value={dt}>
                      {dt}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="field-purpose">Purpose</Label>
                <select
                  id="field-purpose"
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value as CaseFieldPurpose)}
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                >
                  {PURPOSES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
              {editing ? (
                <div className="w-full space-y-1">
                  <Label htmlFor="field-meta">Field meta (JSON)</Label>
                  <Textarea
                    id="field-meta"
                    value={fieldMetaText}
                    onChange={(e) => setFieldMetaText(e.target.value)}
                    placeholder={'{ "options": ["a", "b"] }'}
                    rows={3}
                    className="font-mono text-xs"
                  />
                  {fieldMetaErr && <p className="text-xs text-destructive">{fieldMetaErr}</p>}
                </div>
              ) : (
                <div className="space-y-1">
                  <Label htmlFor="field-query-urn">Query URN (optional scope)</Label>
                  <Input
                    id="field-query-urn" value={queryUrn} onChange={(e) => setQueryUrn(e.target.value)}
                    placeholder="wr:…:query:query/…" className="w-64 font-mono"
                  />
                </div>
              )}
              {editing ? (
                <div className="flex gap-2">
                  <Button type="submit" size="sm" disabled={update.isPending}>
                    {update.isPending ? "Saving…" : "Save"}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={resetForm}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <Button type="submit" size="sm" disabled={create.isPending || !name.trim()}>
                  {create.isPending ? "Creating…" : "Create field"}
                </Button>
              )}
            </form>
          </CardContent>
        </Card>
      </Can>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && rows.length === 0}
        emptyTitle="No custom case fields"
        onRetry={() => query.refetch()}
      >
        <DataTable ariaLabel="Case fields" rows={rows} columns={columns} rowId={(f) => f.id} />
      </AsyncBoundary>

      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => {
          if (!o) setDeleting(null);
        }}
        title={`Delete field "${deleting?.name ?? ""}"`}
        description={
          <>
            If open cases carry values for this field the service refuses with FIELD_IN_USE —
            tick the box below to strand those values (they stay on the cases but become unmanaged).
          </>
        }
        confirmLabel={del.isPending ? "Deleting…" : "Delete"}
        destructive
        onConfirm={() => {
          if (!deleting || del.isPending) return;
          del.mutate(
            { id: deleting.id, orphan: orphan || undefined },
            {
              onSuccess: () => {
                setDeleting(null);
                push({ title: "Case field deleted", variant: "success" });
              },
              onError: (err) => {
                const g = err instanceof GraphQLRequestError ? err : null;
                const inUse = g?.message?.includes("open cases") || g?.code === "CONFLICT";
                push({
                  title: "Delete failed",
                  description: inUse
                    ? `${g?.message ?? "field is in use"} — tick "orphan values" in the dialog and retry to delete anyway.`
                    : (g?.message ?? String(err)),
                  traceId: g?.traceId,
                  variant: "error",
                });
              },
            },
          );
        }}
      >
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={orphan} onChange={(e) => setOrphan(e.target.checked)} />
          Orphan existing values (required when the field is in use on open cases)
        </label>
      </ConfirmDialog>
    </div>
  );
}

/* ---------------------------- SLA policy ----------------------------------- */

/** Platform defaults applied when a field is left at zero/empty (case-service
 * domain.DefaultSLAPolicy). Shown as the form's starting point because the
 * backend exposes NO read route for the current policy. */
const SLA_DEFAULTS = { warnBeforeSeconds: 86_400, onBreach: "auto_unassign" as SlaOnBreach, maxReassignCount: 3 };

function SlaPolicyPanel() {
  const push = useToasts((s) => s.push);
  const toastError = useErrorToast();
  const put = usePutCaseSlaPolicy();
  const usersQuery = useUsers();
  const users = useMemo(() => usersQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [usersQuery.data]);

  const [warnBeforeSeconds, setWarnBeforeSeconds] = useState(SLA_DEFAULTS.warnBeforeSeconds);
  const [onBreach, setOnBreach] = useState<SlaOnBreach>(SLA_DEFAULTS.onBreach);
  const [escalateTo, setEscalateTo] = useState("");
  const [maxReassignCount, setMaxReassignCount] = useState(SLA_DEFAULTS.maxReassignCount);

  const escalateMissing = onBreach === "escalate" && !escalateTo;

  return (
    <Can
      gate={FEATURE_GATES.manageSlaPolicy}
      fallback={<p className="text-sm text-muted-foreground">You need the case admin capability to edit the SLA policy.</p>}
    >
      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle className="text-sm">SLA policy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            The backend exposes no read for the current policy, so this form starts from the
            platform defaults (24h warning, auto-unassign, 3 reassigns) — saving replaces the
            whole workspace policy with what you submit.
          </p>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (put.isPending || escalateMissing) return;
              put.mutate(
                {
                  warnBeforeSeconds,
                  onBreach,
                  escalateTo: onBreach === "escalate" ? escalateTo : undefined,
                  maxReassignCount,
                },
                {
                  onSuccess: (p) =>
                    push({
                      title: "SLA policy saved",
                      description: `warn ${p.warnBeforeSeconds}s before breach · on breach ${p.onBreach} · max ${p.maxReassignCount} reassigns`,
                      variant: "success",
                    }),
                  onError: toastError("Save failed"),
                },
              );
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="sla-warn">Warn before breach (seconds)</Label>
              <Input
                id="sla-warn"
                type="number"
                min={1}
                value={warnBeforeSeconds}
                onChange={(e) => setWarnBeforeSeconds(Number(e.target.value))}
                className="w-44"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="sla-on-breach">On breach</Label>
              <select
                id="sla-on-breach"
                value={onBreach}
                onChange={(e) => setOnBreach(e.target.value as SlaOnBreach)}
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
              >
                {ON_BREACH.map((b) => (
                  <option key={b} value={b}>
                    {b.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </div>
            {onBreach === "escalate" && (
              <div className="space-y-1">
                <Label htmlFor="sla-escalate-to">Escalate to</Label>
                <select
                  id="sla-escalate-to"
                  value={escalateTo}
                  onChange={(e) => setEscalateTo(e.target.value)}
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                >
                  <option value="">Pick a user…</option>
                  {users.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.fullName || u.email}
                    </option>
                  ))}
                </select>
                {escalateMissing && (
                  <p className="text-xs text-destructive">Escalation needs a target user.</p>
                )}
              </div>
            )}
            <div className="space-y-1">
              <Label htmlFor="sla-max-reassign">Max reassignments</Label>
              <Input
                id="sla-max-reassign"
                type="number"
                min={1}
                value={maxReassignCount}
                onChange={(e) => setMaxReassignCount(Number(e.target.value))}
                className="w-44"
              />
            </div>
            <Button type="submit" disabled={put.isPending || escalateMissing}>
              {put.isPending ? "Saving…" : "Save policy"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </Can>
  );
}
