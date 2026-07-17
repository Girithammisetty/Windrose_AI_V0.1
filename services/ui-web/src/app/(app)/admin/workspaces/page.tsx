"use client";
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Boxes, Plus, X } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Input, Label, Badge, Card, CardHeader, CardTitle, CardContent } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useWorkspaces, useCreateWorkspace, useUsers, useGroups,
  // Tier 4b: workspace lifecycle + content groups + content grants.
  useUpdateWorkspace, useArchiveWorkspace, useRestoreWorkspace,
  useLinkWorkspaceContentGroup, useUnlinkWorkspaceContentGroup, useCreateGroup, useUpdateGroup,
  useContentGrants, useCreateContentGrant, useDeleteContentGrant,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { EffectiveAccessEntry, Group, Workspace } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

export default function AdminWorkspacesPage() {
  const [showArchived, setShowArchived] = useState(false);
  const query = useWorkspaces({ archived: showArchived ? "with" : undefined });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<Workspace | null>(null);

  const columns: Column<Workspace>[] = [
    { id: "name", header: "Name", cell: (w) => <span className="font-medium">{w.name}</span> },
    { id: "desc", header: "Description", cell: (w) => w.description || <span className="text-muted-foreground">—</span> },
    {
      id: "visibility", header: "Visibility", width: 120,
      cell: (w) => <Badge variant="secondary">{w.public ? "public" : "private"}</Badge>,
    },
    {
      id: "state", header: "State", width: 120,
      cell: (w) => w.archived
        ? <Badge variant="warning">archived</Badge>
        : <Badge variant="success">active</Badge>,
    },
    { id: "created", header: "Created", width: 170, cell: (w) => formatLocal(w.createdAt) },
  ];

  return (
    <div>
      <PageHeader
        title="Workspaces"
        description="Content boundaries (rbac-service). Each workspace scopes datasets, dashboards, and grants. Select a workspace to edit it, manage sharing, or archive it."
        actions={
          <Can gate={FEATURE_GATES.createWorkspace}>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus /> New workspace
            </Button>
          </Can>
        }
      />

      <div className="mb-3 flex items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)}
            className="size-4 accent-[hsl(var(--primary))]" />
          Include archived
        </label>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_440px]">
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle="No workspaces yet."
          emptyCta={
            <Can gate={FEATURE_GATES.createWorkspace}>
              <Button className="mt-2" onClick={() => setCreateOpen(true)}><Plus /> New workspace</Button>
            </Can>
          }
          onRetry={() => query.refetch()}
        >
          <DataTable
            ariaLabel="Workspaces"
            rows={rows}
            columns={columns}
            rowId={(w) => w.id}
            onRowActivate={(w) => setSelected(w)}
            hasMore={query.hasNextPage}
            isFetchingMore={query.isFetchingNextPage}
            onLoadMore={() => query.fetchNextPage()}
          />
        </AsyncBoundary>

        <WorkspaceDetail
          workspace={selected}
          onClose={() => setSelected(null)}
          onChanged={(w) => setSelected(w)}
        />
      </div>

      <CreateWorkspaceDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(w) => setSelected(w)}
      />
    </div>
  );
}

