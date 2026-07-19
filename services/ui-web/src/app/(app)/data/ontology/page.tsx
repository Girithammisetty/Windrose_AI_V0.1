"use client";
import { useState } from "react";
import { Network, Plus, Trash2, ArrowRight } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Button } from "@/components/ui/button";
import {
  Input, Label, Badge, Card, CardHeader, CardTitle, CardDescription, CardContent,
} from "@/components/ui/primitives";
import {
  useOntologyEntities, useCreateOntologyEntity, useDeleteOntologyEntity,
} from "@/lib/graphql/hooks";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { cap } from "@/lib/authz/registry";
import { useSession } from "@/lib/session/SessionContext";

/**
 * Domain ontology registry (inc11). Read view of the governed entity-TYPE
 * model — the types a vertical operates on (Vendor, Invoice, PaymentRun, ...)
 * with their attributes and typed relationships to one another. Capability
 * packs install these; agents reason over the graph. Distinct from the flat
 * dataset-derived semantic entities and from entity RESOLUTION (instances).
 */
export default function OntologyPage() {
  const { can } = useCapabilities();
  const canCreate = can(cap("dataset.ontology.create"));
  const canDelete = can(cap("dataset.ontology.delete"));
  const { workspaceId } = useSession();

  const q = useOntologyEntities();
  const entities = q.data ?? [];
  const create = useCreateOntologyEntity();
  const del = useDeleteOntologyEntity();

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ entityKey: "", name: "", description: "" });
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    const entityKey = form.entityKey.trim();
    const name = form.name.trim();
    if (!entityKey || !name) {
      setErr("Key and name are required.");
      return;
    }
    try {
      await create.mutateAsync({ workspaceId, entityKey, name, description: form.description.trim() });
      setForm({ entityKey: "", name: "", description: "" });
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to create entity type.");
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Ontology"
        description="The governed domain model — entity types with their attributes and typed relationships. Capability packs install these; agents reason over them."
        actions={
          canCreate ? (
            <Button size="sm" onClick={() => setOpen((v) => !v)}>
              <Plus className="size-4" /> New entity type
            </Button>
          ) : undefined
        }
      />

      {open && canCreate && (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="onto-key">Key</Label>
                <Input
                  id="onto-key"
                  value={form.entityKey}
                  placeholder="vendor"
                  onChange={(e) => setForm({ ...form, entityKey: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="onto-name">Name</Label>
                <Input
                  id="onto-name"
                  value={form.name}
                  placeholder="Vendor"
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="onto-desc">Description</Label>
              <Input
                id="onto-desc"
                value={form.description}
                placeholder="A supplier the organization pays."
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>
            {err && <p role="alert" className="text-sm text-destructive">{err}</p>}
            <div className="flex gap-2">
              <Button size="sm" onClick={submit} disabled={create.isPending}>
                {create.isPending ? "Adding…" : "Add entity type"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => { setOpen(false); setErr(null); }}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <AsyncBoundary
        isLoading={q.isLoading}
        isError={q.isError}
        error={q.error}
        isEmpty={entities.length === 0}
        emptyTitle="No entity types yet"
        emptyHint="Install a capability pack that ships an ontology, or add an entity type above."
        onRetry={() => q.refetch()}
      >
        <div className="grid gap-4 md:grid-cols-2">
          {entities.map((e) => (
            <Card key={e.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="flex items-center gap-2">
                      <Network className="size-4 shrink-0 text-muted-foreground" />
                      <span className="truncate">{e.name}</span>
                    </CardTitle>
                    <CardDescription>
                      <code className="text-xs">{e.entityKey}</code>
                    </CardDescription>
                  </div>
                  {canDelete && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${e.name}`}
                      onClick={() => del.mutate({ entityKey: e.entityKey, workspaceId: e.workspaceId })}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {e.description && <p className="text-sm text-muted-foreground">{e.description}</p>}
                {e.attributes.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Attributes
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {e.attributes.map((a) => (
                        <Badge key={a.name} variant="outline">
                          {a.name}
                          {a.dataType && <span className="ml-1 opacity-60">{a.dataType}</span>}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {e.relationships.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Relationships
                    </p>
                    <ul className="space-y-1 text-sm">
                      {e.relationships.map((r) => (
                        <li key={r.name} className="flex items-center gap-1.5">
                          <span className="font-medium">{r.name}</span>
                          <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                          <code className="text-xs">{r.target}</code>
                          {r.cardinality && (
                            <Badge variant="secondary" className="text-[10px]">{r.cardinality}</Badge>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </AsyncBoundary>
    </div>
  );
}
