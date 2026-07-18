"use client";
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Copy, KeySquare, Plus, X } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useRoles,
  // Tier 4b: custom-role CRUD (rbac-service /roles).
  useCreateRole, useUpdateRole, useRenameRole, useSetRoleActions, useDeleteRole,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { Role } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

/**
 * Roles admin (rbac-service /roles). System roles — the seeded 10-role V1
 * catalog — are IMMUTABLE (every mutation 409s SYSTEM_IMMUTABLE downstream),
 * so mutation controls render only for non-system rows; the service still
 * enforces regardless.
 */
/** Prefill for the create dialog when cloning an existing role as a template. */
interface RoleSeed { name: string; actionsRaw: string }

export default function AdminRolesPage() {
  const query = useRoles();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [selected, setSelected] = useState<Role | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // Clone-and-customize: seed the create dialog from a source role's actions so
  // tenants extend a template rather than assembling permissions from scratch.
  const [seed, setSeed] = useState<RoleSeed | null>(null);
  // Edit-in-place: the same dialog reused in edit mode against a custom role,
  // committing name + actions together via the atomic updateRole PATCH.
  const [editOpen, setEditOpen] = useState(false);
  const [editRole, setEditRole] = useState<Role | null>(null);

  const openBlank = () => { setSeed(null); setCreateOpen(true); };
  const openClone = (r: Role) => {
    setSeed({ name: `Copy of ${r.name}`.slice(0, 255), actionsRaw: r.actions.join("\n") });
    setCreateOpen(true);
  };
  const openEdit = (r: Role) => { setEditRole(r); setEditOpen(true); };

  const columns: Column<Role>[] = [
    { id: "name", header: "Name", cell: (r) => <span className="font-medium">{r.name}</span> },
    {
      id: "kind", header: "Kind", width: 110,
      cell: (r) => r.system ? <Badge variant="warning">system</Badge> : <Badge variant="success">custom</Badge>,
    },
    { id: "version", header: "Version", width: 90, cell: (r) => r.version ?? "—" },
    { id: "actions", header: "Actions", width: 110, cell: (r) => `${r.actions.length}` },
    { id: "updated", header: "Updated", width: 170, cell: (r) => formatLocal(r.updatedAt) },
  ];

  return (
    <div>
      <PageHeader
        title="Roles"
        description="Tenant and system roles (rbac-service). System roles are immutable; custom roles can be created, renamed, re-scoped, and deleted."
        actions={
          <Can gate={FEATURE_GATES.createRole}>
            <Button onClick={openBlank}>
              <Plus /> New role
            </Button>
          </Can>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_420px]">
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle="No roles visible."
          onRetry={() => query.refetch()}
        >
          <DataTable
            ariaLabel="Roles"
            rows={rows}
            columns={columns}
            rowId={(r) => r.id}
            onRowActivate={(r) => setSelected(r)}
            hasMore={query.hasNextPage}
            isFetchingMore={query.isFetchingNextPage}
            onLoadMore={() => query.fetchNextPage()}
          />
        </AsyncBoundary>

        <RoleDetail
          role={selected}
          onClose={() => setSelected(null)}
          onChanged={(r) => setSelected(r)}
          onDeleted={() => setSelected(null)}
          onClone={openClone}
          onEdit={openEdit}
        />
      </div>

      <CreateRoleDialog
        open={createOpen}
        seed={seed}
        onOpenChange={setCreateOpen}
        onSaved={(r) => setSelected(r)}
      />
      <CreateRoleDialog
        open={editOpen}
        editRole={editRole}
        onOpenChange={setEditOpen}
        onSaved={(r) => setSelected(r)}
      />
    </div>
  );
}

/** Parse the one-action-per-line textarea into a clean action list. */
function parseActions(raw: string): string[] {
  return raw.split("\n").map((s) => s.trim()).filter(Boolean);
}

/**
 * The single role authoring dialog, reused across three modes:
 *  - new:   blank (create a custom role).
 *  - clone: prefilled from a `seed` (create a copy of an existing role).
 *  - edit:  prefilled from `editRole` and committed with the atomic updateRole
 *           PATCH — renames the role AND replaces its action set in one call.
 * `editRole` takes precedence over `seed`.
 */