function CreateWorkspaceDialog({
  open, onOpenChange, onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (w: Workspace) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const create = useCreateWorkspace();
  const error = create.error instanceof GraphQLRequestError ? create.error : null;

  const reset = () => { setName(""); setDescription(""); setIsPublic(false); create.reset(); };
  const submit = () => {
    if (!name.trim()) return;
    create.mutate(
      { name: name.trim(), description: description.trim() || undefined, public: isPublic },
      { onSuccess: (r) => { onOpenChange(false); reset(); onCreated(r.createWorkspace); } },
    );
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">New workspace</Dialog.Title>
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <div className="space-y-1.5">
              <Label htmlFor="ws-name">Name</Label>
              <Input id="ws-name" value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="Claims Q3" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ws-desc">Description (optional)</Label>
              <Input id="ws-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)}
                className="size-4 accent-[hsl(var(--primary))]" />
              Public (visible to all tenant members)
            </label>
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}{error.traceId ? ` (trace: ${error.traceId})` : ""}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button type="submit" disabled={!name.trim() || create.isPending}>
                {create.isPending ? "Creating…" : "Create"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/* ---- Tier 4b: workspace detail — edit, lifecycle, sharing, content groups --- */
function WorkspaceDetail({
  workspace, onClose, onChanged,
}: {
  workspace: Workspace | null;
  onClose: () => void;
  onChanged: (w: Workspace) => void;
}) {
  if (!workspace) {
    return (
      <Card className="h-fit">
        <CardHeader><CardTitle className="text-sm">Workspace detail</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <Boxes className="size-6" aria-hidden />
            <p>Select a workspace to edit it, manage sharing and content groups, or archive it.</p>
          </div>
        </CardContent>
      </Card>
    );
  }
  return <WorkspaceDetailBody key={workspace.id} workspace={workspace} onClose={onClose} onChanged={onChanged} />;
}

function WorkspaceDetailBody({
  workspace, onClose, onChanged,
}: {
  workspace: Workspace;
  onClose: () => void;
  onChanged: (w: Workspace) => void;
}) {
  const update = useUpdateWorkspace();
  const archive = useArchiveWorkspace();
  const restore = useRestoreWorkspace();

  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(workspace.name);
  const [description, setDescription] = useState(workspace.description ?? "");
  const [isPublic, setIsPublic] = useState(!!workspace.public);
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  const [confirmingRestore, setConfirmingRestore] = useState(false);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);

  const fail = (e: Error) => {
    const trace = e instanceof GraphQLRequestError && e.traceId ? ` (trace: ${e.traceId})` : "";
    setLifecycleError(`${e.message}${trace}`);
  };

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          {workspace.name}
          {workspace.archived && <Badge variant="warning">archived</Badge>}
        </CardTitle>
        <div className="flex items-center gap-1">
          <Can gate={FEATURE_GATES.adminWorkspace}>
            {workspace.archived ? (
              <Button variant="ghost" size="sm" onClick={() => setConfirmingRestore(true)}>Restore</Button>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setConfirmingArchive(true)}>Archive</Button>
            )}
          </Can>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <section className="space-y-2 border-b pb-3">
          {editing ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                update.mutate(
                  { id: workspace.id, input: { name: name.trim() || undefined, description, public: isPublic } },
                  { onSuccess: (w) => { setEditing(false); onChanged(w); }, onError: fail },
                );
              }}
            >
              <Input value={name} onChange={(e) => setName(e.target.value)} aria-label="Edit workspace name" />
              <Input value={description} onChange={(e) => setDescription(e.target.value)} aria-label="Edit workspace description" placeholder="Description" />
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)}
                  className="size-4 accent-[hsl(var(--primary))]" aria-label="Public workspace" />
                Public (visible to all tenant members)
              </label>
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={update.isPending}>Save</Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
              </div>
              {update.error && <p className="text-xs text-destructive">{update.error.message}</p>}
            </form>
          ) : (
            <div className="flex items-center justify-between">
              <p className="text-muted-foreground">
                {workspace.description || "No description."}{" "}
                <Badge variant="secondary">{workspace.public ? "public" : "private"}</Badge>
              </p>
              <Can gate={FEATURE_GATES.updateWorkspace}>
                <Button
                  variant="ghost" size="sm"
                  onClick={() => {
                    setName(workspace.name);
                    setDescription(workspace.description ?? "");
                    setIsPublic(!!workspace.public);
                    setEditing(true);
                  }}
                >
                  Edit
                </Button>
              </Can>
            </div>
          )}
          {lifecycleError && (
            <p role="alert" className="text-xs text-destructive" data-testid="workspace-action-error">{lifecycleError}</p>
          )}
        </section>

        <Can gate={FEATURE_GATES.listGrants}>
          <SharingSection workspaceId={workspace.id} />
        </Can>

        <ContentGroupsSection workspaceId={workspace.id} />
      </CardContent>

      <ConfirmDialog
        open={confirmingArchive}
        onOpenChange={setConfirmingArchive}
        title="Archive workspace"
        description={`Archive "${workspace.name}"? Everything scoped to it — datasets, dashboards, saved queries, content grants — stops resolving for non-admin members until it is restored. The workspace itself is not deleted.`}
        confirmLabel="Archive"
        destructive
        onConfirm={() => {
          archive.mutate(workspace.id, {
            onSuccess: (w) => { setLifecycleError(null); onChanged(w); },
            onError: fail,
            onSettled: () => setConfirmingArchive(false),
          });
        }}
      />
      <ConfirmDialog
        open={confirmingRestore}
        onOpenChange={setConfirmingRestore}
        title="Restore workspace"
        description={`Restore "${workspace.name}"? Members regain access to its content.`}
        confirmLabel="Restore"
        onConfirm={() => {
          restore.mutate(workspace.id, {
            onSuccess: (w) => { setLifecycleError(null); onChanged(w); },
            onError: fail,
            onSettled: () => setConfirmingRestore(false),
          });
        }}
      />
    </Card>
  );
}

