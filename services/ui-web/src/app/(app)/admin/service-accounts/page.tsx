"use client";
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, Plus, X } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useServiceAccounts,
  // Tier 4b: service-account lifecycle (identity.service_account.admin).
  useCreateServiceAccount, useRotateServiceAccount, useRevokeServiceAccount,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { ServiceAccount } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

export default function AdminServiceAccountsPage() {
  const query = useServiceAccounts();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [createOpen, setCreateOpen] = useState(false);
  // The one-time api_key lives ONLY in this transient state — shown once via
  // SecretBanner, dismissible, never cached or persisted anywhere (BR-11).
  const [issuedKey, setIssuedKey] = useState<{ name: string; apiKey: string; kind: "created" | "rotated" } | null>(null);
  const [rotating, setRotating] = useState<ServiceAccount | null>(null);
  const [revoking, setRevoking] = useState<ServiceAccount | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const rotate = useRotateServiceAccount();
  const revoke = useRevokeServiceAccount();

  const fail = (e: Error) => {
    const trace = e instanceof GraphQLRequestError && e.traceId ? ` (trace: ${e.traceId})` : "";
    setActionError(`${e.message}${trace}`);
  };

  const columns: Column<ServiceAccount>[] = [
    { id: "name", header: "Name", cell: (s) => <span className="font-medium">{s.name}</span> },
    {
      id: "state", header: "State", width: 120,
      cell: (s) => s.revokedAt
        ? <Badge variant="destructive">revoked</Badge>
        : <Badge variant="success">active</Badge>,
    },
    {
      id: "scopes", header: "Scopes",
      cell: (s) => s.scopes.length
        ? (
          <span className="flex flex-wrap gap-1">
            {s.scopes.slice(0, 4).map((sc) => <Badge key={sc} variant="secondary">{sc}</Badge>)}
            {s.scopes.length > 4 ? <Badge variant="secondary">+{s.scopes.length - 4}</Badge> : null}
          </span>
        )
        : <span className="text-muted-foreground">—</span>,
    },
    { id: "lastUsed", header: "Last used", width: 170, cell: (s) => formatLocal(s.lastUsedAt) },
    { id: "expires", header: "Expires", width: 170, cell: (s) => formatLocal(s.expiresAt) },
    {
      id: "actions", header: "", width: 170,
      // Rotate/revoke are hidden for already-revoked accounts (irreversible).
      cell: (s) => s.revokedAt ? null : (
        <Can gate={FEATURE_GATES.manageServiceAccounts}>
          <span className="flex gap-1">
            <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setRotating(s); }}>
              Rotate
            </Button>
            <Button variant="ghost" size="sm" className="text-destructive"
              onClick={(e) => { e.stopPropagation(); setRevoking(s); }}>
              Revoke
            </Button>
          </span>
        </Can>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Service accounts"
        description="Machine principals (identity-service). Secret material is never shown on reads — only metadata. A key is displayed exactly once, at create or rotate."
        actions={
          <Can gate={FEATURE_GATES.manageServiceAccounts}>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus /> New service account
            </Button>
          </Can>
        }
      />

      {issuedKey && (
        <SecretBanner
          label={`API key for ${issuedKey.name} (${issuedKey.kind}) — shown once, store it now`}
          secret={issuedKey.apiKey}
          onDismiss={() => setIssuedKey(null)}
        />
      )}
      {actionError && (
        <p role="alert" className="mb-3 text-xs text-destructive" data-testid="sa-action-error">{actionError}</p>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No service accounts yet."
        emptyCta={
          <Can gate={FEATURE_GATES.manageServiceAccounts}>
            <Button className="mt-2" onClick={() => setCreateOpen(true)}><Plus /> New service account</Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Service accounts"
          rows={rows}
          columns={columns}
          rowId={(s) => s.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
        />
      </AsyncBoundary>

      <CreateServiceAccountDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(name, apiKey) => setIssuedKey({ name, apiKey, kind: "created" })}
      />

      <ConfirmDialog
        open={!!rotating}
        onOpenChange={(o) => !o && setRotating(null)}
        title="Rotate API key"
        description={`Rotate the API key for "${rotating?.name}"? The current key stops working and a NEW key is issued — it will be shown exactly once.`}
        confirmLabel="Rotate"
        onConfirm={() => {
          if (!rotating) return;
          const name = rotating.name;
          rotate.mutate(rotating.id, {
            onSuccess: (r) => { setActionError(null); setIssuedKey({ name, apiKey: r.apiKey, kind: "rotated" }); },
            onError: fail,
            onSettled: () => setRotating(null),
          });
        }}
      />

      <ConfirmDialog
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title="Revoke service account"
        description={`Revoke "${revoking?.name}"? Its API key stops authenticating immediately. This is irreversible — create a new account to restore access.`}
        confirmLabel="Revoke"
        confirmPhrase={revoking?.name}
        destructive
        onConfirm={() => {
          if (!revoking) return;
          revoke.mutate(revoking.id, {
            onSuccess: () => setActionError(null),
            onError: fail,
            onSettled: () => setRevoking(null),
          });
        }}
      />
    </div>
  );
}

/** One-time secret display (same idiom as the webhook signing secret on
 * /admin/notifications): copy + dismiss; never persisted. */
function SecretBanner({ label, secret, onDismiss }: { label: string; secret: string; onDismiss: () => void }) {
  return (
    <div role="alert" className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-warning/50 bg-warning/10 p-3 text-xs">
      <KeyRound className="size-4" aria-hidden />
      <span className="font-medium">{label}</span>
      <code data-testid="sa-api-key" className="rounded bg-background px-2 py-1 font-mono">{secret}</code>
      <Button variant="ghost" size="sm" onClick={() => void navigator.clipboard?.writeText(secret)}>Copy</Button>
      <Button variant="ghost" size="icon" aria-label="Dismiss API key" onClick={onDismiss}><X className="size-4" /></Button>
    </div>
  );
}

function CreateServiceAccountDialog({
  open, onOpenChange, onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (name: string, apiKey: string) => void;
}) {
  const [name, setName] = useState("");
  const [scopesRaw, setScopesRaw] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const create = useCreateServiceAccount();
  const error = create.error instanceof GraphQLRequestError ? create.error : null;

  const reset = () => { setName(""); setScopesRaw(""); setExpiresAt(""); create.reset(); };

  const submit = () => {
    if (!name.trim()) return;
    // Scopes: comma- or whitespace-separated action names → array.
    const scopes = scopesRaw.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
    create.mutate(
      {
        name: name.trim(),
        scopes: scopes.length ? scopes : undefined,
        expiresAt: expiresAt ? new Date(expiresAt).toISOString() : undefined,
      },
      {
        onSuccess: (r) => {
          onOpenChange(false);
          reset();
          onCreated(r.serviceAccount.name, r.apiKey);
        },
      },
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
          <Dialog.Title className="text-lg font-semibold">New service account</Dialog.Title>
          <p className="mt-1 text-xs text-muted-foreground">
            The API key is returned once on creation and can never be retrieved again —
            copy it from the banner immediately after creating.
          </p>
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <div className="space-y-1.5">
              <Label htmlFor="sa-name">Name</Label>
              <Input id="sa-name" value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="etl-bot" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sa-scopes">Scopes (comma or space separated, optional)</Label>
              <Input id="sa-scopes" value={scopesRaw} onChange={(e) => setScopesRaw(e.target.value)}
                placeholder="dataset.dataset.read, pipeline.run.create" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sa-expires">Expires (optional)</Label>
              <Input id="sa-expires" type="date" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} />
            </div>
            {create.error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {create.error.message}{error?.traceId ? ` (trace: ${error.traceId})` : ""}
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