function CreateRoleDialog({
  open, seed, editRole, onOpenChange, onSaved,
}: {
  open: boolean;
  seed?: RoleSeed | null;
  editRole?: Role | null;
  onOpenChange: (o: boolean) => void;
  onSaved: (r: Role) => void;
}) {
  const [name, setName] = useState("");
  const [actionsRaw, setActionsRaw] = useState("");
  const create = useCreateRole();
  const update = useUpdateRole();
  const isEdit = !!editRole;
  const active = isEdit ? update : create;
  const error = active.error instanceof GraphQLRequestError ? active.error : null;

  // Prefill each time the dialog opens: from the role being edited, else the
  // clone seed, else blank. Keyed on `open` so in-progress edits are never
  // re-seeded.
  useEffect(() => {
    if (open) {
      setName(editRole?.name ?? seed?.name ?? "");
      setActionsRaw(editRole ? editRole.actions.join("\n") : seed?.actionsRaw ?? "");
      create.reset();
      update.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const reset = () => { setName(""); setActionsRaw(""); create.reset(); update.reset(); };
  const submit = () => {
    const actions = parseActions(actionsRaw);
    if (!name.trim() || actions.length === 0) return;
    const done = { onSuccess: (r: Role) => { onOpenChange(false); reset(); onSaved(r); } };
    if (editRole) {
      // Omit `actions` when unchanged so a name-only edit doesn't bump the role
      // version (updateRole bumps whenever an actions array is provided). The
      // API contract is "omit a field to leave it unchanged".
      const orig = [...editRole.actions].sort();
      const next = [...actions].sort();
      const actionsChanged = orig.length !== next.length || orig.some((a, i) => a !== next[i]);
      update.mutate(
        { id: editRole.id, input: { name: name.trim(), ...(actionsChanged ? { actions } : {}) } },
        done,
      );
    } else {
      create.mutate({ name: name.trim(), actions }, done);
    }
  };

  const title = isEdit ? "Edit role" : seed ? "Clone role" : "New role";
  const submitLabel = isEdit
    ? (update.isPending ? "Saving…" : "Save changes")
    : (create.isPending ? "Creating…" : "Create");

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
          <p className="mt-1 text-xs text-muted-foreground">
            {isEdit
              ? "Rename the role and adjust its actions, then save — name and actions are updated together."
              : seed
                ? "Prefilled from the source role — rename it and adjust the actions, then create."
                : "Actions must exist in the rbac action catalog (one per line, e.g. case.case.read) — unknown actions are rejected by the service."}
          </p>
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <div className="space-y-1.5">
              <Label htmlFor="role-name">Name</Label>
              <Input id="role-name" value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="Claims Triage" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="role-actions">Actions (one per line)</Label>
              <textarea
                id="role-actions"
                value={actionsRaw}
                onChange={(e) => setActionsRaw(e.target.value)}
                rows={6}
                placeholder={"case.case.read\ncase.case.update"}
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 font-mono text-xs"
              />
            </div>
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}{error.traceId ? ` (trace: ${error.traceId})` : ""}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button type="submit" disabled={!name.trim() || parseActions(actionsRaw).length === 0 || active.isPending}>
                {submitLabel}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function RoleDetail({
  role, onClose, onChanged, onDeleted, onClone, onEdit,
}: {
  role: Role | null;
  onClose: () => void;
  onChanged: (r: Role) => void;
  onDeleted: () => void;
  onClone: (r: Role) => void;
  onEdit: (r: Role) => void;
}) {
  const rename = useRenameRole();
  const setActions = useSetRoleActions();
  const deleteRole = useDeleteRole();

  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [editingActions, setEditingActions] = useState(false);
  const [actionsRaw, setActionsRaw] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // 409 SYSTEM_IMMUTABLE (and every other real downstream error) surfaces verbatim.
  const fail = (e: Error) => {
    const trace = e instanceof GraphQLRequestError && e.traceId ? ` (trace: ${e.traceId})` : "";
    setActionError(`${e.message}${trace}`);
  };

  if (!role) {
    return (
      <Card className="h-fit">
        <CardHeader><CardTitle className="text-sm">Role detail</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <KeySquare className="size-6" aria-hidden />
            <p>Select a role to see its actions. Custom roles can be renamed, re-scoped, or deleted.</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          {role.name}
          {role.system
            ? <Badge variant="warning">system</Badge>
            : <Badge variant="success">custom</Badge>}
          {role.version != null && <Badge variant="secondary">v{role.version}</Badge>}
        </CardTitle>
        <div className="flex items-center gap-1">
          {/* Clone works for system roles too — the canonical "start from a
              template, then customize" flow (best practice). Gated on create. */}
          <Can gate={FEATURE_GATES.createRole}>
            <Button variant="ghost" size="sm" onClick={() => onClone(role)}>
              <Copy className="mr-1 size-3.5" /> Clone
            </Button>
          </Can>
          {/* System roles are immutable (409 SYSTEM_IMMUTABLE) — no mutation controls. */}
          {!role.system && (
            <Can gate={FEATURE_GATES.updateRole}>
              <Button variant="ghost" size="sm" onClick={() => onEdit(role)}>Edit</Button>
            </Can>
          )}
          {!role.system && (
            <Can gate={FEATURE_GATES.deleteRole}>
              <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(true)}>Delete</Button>
            </Can>
          )}
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {role.system && (
          <p className="text-xs text-muted-foreground">
            System roles ship with the platform and reject every mutation (409 SYSTEM_IMMUTABLE).
          </p>
        )}

        {!role.system && (
          <section className="space-y-2 border-b pb-3">
            {renaming ? (
              <form
                className="flex items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!name.trim()) return;
                  rename.mutate(
                    { id: role.id, name: name.trim() },
                    { onSuccess: (r) => { setRenaming(false); setActionError(null); onChanged(r); }, onError: fail },
                  );
                }}
              >
                <Input value={name} onChange={(e) => setName(e.target.value)} aria-label="Edit role name" className="h-8 text-xs" />
                <Button type="submit" size="sm" disabled={!name.trim() || rename.isPending}>Save</Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => setRenaming(false)}>Cancel</Button>
              </form>
            ) : (
              <Can gate={FEATURE_GATES.updateRole}>
                <Button variant="outline" size="sm" onClick={() => { setName(role.name); setRenaming(true); }}>
                  Rename
                </Button>
              </Can>
            )}
          </section>
        )}

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium uppercase text-muted-foreground">
              Actions ({role.actions.length})
            </p>
            {!role.system && !editingActions && (
              <Can gate={FEATURE_GATES.updateRole}>
                <Button variant="ghost" size="sm"
                  onClick={() => { setActionsRaw(role.actions.join("\n")); setEditingActions(true); }}>
                  Edit actions
                </Button>
              </Can>
            )}
          </div>
          {editingActions ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                const actions = parseActions(actionsRaw);
                if (actions.length === 0) return;
                setActions.mutate(
                  { id: role.id, actions },
                  { onSuccess: (r) => { setEditingActions(false); setActionError(null); onChanged(r); }, onError: fail },
                );
              }}
            >
              <textarea
                value={actionsRaw}
                onChange={(e) => setActionsRaw(e.target.value)}
                rows={8}
                aria-label="Edit role actions"
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 font-mono text-xs"
              />
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={parseActions(actionsRaw).length === 0 || setActions.isPending}>
                  Save actions
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => setEditingActions(false)}>Cancel</Button>
              </div>
            </form>
          ) : role.actions.length ? (
            <ul className="flex max-h-64 flex-wrap gap-1 overflow-y-auto">
              {role.actions.map((a) => <li key={a}><Badge variant="secondary">{a}</Badge></li>)}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">
              No actions returned on the list row — the roles list omits per-role actions for
              some system rows; the service remains the source of truth.
            </p>
          )}
        </section>

        {actionError && (
          <p role="alert" className="text-xs text-destructive" data-testid="role-action-error">{actionError}</p>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmingDelete}
        onOpenChange={setConfirmingDelete}
        title="Delete role"
        description={`Delete the custom role "${role.name}"? Teams bound to it lose the grants it carried. This cannot be undone.`}
        confirmLabel="Delete"
        destructive
        onConfirm={() => {
          deleteRole.mutate(role.id, {
            onSuccess: () => { setActionError(null); onDeleted(); },
            onError: fail,
            onSettled: () => setConfirmingDelete(false),
          });
        }}
      />
    </Card>
  );
}