/** Sharing / grants: effective access for a resource URN + grant create/delete.
 * rbac's GET /grants REQUIRES a resource_urn — the lookup is per-resource by
 * design, so the admin pastes/types the URN (copyable from every detail screen). */
function SharingSection({ workspaceId }: { workspaceId: string }) {
  const [urnInput, setUrnInput] = useState("");
  const [resourceUrn, setResourceUrn] = useState<string | null>(null);
  const grants = useContentGrants(resourceUrn);
  const createGrant = useCreateContentGrant();
  const deleteGrant = useDeleteContentGrant();
  const [deleting, setDeleting] = useState<EffectiveAccessEntry | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  // Subject pickers fed by the real directory + group list.
  const users = useUsers();
  const groups = useGroups({});
  const userOptions = useMemo(() => users.data?.pages.flatMap((p) => p.nodes) ?? [], [users.data]);
  const groupOptions = useMemo(() => groups.data?.pages.flatMap((p) => p.nodes) ?? [], [groups.data]);

  const [subjectType, setSubjectType] = useState<"user" | "group">("user");
  const [subjectId, setSubjectId] = useState("");
  const [level, setLevel] = useState<"viewer" | "editor" | "owner">("viewer");

  return (
    <section className="space-y-2 border-b pb-3">
      <p className="text-xs font-medium uppercase text-muted-foreground">Sharing / grants</p>
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => { e.preventDefault(); if (urnInput.trim()) setResourceUrn(urnInput.trim()); }}
      >
        <Input value={urnInput} onChange={(e) => setUrnInput(e.target.value)}
          placeholder="resource URN, e.g. wr:<tenant>:dataset:dataset/<id>"
          aria-label="Resource URN" className="h-8 flex-1 font-mono text-xs" />
        <Button type="submit" size="sm" disabled={!urnInput.trim()}>Look up</Button>
      </form>

      {resourceUrn && (
        <AsyncBoundary
          isLoading={grants.isLoading}
          isError={grants.isError}
          error={grants.error}
          isEmpty={(grants.data?.length ?? 0) === 0}
          emptyTitle="No grants on this resource."
          onRetry={() => grants.refetch()}
        >
          <ul className="space-y-1" data-testid="grant-list">
            {(grants.data ?? []).map((g, i) => (
              <li key={`${g.grantId}-${g.subjectType}-${g.subjectId}-${i}`}
                className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5">
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs">{g.subjectType}:{g.subjectId}</p>
                  <p className="text-xs text-muted-foreground">
                    {g.level} · {g.provenance}{g.via ? ` via ${g.via}` : ""}
                  </p>
                </div>
                {/* Only DIRECT grants are deletable rows — implicit-creator and
                    via_group entries are derived, not standalone grants. */}
                {g.provenance === "direct" && g.grantId && (
                  <Can gate={FEATURE_GATES.deleteGrant}>
                    <Button variant="ghost" size="sm" onClick={() => setDeleting(g)}>Remove</Button>
                  </Can>
                )}
              </li>
            ))}
          </ul>
        </AsyncBoundary>
      )}

      {resourceUrn && (
        <Can gate={FEATURE_GATES.createGrant}>
          <form
            className="flex flex-wrap items-center gap-2 pt-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (!subjectId) return;
              createGrant.mutate(
                { workspaceId, resourceUrn, subjectType, subjectId, level },
                {
                  onSuccess: () => { setBanner(`Granted ${level} to ${subjectType} ${subjectId}.`); setSubjectId(""); },
                  onError: (err) => setBanner(err.message),
                },
              );
            }}
          >
            <select value={subjectType} aria-label="Grant subject type"
              onChange={(e) => { setSubjectType(e.target.value as "user" | "group"); setSubjectId(""); }}
              className="h-8 rounded-md border border-input bg-background px-2 text-xs">
              <option value="user">user</option>
              <option value="group">group</option>
            </select>
            <select value={subjectId} aria-label="Grant subject"
              onChange={(e) => setSubjectId(e.target.value)}
              className="h-8 min-w-40 flex-1 rounded-md border border-input bg-background px-2 text-xs">
              <option value="">select {subjectType}…</option>
              {subjectType === "user"
                ? userOptions.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)
                : groupOptions.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
            <select value={level} aria-label="Grant level"
              onChange={(e) => setLevel(e.target.value as "viewer" | "editor" | "owner")}
              className="h-8 rounded-md border border-input bg-background px-2 text-xs">
              <option value="viewer">viewer</option>
              <option value="editor">editor</option>
              <option value="owner">owner</option>
            </select>
            <Button type="submit" size="sm" disabled={!subjectId || createGrant.isPending}>Grant</Button>
          </form>
        </Can>
      )}
      {banner && <p role="status" className="text-xs text-muted-foreground">{banner}</p>}

      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Remove grant"
        description={`Remove the ${deleting?.level} grant for ${deleting?.subjectType} ${deleting?.subjectId}? Access via other grants or groups is unaffected.`}
        confirmLabel="Remove"
        destructive
        onConfirm={() => {
          if (!deleting) return;
          deleteGrant.mutate(deleting.grantId, {
            onSuccess: () => setBanner("Grant removed."),
            onError: (err) => setBanner(err.message),
            onSettled: () => setDeleting(null),
          });
        }}
      />
    </section>
  );
}

