"use client";
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { UserPlus } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Can } from "@/components/authz/Can";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useUsers, useInviteUser, useGroups, useUserGroups,
  // Tier 4b: identity user lifecycle.
  useUpdateUser, useDeactivateUser, useResendUserInvite, useDeleteUser,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { User } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

/** Map identity user status → the StatusChip's known lifecycle vocabulary. */
function statusChip(status?: string | null) {
  if (!status) return <span className="text-muted-foreground">—</span>;
  const map: Record<string, string> = { active: "SUCCEEDED", invited: "PENDING", deactivated: "FAILED" };
  return <StatusChip status={map[status] ?? status.toUpperCase()} />;
}

export default function AdminUsersPage() {
  const query = useUsers();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [selected, setSelected] = useState<User | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);

  const columns: Column<User>[] = [
    { id: "email", header: "Email", cell: (u) => <span className="font-medium">{u.email}</span> },
    { id: "name", header: "Name", width: 200, cell: (u) => u.fullName || <span className="text-muted-foreground">—</span> },
    { id: "status", header: "Status", width: 130, cell: (u) => statusChip(u.status) },
    { id: "lastLogin", header: "Last sign-in", width: 180, cell: (u) => formatLocal(u.lastLoginAt) },
    { id: "created", header: "Created", width: 180, cell: (u) => formatLocal(u.createdAt) },
  ];

  return (
    <div>
      <PageHeader
        title="Users"
        description="The tenant user directory (identity-service). Invite users, view status and sign-in activity."
        actions={
          <Can gate={FEATURE_GATES.inviteUser}>
            <Button onClick={() => setInviteOpen(true)}>
              <UserPlus /> Invite user
            </Button>
          </Can>
        }
      />

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No users in this tenant yet."
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Users"
          rows={rows}
          columns={columns}
          rowId={(u) => u.id}
          onRowActivate={(u) => setSelected(u)}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
        />
      </AsyncBoundary>

      <UserDetailDialog user={selected} onClose={() => setSelected(null)} />
      <InviteUserDialog open={inviteOpen} onOpenChange={setInviteOpen} />
    </div>
  );
}

