"use client";
import { useState } from "react";
import { Boxes, Plus, Trash2, Target, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Button } from "@/components/ui/button";
import {
  Input, Label, Badge, Card, CardHeader, CardTitle, CardDescription, CardContent,
} from "@/components/ui/primitives";
import {
  useModelArchetypes, useCreateModelArchetype, useDeleteModelArchetype,
} from "@/lib/graphql/hooks";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { cap } from "@/lib/authz/registry";
import { useSession } from "@/lib/session/SessionContext";

/**
 * Model-archetype registry editor (inc16). The governed model BLUEPRINTS a
 * vertical expects — task type, target, expected metrics, governance
 * expectations — declared independent of any trained artifact. Capability packs
 * install these (pack-service inc9); the ml-engineer agent resolves + promotes
 * trained models against them. This page lets a tenant admin browse + author
 * them directly (not only via a pack). Distinct from registered MODELS.
 */
const TASK_TYPES = [
  "binary_classification",
  "pairwise_binary_classification",
  "multiclass_classification",
  "regression",
  "ranking",
  "anomaly_detection",
];

export default function ModelArchetypesPage() {
  const { can } = useCapabilities();
  const canCreate = can(cap("experiment.archetype.create"));
  const canDelete = can(cap("experiment.archetype.delete"));
  const { workspaceId } = useSession();

  const q = useModelArchetypes();
  const archetypes = q.data ?? [];
  const create = useCreateModelArchetype();
  const del = useDeleteModelArchetype();

  const [open, setOpen] = useState(false);
  const empty = { archetypeKey: "", name: "", taskType: "binary_classification", target: "", description: "", governanceNotes: "" };
  const [form, setForm] = useState(empty);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    const archetypeKey = form.archetypeKey.trim();
    const name = form.name.trim();
    if (!archetypeKey || !name || !form.taskType) {
      setErr("Key, name and task type are required.");
      return;
    }
    try {
      await create.mutateAsync({
        workspaceId,
        archetypeKey,
        name,
        taskType: form.taskType,
        target: form.target.trim() || undefined,
        description: form.description.trim() || undefined,
        governanceNotes: form.governanceNotes.trim() || undefined,
      });
      setForm(empty);
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to create archetype.");
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Model archetypes"
        description="Governed model blueprints — the model a vertical expects (task, target, metric gates, governance), independent of any trained artifact. Capability packs install these; the ml-engineer agent promotes models against them."
        actions={
          canCreate ? (
            <Button size="sm" onClick={() => setOpen((v) => !v)}>
              <Plus className="size-4" /> New archetype
            </Button>
          ) : undefined
        }
      />

      <div className="flex flex-wrap gap-2">
        <Link href="/ml"><Button variant="outline" size="sm">Experiments</Button></Link>
        <Link href="/ml/models"><Button variant="outline" size="sm">Models</Button></Link>
      </div>

      {open && canCreate && (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="arch-key">Key</Label>
                <Input
                  id="arch-key"
                  value={form.archetypeKey}
                  placeholder="vendor_fraud_risk_score"
                  onChange={(e) => setForm({ ...form, archetypeKey: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="arch-name">Name</Label>
                <Input
                  id="arch-name"
                  value={form.name}
                  placeholder="Vendor fraud-risk score"
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="arch-task">Task type</Label>
                <select
                  id="arch-task"
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                  value={form.taskType}
                  onChange={(e) => setForm({ ...form, taskType: e.target.value })}
                >
                  {TASK_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="arch-target">Target column</Label>
                <Input
                  id="arch-target"
                  value={form.target}
                  placeholder="vendor_fraud_escalation"
                  onChange={(e) => setForm({ ...form, target: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="arch-desc">Description</Label>
              <Input
                id="arch-desc"
                value={form.description}
                placeholder="Scores a vendor/invoice for shell-vendor and banking-change fraud indicators."
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="arch-gov">Governance notes</Label>
              <Input
                id="arch-gov"
                value={form.governanceNotes}
                placeholder="A high score only HOLDS a payment for human review; four-eyes on every block."
                onChange={(e) => setForm({ ...form, governanceNotes: e.target.value })}
              />
            </div>
            {err && <p role="alert" className="text-sm text-destructive">{err}</p>}
            <div className="flex gap-2">
              <Button size="sm" onClick={submit} disabled={create.isPending}>
                {create.isPending ? "Adding…" : "Add archetype"}
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
        isEmpty={archetypes.length === 0}
        emptyTitle="No model archetypes yet"
        emptyHint="Install a capability pack that ships model blueprints, or add an archetype above."
        onRetry={() => q.refetch()}
      >
        <div className="grid gap-4 md:grid-cols-2">
          {archetypes.map((a) => (
            <Card key={a.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="flex items-center gap-2">
                      <Boxes className="size-4 shrink-0 text-muted-foreground" />
                      <span className="truncate">{a.name}</span>
                    </CardTitle>
                    <CardDescription>
                      <code className="text-xs">{a.archetypeKey}</code>
                    </CardDescription>
                  </div>
                  {canDelete && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${a.name}`}
                      onClick={() => del.mutate({ archetypeKey: a.archetypeKey, workspaceId: a.workspaceId })}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="secondary">{a.taskType}</Badge>
                  {a.target && (
                    <Badge variant="outline" className="gap-1">
                      <Target className="size-3" /> {a.target}
                    </Badge>
                  )}
                </div>
                {a.description && <p className="text-sm text-muted-foreground">{a.description}</p>}
                {a.expectedMetrics && Object.keys(a.expectedMetrics).length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Expected metrics
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(a.expectedMetrics).map(([k, v]) => (
                        <Badge key={k} variant="outline">
                          {k}<span className="ml-1 opacity-60">{String(v)}</span>
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {a.governanceNotes && (
                  <div className="flex gap-1.5 rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
                    <ShieldCheck className="size-3.5 shrink-0" />
                    <span>{a.governanceNotes}</span>
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