/** Content groups: the content-type rbac groups + link/unlink to THIS workspace.
 * rbac exposes no read for a workspace's currently linked groups — link/unlink
 * are write-only (same honesty note as team role bindings). */
function ContentGroupsSection({ workspaceId }: { workspaceId: string }) {
  const groups = useGroups({ type: "content" });
  const rows = useMemo(() => groups.data?.pages.flatMap((p) => p.nodes) ?? [], [groups.data]);
  const link = useLinkWorkspaceContentGroup();
  const unlink = useUnlinkWorkspaceContentGroup();
  const [creating, setCreating] = useState(false);
  // Edit-in-place: the same create dialog reused against an existing content
  // group; group_type is immutable so only name + description commit.
  const [editingGroup, setEditingGroup] = useState<Group | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium uppercase text-muted-foreground">Content groups</p>
        <Can gate={FEATURE_GATES.createContentGroup}>
          <Button variant="ghost" size="sm" onClick={() => setCreating(true)}>New content group</Button>
        </Can>
      </div>
      <p className="text-xs text-muted-foreground">
        rbac-service has no read endpoint for a workspace&apos;s currently linked groups —
        link or unlink below (write-only).
      </p>
      <AsyncBoundary
        isLoading={groups.isLoading}
        isError={groups.isError}
        error={groups.error}
        isEmpty={rows.length === 0}
        emptyTitle="No content groups in this tenant yet."
        onRetry={() => groups.refetch()}
      >
        <ul className="space-y-1">
          {rows.map((g) => (
            <li key={g.id} className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5">
              <div className="min-w-0">
                <p className="truncate text-xs font-medium">{g.name}</p>
                {g.description && <p className="truncate text-xs text-muted-foreground">{g.description}</p>}
              </div>
              <span className="flex gap-1">
                <Can gate={FEATURE_GATES.createContentGroup}>
                  <Button variant="ghost" size="sm" onClick={() => setEditingGroup(g)}>
                    Edit
                  </Button>
                </Can>
                <Can gate={FEATURE_GATES.updateWorkspace}>
                  <Button variant="ghost" size="sm" disabled={link.isPending}
                    onClick={() => link.mutate({ workspaceId, groupId: g.id }, {
                      onSuccess: () => setBanner(`Linked ${g.name}.`),
                      onError: (err) => setBanner(err.message),
                    })}>
                    Link
                  </Button>
                  <Button variant="ghost" size="sm" disabled={unlink.isPending}
                    onClick={() => unlink.mutate({ workspaceId, groupId: g.id }, {
                      onSuccess: () => setBanner(`Unlinked ${g.name}.`),
                      onError: (err) => setBanner(err.message),
                    })}>
                    Unlink
                  </Button>
                </Can>
              </span>
            </li>
          ))}
        </ul>
      </AsyncBoundary>
      {banner && <p role="status" className="text-xs text-muted-foreground">{banner}</p>}

      <NewContentGroupDialog
        open={creating || !!editingGroup}
        editGroup={editingGroup}
        onOpenChange={(o) => { if (!o) { setCreating(false); setEditingGroup(null); } }}
      />
    </section>
  );
}

