"use client";
import { useMemo, useState } from "react";
import { Plus, Server, PauseCircle } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useAiProviders, useCreateAiProvider, useDrainAiProvider } from "@/lib/graphql/hooks";
import type { AiProviderDeployment } from "@/lib/graphql/types";

const STATUS_VARIANT: Record<string, "success" | "warning" | "secondary"> = {
  active: "success",
  draining: "warning",
  disabled: "secondary",
};

export default function AiProvidersPage() {
  const [creating, setCreating] = useState(false);
  const [toDrain, setToDrain] = useState<AiProviderDeployment | null>(null);
  const query = useAiProviders();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const create = useCreateAiProvider();
  const drain = useDrainAiProvider();

  const columns: Column<AiProviderDeployment>[] = [
    { id: "deployment", header: "Deployment", cell: (d) => <span className="font-medium">{d.deploymentName}</span> },
    { id: "provider", header: "Provider", width: 130, cell: (d) => d.provider },
    { id: "family", header: "Model family", width: 150, cell: (d) => d.modelFamily },
    { id: "region", header: "Region", width: 110, cell: (d) => `${d.region} (${d.cloud})` },
    { id: "priority", header: "Priority", width: 80, cell: (d) => d.priority },
    { id: "status", header: "Status", width: 100, cell: (d) => <Badge variant={STATUS_VARIANT[d.status] ?? "success"}>{d.status}</Badge> },
    {
      id: "health", header: "Live health", width: 140,
      cell: (d) => (
        <span className="flex items-center gap-1 text-xs">
          <Badge variant={d.healthy ? "success" : "destructive"}>{d.healthy ? "healthy" : "unhealthy"}</Badge>
          {d.circuitState && <span className="text-muted-foreground">{d.circuitState}</span>}
        </span>
      ),
    },
    {
      id: "actions", header: "", width: 100,
      cell: (d) =>
        d.status === "active" ? (
          <Can gate={FEATURE_GATES.manageAiProviders}>
            <Button size="sm" variant="outline" onClick={() => setToDrain(d)}>
              <PauseCircle className="size-3" /> Drain
            </Button>
          </Can>
        ) : null,
    },
  ];

  const newButton = (
    <Can gate={FEATURE_GATES.manageAiProviders}>
      <Button onClick={() => setCreating((v) => !v)}><Plus /> {creating ? "Cancel" : "New deployment"}</Button>
    </Can>
  );

  return (
    <div>
      <PageHeader title="Provider / deployment catalog" description="LLM provider deployments backing the routing ladders." actions={newButton} />

      {creating && (
        <Card className="mb-4 border-primary/40">
          <CardContent className="pt-4">
            <NewProviderForm pending={create.isPending} error={create.error} onCreate={(input) => create.mutate(input, { onSuccess: () => setCreating(false) })} />
          </CardContent>
        </Card>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No provider deployments configured"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Provider deployments"
          rows={rows}
          columns={columns}
          rowId={(d) => d.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Server className="size-8" />
              <p>No deployments</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ConfirmDialog
        open={!!toDrain}
        onOpenChange={(o) => !o && setToDrain(null)}
        title={`Drain ${toDrain?.deploymentName}?`}
        description="Draining stops routing new traffic to this deployment. In-flight requests complete normally."
        confirmLabel="Drain"
        destructive
        onConfirm={() => {
          if (toDrain) drain.mutate({ deploymentId: toDrain.id }, { onSuccess: () => setToDrain(null) });
        }}
      />
    </div>
  );
}

function NewProviderForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: {
    provider: string; modelFamily: string; deploymentName: string; region: string; cloud: string; endpointVaultRef: string;
  }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [provider, setProvider] = useState("anthropic");
  const [modelFamily, setModelFamily] = useState("");
  const [deploymentName, setDeploymentName] = useState("");
  const [region, setRegion] = useState("");
  const [cloud, setCloud] = useState("aws");
  const [endpointVaultRef, setEndpointVaultRef] = useState("");

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (modelFamily.trim() && deploymentName.trim() && region.trim() && endpointVaultRef.trim()) {
          onCreate({ provider, modelFamily: modelFamily.trim(), deploymentName: deploymentName.trim(), region: region.trim(), cloud, endpointVaultRef: endpointVaultRef.trim() });
        }
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="p-provider">Provider</Label>
        <select id="p-provider" value={provider} onChange={(e) => setProvider(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
          <option value="ollama">ollama</option>
          <option value="openai">openai</option>
          <option value="azure_openai">azure_openai</option>
          <option value="anthropic">anthropic</option>
          <option value="bedrock">bedrock</option>
          <option value="vertex">vertex</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="p-family">Model family</Label>
        <Input id="p-family" value={modelFamily} onChange={(e) => setModelFamily(e.target.value)} placeholder="claude-judge" className="h-9 w-40" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="p-name">Deployment name</Label>
        <Input id="p-name" value={deploymentName} onChange={(e) => setDeploymentName(e.target.value)} className="h-9 w-40" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="p-region">Region</Label>
        <Input id="p-region" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="us-east-1" className="h-9 w-32" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="p-cloud">Cloud</Label>
        <select id="p-cloud" value={cloud} onChange={(e) => setCloud(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
          <option value="aws">aws</option>
          <option value="azure">azure</option>
          <option value="gcp">gcp</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="p-vault">Endpoint / credential ref</Label>
        <Input id="p-vault" value={endpointVaultRef} onChange={(e) => setEndpointVaultRef(e.target.value)} placeholder="http://host:11434/v1 or secret/ai/<provider>/<name>" className="h-9 w-72" />
      </div>
      <Button type="submit" disabled={pending}>Create</Button>
      <p className="w-full text-xs text-muted-foreground">
        Deployment name is the concrete provider-side model id (e.g. qwen2.5:0.5b, claude-opus-4-8, gpt-4o-mini).
        The endpoint/credential ref resolves in the gateway&apos;s own store: a URL is used directly as the base URL
        (self-hosted/local), a <code>secret/…</code> name reads a mounted <code>AIG_SECRET__*</code> secret (cloud key).
        Cost is priced per (provider, model) from the versioned price table; ollama is $0. bedrock/vertex are accepted
        but execution requires cloud-cred wiring not present locally.
      </p>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}