function UserDetailDialog({ user, onClose }: { user: User | null; onClose: () => void }) {
  // Tier 4b: identity user lifecycle (identity-service, guard identity.user.admin).
  const updateUser = useUpdateUser();
  const deactivate = useDeactivateUser();
  const resendInvite = useResendUserInvite();
  const deleteUser = useDeleteUser();
  // The rbac groups this user belongs to (rbac-service GET /users/{id}/groups).
  const userGroups = useUserGroups(user?.id ?? null);

  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState("");
  const [confirmingDeactivate, setConfirmingDeactivate] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Surface the REAL downstream error (e.g. the last-admin guard's 409) verbatim.
  const fail = (e: Error) => {
    const trace = e instanceof GraphQLRequestError && e.traceId ? ` (trace: ${e.traceId})` : "";
    setActionError(`${e.message}${trace}`);
  };
  const ok = (msg: string) => { setActionError(null); setBanner(msg); };

  const close = () => {
    setEditingName(false); setBanner(null); setActionError(null);
    setConfirmingDeactivate(false); setConfirmingDelete(false);
    onClose();
  };

  return (
    <Dialog.Root open={!!user} onOpenChange={(o) => !o && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">User detail</Dialog.Title>
          {user && (
            <dl className="mt-4 space-y-2 text-sm">
              <Row label="Email" value={user.email} />
              <Row label="Name" value={user.fullName || "—"} />
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">Status</dt>
                <dd>{statusChip(user.status)}</dd>
              </div>
              <Row label="Last sign-in" value={formatLocal(user.lastLoginAt)} />
              <Row label="Created" value={formatLocal(user.createdAt)} />
              <Row label="User ID" value={user.id} mono />
              <Row label="URN" value={user.urn} mono />
            </dl>
          )}
          {user && (
            <Can gate={FEATURE_GATES.manageUserLifecycle}>
              <div className="mt-4 space-y-2 border-t pt-3">
                {editingName ? (
                  <form
                    className="flex items-center gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (!name.trim()) return;
                      updateUser.mutate(
                        { id: user.id, fullName: name.trim() },
                        { onSuccess: (u) => { setEditingName(false); ok(`Renamed to ${u.fullName}.`); }, onError: fail },
                      );
                    }}
                  >
                    <Input value={name} onChange={(e) => setName(e.target.value)} aria-label="Edit full name" className="h-8 text-xs" />
                    <Button type="submit" size="sm" disabled={!name.trim() || updateUser.isPending}>Save</Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setEditingName(false)}>Cancel</Button>
                  </form>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm"
                      onClick={() => { setName(user.fullName ?? ""); setEditingName(true); }}>
                      Edit name
                    </Button>
                    {user.status === "invited" && (
                      <Button variant="outline" size="sm" disabled={resendInvite.isPending}
                        onClick={() => resendInvite.mutate(user.id, {
                          onSuccess: () => ok("Invite re-sent."), onError: fail,
                        })}>
                        Resend invite
                      </Button>
                    )}
                    {user.status !== "deactivated" && (
                      <Button variant="outline" size="sm" onClick={() => setConfirmingDeactivate(true)}>
                        Deactivate
                      </Button>
                    )}
                    <Button variant="destructive" size="sm" onClick={() => setConfirmingDelete(true)}>
                      Delete
                    </Button>
                  </div>
                )}
                {banner && <p role="status" className="text-xs text-emerald-600 dark:text-emerald-400">{banner}</p>}
                {actionError && (
                  <p role="alert" className="text-xs text-destructive" data-testid="user-action-error">{actionError}</p>
                )}
              </div>
            </Can>
          )}
          {user && (
            <div className="mt-4 space-y-2 border-t pt-3">
              <p className="text-xs font-medium uppercase text-muted-foreground">Groups</p>
              <AsyncBoundary
                isLoading={userGroups.isLoading}
                isError={userGroups.isError}
                error={userGroups.error}
                isEmpty={(userGroups.data?.length ?? 0) === 0}
                emptyTitle="Not a member of any group."
                onRetry={() => userGroups.refetch()}
              >
                <ul className="space-y-1">
                  {(userGroups.data ?? []).map((g) => (
                    <li key={g.id} className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-sm">
                      <span className="truncate font-medium">{g.name}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">{g.groupType}</span>
                    </li>
                  ))}
                </ul>
              </AsyncBoundary>
            </div>
          )}
          <p className="mt-4 text-xs text-muted-foreground">
            Per-user role assignments live in rbac-service (via groups). This directory shows the
            identity-service profile; the groups above are this user&apos;s rbac group memberships.
          </p>
          <div className="mt-5 flex justify-end">
            <Button variant="outline" onClick={close}>Close</Button>
          </div>

          {user && (
            <>
              <ConfirmDialog
                open={confirmingDeactivate}
                onOpenChange={setConfirmingDeactivate}
                title="Deactivate user"
                description={`Deactivate ${user.email}? They lose access immediately; the account can be reactivated by identity-service. Deactivating the last tenant admin is refused by the service (last-admin guard).`}
                confirmLabel="Deactivate"
                destructive
                onConfirm={() => {
                  deactivate.mutate(
                    { id: user.id },
                    {
                      onSuccess: () => ok(`${user.email} deactivated.`),
                      onError: fail,
                      onSettled: () => setConfirmingDeactivate(false),
                    },
                  );
                }}
              />
              <ConfirmDialog
                open={confirmingDelete}
                onOpenChange={setConfirmingDelete}
                title="Delete user"
                description={`Soft-delete ${user.email} from the directory. This is irreversible from this screen.`}
                confirmLabel="Delete"
                confirmPhrase={user.email}
                destructive
                onConfirm={() => {
                  deleteUser.mutate(user.id, {
                    onSuccess: close,
                    onError: fail,
                    onSettled: () => setConfirmingDelete(false),
                  });
                }}
              />
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function InviteUserDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [ok, setOk] = useState<string | null>(null);
  const invite = useInviteUser();
  // Permission groups (teams) the invitee can be placed into on acceptance, so
  // they arrive with a role instead of zero permissions (identity emits the
  // groups on the user.invited event; rbac-service's consumer binds them).
  const groupsQuery = useGroups({ type: "permission" });
  const availableGroups = groupsQuery.data?.pages.flatMap((p) => p.nodes) ?? [];
  const error = invite.error instanceof GraphQLRequestError ? invite.error : null;

  const reset = () => { setEmail(""); setFullName(""); setGroups([]); setOk(null); invite.reset(); };

  const toggleGroup = (id: string) =>
    setGroups((g) => (g.includes(id) ? g.filter((x) => x !== id) : [...g, id]));

  const submit = () => {
    setOk(null);
    if (!email.trim()) return;
    invite.mutate(
      {
        email: email.trim(),
        fullName: fullName.trim() || undefined,
        groups: groups.length > 0 ? groups : undefined,
      },
      { onSuccess: (r) => { setOk(`Invited ${r.inviteUser.email} (${r.inviteUser.status}).`); setEmail(""); setFullName(""); setGroups([]); } },
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
          <Dialog.Title className="text-lg font-semibold">Invite user</Dialog.Title>
          <p className="mt-1 text-xs text-muted-foreground">
            Creates the user in the &ldquo;invited&rdquo; state via identity-service. The identity
            provider (Keycloak) path is exercised for real; if it errors, the failure is shown
            honestly rather than reported as success.
          </p>
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <div className="space-y-1.5">
              <Label htmlFor="invite-email">Email</Label>
              <Input id="invite-email" type="email" value={email} autoFocus
                onChange={(e) => setEmail(e.target.value)} placeholder="person@company.com" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-name">Full name (optional)</Label>
              <Input id="invite-name" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Ada Lovelace" />
            </div>
            <div className="space-y-1.5">
              <Label>Teams (optional)</Label>
              <p className="text-xs text-muted-foreground">
                Placing the invitee in one or more teams grants them a role on acceptance,
                so they arrive with permissions instead of none.
              </p>
              {availableGroups.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {groupsQuery.isLoading ? "Loading teams…" : "No teams available."}
                </p>
              ) : (
                <div className="max-h-32 overflow-y-auto rounded-md border p-2">
                  {availableGroups.map((g) => (
                    <label key={g.id} className="flex items-center gap-2 py-0.5 text-sm">
                      <input
                        type="checkbox"
                        checked={groups.includes(g.id)}
                        onChange={() => toggleGroup(g.id)}
                      />
                      <span>{g.name}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
            {ok && <p role="status" className="text-xs text-emerald-600 dark:text-emerald-400">{ok}</p>}
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="invite-error">
                {error.message}{error.traceId ? ` (trace: ${error.traceId})` : ""}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
              <Button type="submit" disabled={!email.trim() || invite.isPending}>
                {invite.isPending ? "Inviting…" : "Send invite"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "truncate font-mono text-xs" : "font-medium"}>{value}</dd>
    </div>
  );
}