/**
 * Content-group authoring dialog, reused in two modes: create (blank) and edit
 * (prefilled from `editGroup` and committed via updateGroup). group_type is
 * immutable (always content) so only name + description are editable.
 */
function NewContentGroupDialog({
  open, onOpenChange, editGroup,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  editGroup?: Group | null;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useCreateGroup();
  const update = useUpdateGroup();
  const isEdit = !!editGroup;
  const active = isEdit ? update : create;
  const error = active.error instanceof GraphQLRequestError ? active.error : null;

  // Prefill each time the dialog opens: from the group being edited, else blank.
  useEffect(() => {
    if (open) {
      setName(editGroup?.name ?? "");
      setDescription(editGroup?.description ?? "");
      create.reset();
      update.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open
  }, [open]);

  const reset = () => { setName(""); setDescription(""); create.reset(); update.reset(); };
  const submit = () => {
    if (!name.trim()) return;
    const done = { onSuccess: () => { onOpenChange(false); reset(); } };
    if (editGroup) {
      update.mutate({ id: editGroup.id, name: name.trim(), description: description.trim() || undefined }, done);
    } else {
      create.mutate({ name: name.trim(), description: description.trim() || undefined, groupType: "CONTENT" }, done);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">{isEdit ? "Edit content group" : "New content group"}</Dialog.Title>
          <p className="mt-1 text-xs text-muted-foreground">
            Content groups carry workspace/data access (rbac group_type=content) — distinct
            from Teams, which carry roles.
          </p>
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <div className="space-y-1.5">
              <Label htmlFor="cg-name">Name</Label>
              <Input id="cg-name" value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="Underwriting documents" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cg-desc">Description (optional)</Label>
              <Input id="cg-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}{error.traceId ? ` (trace: ${error.traceId})` : ""}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button type="submit" disabled={!name.trim() || active.isPending}>
                {isEdit ? (update.isPending ? "Saving…" : "Save") : (create.isPending ? "Creating…" : "Create")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
