"use client";
import { useMemo, useState } from "react";
import { Plus, KeyRound, Ban, RefreshCw, Copy } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useAiKeys, useCreateAiVirtualKey, useRevokeAiVirtualKey, useRotateAiVirtualKey } from "@/lib/graphql/hooks";
import type { AiVirtualKey } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

const REQUEST_CLASSES = ["chat", "sql-gen", "judge", "embed"];

export default function AiKeysPage() {
  const [creating, setCreating] = useState(false);
  const [toRevoke, setToRevoke] = useState<AiVirtualKey | null>(null);
  const [revealedSecret, setRevealedSecret] = useState<{ id: string; secret: string } | null>(null);

  const query = useAiKeys();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const create = useCreateAiVirtualKey();
  const revoke = useRevokeAiVirtualKey();
  const rotate = useRotateAiVirtualKey();

  const columns: Column<AiVirtualKey>[] = [
    { id: "principal", header: "Principal", cell: (k) => `${k.principalType}/${k.principalId}` },
    { id: "classes", header: "Allowed classes", cell: (k) => (k.allowedRequestClasses?.length ? k.allowedRequestClasses.join(", ") : "all") },
    { id: "maxRung", header: "Max rung", width: 90, cell: (k) => k.maxRung },
    { id: "status", header: "Status", width: 100, cell: (k) => <Badge variant={k.status === "active" ? "success" : "secondary"}>{k.status}</Badge> },
    { id: "expiresAt", header: "Expires", width: 170, cell: (k) => formatLocal(k.expiresAt) },
    { id: "createdAt", header: "Created", width: 170, cell: (k) => formatLocal(k.createdAt) },
    {
      id: "actions", header: "", width: 160,
      cell: (k) =>
        k.status === "active" ? (
          <Can gate={FEATURE_GATES.manageAiKeys}>
            <div className="flex gap-1">
              <Button
                size="sm" variant="outline" disabled={rotate.isPending}
                onClick={() => rotate.mutate(k.id, { onSuccess: (r) => r.secret && setRevealedSecret({ id: r.id, secret: r.secret }) })}
              >
                <RefreshCw className="size-3" /> Rotate
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setToRevoke(k)}><Ban className="size-3" /></Button>
            </div>
          </Can>
        ) : null,
    },
  ];

  const newButton = (
    <Can gate={FEATURE_GATES.manageAiKeys}>
      <Button onClick={() => setCreating((v) => !v)}><Plus /> {creating ? "Cancel" : "Issue key"}</Button>
    </Can>
  );

  return (
    <div>
      <PageHeader title="Virtual keys" description="Scoped API keys agents use to call the gateway (Authorization: Bearer nk-...)." actions={newButton} />

      {revealedSecret && (
        <Card className="mb-4 border-[hsl(var(--warning))]">
          <CardContent className="flex flex-wrap items-center justify-between gap-2 pt-4">
            <div>
              <p className="text-sm font-medium">Secret for key {revealedSecret.id} — shown ONCE, copy it now:</p>
              <code className="mt-1 block rounded bg-muted px-2 py-1 text-xs">{revealedSecret.secret}</code>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => navigator.clipboard?.writeText(revealedSecret.secret)}>
                <Copy className="size-3" /> Copy
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setRevealedSecret(null)}>Dismiss</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {creating && (
        <Card className="mb-4 border-primary/40">
          <CardContent className="pt-4">
            <NewKeyForm
              pending={create.isPending}
              error={create.error}
              onCreate={(input) =>
                create.mutate(input, {
                  onSuccess: (r) => {
                    setCreating(false);
                    if (r.secret) setRevealedSecret({ id: r.id, secret: r.secret });
                  },
                })
              }
            />
          </CardContent>
        </Card>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No virtual keys issued"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Virtual keys"
          rows={rows}
          columns={columns}
          rowId={(k) => k.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <KeyRound className="size-8" />
              <p>No keys</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ConfirmDialog
        open={!!toRevoke}
        onOpenChange={(o) => !o && setToRevoke(null)}
        title={`Revoke key for ${toRevoke?.principalId}?`}
        description="The principal immediately loses gateway access. This cannot be undone."
        confirmLabel="Revoke"
        destructive
        onConfirm={() => {
          if (toRevoke) revoke.mutate(toRevoke.id, { onSuccess: () => setToRevoke(null) });
        }}
      />
    </div>
  );
}

function NewKeyForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: { principalType: string; principalId: string; allowedRequestClasses?: string[]; maxRung?: number }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [principalType, setPrincipalType] = useState("agent");
  const [principalId, setPrincipalId] = useState("");
  const [classes, setClasses] = useState<string[]>([]);
  const [maxRung, setMaxRung] = useState("2");

  const toggle = (c: string) => setClasses((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]));

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (principalId.trim()) {
          onCreate({ principalType, principalId: principalId.trim(), allowedRequestClasses: classes.length ? classes : undefined, maxRung: maxRung.trim() ? Number(maxRung) : undefined });
        }
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="k-principal-type">Principal type</Label>
        <select id="k-principal-type" value={principalType} onChange={(e) => setPrincipalType(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
          <option value="user">user</option>
          <option value="agent">agent</option>
          <option value="service">service</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="k-principal-id">Principal id</Label>
        <Input id="k-principal-id" value={principalId} onChange={(e) => setPrincipalId(e.target.value)} placeholder="claims-agent" className="h-9 w-48" />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Allowed request classes</Label>
        <div className="flex gap-2 pt-1">
          {REQUEST_CLASSES.map((c) => (
            <label key={c} className="flex items-center gap-1 text-xs">
              <input type="checkbox" checked={classes.includes(c)} onChange={() => toggle(c)} /> {c}
            </label>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="k-max-rung">Max rung</Label>
        <Input id="k-max-rung" type="number" min="0" value={maxRung} onChange={(e) => setMaxRung(e.target.value)} className="h-9 w-20" />
      </div>
      <Button type="submit" disabled={pending}>Issue</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}
