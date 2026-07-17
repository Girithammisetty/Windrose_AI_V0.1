"use client";
import { use, useMemo, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Can } from "@/components/authz/Can";
import { Card, CardContent, CardHeader, CardTitle, Badge, Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { useSession } from "@/lib/session/SessionContext";
import {
  useModel, usePromoteModelVersion, usePromotions, useDecidePromotion,
  // Tier 4b: ml ops — model cards.
  useModelCard, useUpdateModelCard,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { formatLocal } from "@/lib/utils";
import type { ModelVersion, Promotion } from "@/lib/graphql/types";

const STAGE_VARIANT: Record<string, "success" | "warning" | "secondary"> = {
  production: "success",
  staging: "warning",
  archived: "secondary",
  none: "secondary",
};

function StageBadge({ stage }: { stage?: string | null }) {
  if (!stage) return <span className="text-muted-foreground">—</span>;
  return <Badge variant={STAGE_VARIANT[stage] ?? "secondary"}>{stage}</Badge>;
}

const PROMOTION_STATUS_VARIANT: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  pending: "warning",
  approved: "success",
  rejected: "destructive",
  expired: "secondary",
  cancelled: "secondary",
};

/** Mirrors experiment-service's real STAGE transition table
 * (app/domain/state.py _STAGE_TRANSITIONS) — none/staging/production/archived
 * with archived reachable ONLY via a fresh staging round (reinstate). The
 * picker never offers a stage the backend would 409/422 reject. */
export const STAGE_TRANSITIONS: Record<string, string[]> = {
  none: ["staging", "archived"],
  staging: ["production", "archived"],
  production: ["archived"],
  archived: ["staging"],
};

export default function ModelDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useModel(id);
  const model = query.data?.model;
  const versions = useMemo(() => model?.versions ?? [], [model]);
  const production = useMemo(() => versions.find((v) => v.stage === "production"), [versions]);
  const [promoteFor, setPromoteFor] = useState<ModelVersion | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<ModelVersion | null>(null);
  // Tier 4b: ml ops — the version whose model card is open below the table.
  const [cardFor, setCardFor] = useState<ModelVersion | null>(null);

  const columns: Column<ModelVersion>[] = [
    { id: "version", header: "Version", width: 90, cell: (v) => <span className="font-mono">v{v.version}</span> },
    { id: "stage", header: "Stage", width: 120, cell: (v) => <StageBadge stage={v.stage} /> },
    {
      id: "run", header: "Source run", width: "1.5fr",
      cell: (v) =>
        v.sourceRunId ? (
          <Link href={`/ml/runs/${v.sourceRunId}`} className="truncate font-mono text-xs text-primary hover:underline">
            {v.sourceRunId}
          </Link>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    { id: "flavor", header: "Flavor", width: 120, cell: (v) => v.flavor ?? "—" },
    { id: "updated", header: "Stage updated", width: 170, cell: (v) => formatLocal(v.stageUpdatedAt) },
    {
      id: "actions", header: "", width: 240,
      cell: (v) => (
        <div className="flex items-center gap-1">
          {/* Every stage has at least one real transition (see STAGE_TRANSITIONS,
             mirrors experiment-service's own table) — production can still move
             to archived, so promote/transition is never hidden by stage. */}
          <Can gate={FEATURE_GATES.promoteModel}>
            <Button size="sm" variant="outline" onClick={() => setPromoteFor(v)}>
              Change stage
            </Button>
          </Can>
          <Button size="sm" variant="ghost" onClick={() => setSelectedVersion(v)}>
            Promotions
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setCardFor(v)}>
            Card
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && !model}
        emptyTitle="Model not found"
        onRetry={() => query.refetch()}
      >
        {model && (
          <>
            <PageHeader
              title={model.name ?? model.id}
              description={model.description ?? model.urn}
              actions={model.modelType ? <Badge variant="secondary">{model.modelType}</Badge> : undefined}
            />

            <Card className="mb-4 border-[hsl(var(--success))]/40">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <CheckCircle2 className="size-4 text-[hsl(var(--success))]" />
                  Promoted (production) version
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                {production ? (
                  <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
                    <span className="font-mono font-medium">v{production.version}</span>
                    {production.sourceRunId && (
                      <Link href={`/ml/runs/${production.sourceRunId}`} className="text-primary hover:underline">
                        source run
                      </Link>
                    )}
                    {production.mlflowModelRef && (
                      <span className="font-mono text-xs text-muted-foreground">{production.mlflowModelRef}</span>
                    )}
                    <span className="text-xs text-muted-foreground">
                      promoted {formatLocal(production.stageUpdatedAt)}
                    </span>
                  </div>
                ) : (
                  <p className="text-muted-foreground">
                    No version is in production. Promotion is a four-eyes action: a request opens a pending
                    promotion that a second person must approve.
                  </p>
                )}
              </CardContent>
            </Card>

            <h2 className="mb-2 text-sm font-semibold">Versions</h2>
            <DataTable
              ariaLabel="Model versions"
              rows={versions}
              columns={columns}
              rowId={(v) => String(v.version)}
              onRowActivate={(v) => setSelectedVersion(v)}
              emptyState={
                <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
                  <p>This model has no versions.</p>
                </div>
              }
            />

            {selectedVersion && (
              <div className="mt-4">
                <PromotionsPanel modelId={id} version={selectedVersion} onClose={() => setSelectedVersion(null)} />
              </div>
            )}

            {cardFor && (
              <div className="mt-4">
                <ModelCardPanel modelId={id} version={cardFor} onClose={() => setCardFor(null)} />
              </div>
            )}
          </>
        )}
      </AsyncBoundary>

      <PromoteDialog modelId={id} version={promoteFor} onClose={() => setPromoteFor(null)} />
    </div>
  );
}

/**
 * The four-eyes approval queue for one model version (ART-... n/a; experiment-
 * service promotions). experiment-service has no cross-model "all pending
 * promotions" endpoint — this is genuinely per-version, matching the real
 * backend capability (GET /models/{id}/versions/{v}/promotions).
 */
export function PromotionsPanel({
  modelId,
  version,
  onClose,
}: {
  modelId: string;
  version: ModelVersion;
  onClose: () => void;
}) {
  const query = usePromotions(modelId, version.version);
  const rows = query.data?.nodes ?? [];
  const pending = rows.filter((p) => p.status === "pending");

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">
          Promotions for v{version.version}
          {pending.length > 0 && <Badge variant="warning" className="ml-2">{pending.length} pending</Badge>}
        </CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle="No promotions requested for this version yet."
          onRetry={() => query.refetch()}
        >
          <ul className="space-y-3">
            {rows.map((p) => (
              <li key={p.id} className="rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-muted-foreground">{p.fromStage ?? "?"}</span>
                  <span>→</span>
                  <span className="font-medium">{p.targetStage}</span>
                  <Badge variant={PROMOTION_STATUS_VARIANT[p.status ?? ""] ?? "secondary"}>{p.status}</Badge>
                  <span className="ml-auto text-xs text-muted-foreground">{formatLocal(p.createdAt)}</span>
                </div>
                {p.rationale && <p className="mt-1 text-muted-foreground">{p.rationale}</p>}
                <p className="mt-1 text-xs text-muted-foreground">requested by {p.requestedBy ?? "unknown"}</p>
                {p.status === "pending" && (
                  <PromotionDecisionActions modelId={modelId} version={version.version} promotion={p} />
                )}
              </li>
            ))}
          </ul>
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}

/**
 * Approve/reject one pending promotion. Gated on experiment.promotion.approve
 * AND client-side hidden when the viewer is the promotion's own requester —
 * the service forbids self-approval regardless (four-eyes); this only avoids
 * a guaranteed-403 click (same pattern as semantic ReviewActions).
 */
export function PromotionDecisionActions({
  modelId,
  version,
  promotion,
}: {
  modelId: string;
  version: number;
  promotion: Promotion;
}) {
  const { userId } = useSession();
  const { can } = useCapabilities();
  const isRequester = !!promotion.requestedBy && promotion.requestedBy === userId;
  const decide = useDecidePromotion(modelId, version);
  const [rejecting, setRejecting] = useState(false);
  const [message, setMessage] = useState("");
  const error = decide.error instanceof GraphQLRequestError ? decide.error : null;

  if (isRequester && can(FEATURE_GATES.decidePromotion)) {
    return (
      <p className="mt-2 text-xs text-muted-foreground" role="status">
        You requested this promotion — a different approver must decide it (four-eyes).
      </p>
    );
  }

  return (
    <Can gate={FEATURE_GATES.decidePromotion}>
      {!isRequester && (
        <div className="mt-2 space-y-2">
          {!rejecting ? (
            <div className="flex gap-2">
              <Button
                size="sm"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ promotionId: promotion.id, decision: "approve" })}
              >
                {decide.isPending ? <Loader2 className="size-3 animate-spin" /> : null} Approve
              </Button>
              <Button size="sm" variant="outline" onClick={() => setRejecting(true)}>Reject</Button>
            </div>
          ) : (
            <div className="space-y-2">
              <Input
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Reason for rejecting (optional)"
                aria-label="Rejection message"
                className="h-8 text-xs"
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={decide.isPending}
                  onClick={() =>
                    decide.mutate(
                      { promotionId: promotion.id, decision: "reject", message: message.trim() || undefined },
                      { onSuccess: () => setRejecting(false) },
                    )
                  }
                >
                  Confirm reject
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>Cancel</Button>
              </div>
            </div>
          )}
          {error && <p role="alert" className="text-xs text-destructive">{error.message}</p>}
        </div>
      )}
    </Can>
  );
}

export function PromoteDialog({
  modelId,
  version,
  onClose,
}: {
  modelId: string;
  version: ModelVersion | null;
  onClose: () => void;
}) {
  const promote = usePromoteModelVersion();
  const [rationale, setRationale] = useState("");
  const validTargets = STAGE_TRANSITIONS[version?.stage ?? "none"] ?? [];
  const [targetStage, setTargetStage] = useState(validTargets[0] ?? "");
  const error = promote.error instanceof GraphQLRequestError ? promote.error : null;
  const done = promote.isSuccess ? promote.data : null;

  if (!version) return null;

  const close = () => {
    promote.reset();
    setRationale("");
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={close}>
      <div
        key={`${version.modelId}@${version.version}`}
        className="w-full max-w-md rounded-lg border bg-card p-5 shadow-lg"
        role="dialog"
        aria-label="Change model version stage"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold">Change stage for v{version.version}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Currently <span className="font-mono">{version.stage ?? "none"}</span>. This opens a pending promotion.
          A different user must approve it (four-eyes); you cannot approve your own request.
        </p>

        {done ? (
          <div className="mt-4 space-y-2 text-sm" data-testid="promote-result">
            <p className="font-medium text-[hsl(var(--success))]">Promotion requested.</p>
            <p className="text-muted-foreground">
              Status: <span className="font-mono">{done.status}</span> · promotion{" "}
              <span className="font-mono">{done.promotionId}</span>
            </p>
            <div className="flex justify-end pt-2">
              <Button onClick={close}>Done</Button>
            </div>
          </div>
        ) : validTargets.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">No valid stage transition from here.</p>
        ) : (
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              promote.mutate({ modelId, version: version.version, targetStage, rationale: rationale.trim() || undefined });
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="target-stage">Target stage</Label>
              <select
                id="target-stage"
                value={targetStage}
                onChange={(e) => setTargetStage(e.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
              >
                {validTargets.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rationale">Rationale (optional)</Label>
              <Input id="rationale" value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="Beats current production on F1" />
            </div>
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}
                {error.traceId ? ` (trace: ${error.traceId})` : ""}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={close}>
                Cancel
              </Button>
              <Button type="submit" disabled={promote.isPending}>
                {promote.isPending ? "Requesting…" : "Request promotion"}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

/* ==== Tier 4b: ml ops — model card panel ==================================== */

/** The 4 human-editable overlay fields (experiment-service card overlay). */
const OVERLAY_FIELDS = [
  { key: "intended_use", input: "intendedUse", label: "Intended use" },
  { key: "limitations", input: "limitations", label: "Limitations" },
  { key: "evaluation_summary", input: "evaluationSummary", label: "Evaluation summary" },
  { key: "ethical_considerations", input: "ethicalConsiderations", label: "Ethical considerations" },
] as const;

function fieldCount(schema: unknown): number | null {
  if (!schema || typeof schema !== "object") return null;
  const s = schema as Record<string, unknown>;
  // Common shapes: {columns: [...]}, {fields: [...]}, or a flat {name: type} map.
  if (Array.isArray(s.columns)) return s.columns.length;
  if (Array.isArray(s.fields)) return s.fields.length;
  return Object.keys(s).length;
}

/**
 * The MERGED model card for one version (experiment-service GET .../card):
 * service-owned auto fields read-only + the 4 human overlay fields editable
 * behind an Edit toggle (gated experiment.model_card.update). Saving re-renders
 * from the mutation's returned merged card.
 */
export function ModelCardPanel({
  modelId,
  version,
  onClose,
}: {
  modelId: string;
  version: ModelVersion;
  onClose: () => void;
}) {
  const query = useModelCard(modelId, version.version);
  const update = useUpdateModelCard();
  const card = (query.data ?? null) as Record<string, unknown> | null;
  const overlay = (card?.overlay ?? {}) as Record<string, string | null | undefined>;

  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const error = update.error instanceof GraphQLRequestError ? update.error : (update.error as Error | null);

  const startEdit = () => {
    setDrafts(Object.fromEntries(OVERLAY_FIELDS.map((f) => [f.input, overlay[f.key] ?? ""])));
    update.reset();
    setEditing(true);
  };

  const save = () => {
    // Send ONLY the fields that changed (the service PATCH is exclude_unset).
    const input: Record<string, string> = {};
    for (const f of OVERLAY_FIELDS) {
      const next = (drafts[f.input] ?? "").trim();
      const current = (overlay[f.key] ?? "").trim();
      if (next !== current && next !== "") input[f.input] = next;
    }
    if (Object.keys(input).length === 0) {
      setEditing(false);
      return;
    }
    update.mutate(
      { modelId, version: version.version, input },
      { onSuccess: () => setEditing(false) },
    );
  };

  const metrics = (card?.final_metrics ?? null) as Record<string, number> | null;
  const promotionCount = Array.isArray(card?.promotion_history) ? (card!.promotion_history as unknown[]).length : 0;
  const inputCols = fieldCount(card?.input_schema);
  const outputCols = fieldCount(card?.output_schema);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Model card — v{version.version}</CardTitle>
        <div className="flex items-center gap-1">
          {card && !editing && (
            <Can gate={FEATURE_GATES.updateModelCard}>
              <Button variant="outline" size="sm" onClick={startEdit}>
                Edit
              </Button>
            </Can>
          )}
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={!query.isLoading && !card}
          emptyTitle="No model card exists for this version."
          onRetry={() => query.refetch()}
        >
          {card && (
            <div className="space-y-4 text-sm">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-3">
                <div>
                  <dt className="text-xs text-muted-foreground">Algorithm</dt>
                  <dd className="font-medium">{(card.algorithm as string) ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Stage</dt>
                  <dd>
                    <StageBadge stage={card.stage as string | undefined} />
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Flavor</dt>
                  <dd className="font-mono text-xs">{(card.flavor as string) ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Input schema</dt>
                  <dd>{inputCols != null ? `${inputCols} column${inputCols === 1 ? "" : "s"}` : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Output schema</dt>
                  <dd>{outputCols != null ? `${outputCols} column${outputCols === 1 ? "" : "s"}` : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Promotions</dt>
                  <dd>{promotionCount}</dd>
                </div>
              </dl>

              {card.training_data_unavailable === true && (
                <p role="alert" className="rounded-md border border-[hsl(var(--warning))]/50 bg-[hsl(var(--warning))]/10 p-2 text-xs">
                  Training data unavailable: an input dataset of this model has since been deleted.
                </p>
              )}

              {metrics && Object.keys(metrics).length > 0 && (
                <div>
                  <h3 className="mb-1 text-xs font-semibold text-muted-foreground">Final metrics</h3>
                  <dl className="grid grid-cols-2 gap-x-6 gap-y-1 md:grid-cols-4">
                    {Object.entries(metrics).map(([k, v]) => (
                      <div key={k}>
                        <dt className="font-mono text-xs text-muted-foreground">{k}</dt>
                        <dd className="font-medium tabular-nums">{v}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              )}

              <div className="space-y-3">
                <h3 className="text-xs font-semibold text-muted-foreground">Documentation (human overlay)</h3>
                {editing ? (
                  <>
                    {OVERLAY_FIELDS.map((f) => (
                      <div key={f.key} className="space-y-1.5">
                        <Label htmlFor={`card-${f.key}`}>{f.label}</Label>
                        <Textarea
                          id={`card-${f.key}`}
                          rows={2}
                          value={drafts[f.input] ?? ""}
                          onChange={(e) => setDrafts((prev) => ({ ...prev, [f.input]: e.target.value }))}
                        />
                      </div>
                    ))}
                    {error && (
                      <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                        {error.message}
                      </p>
                    )}
                    <div className="flex justify-end gap-2">
                      <Button variant="outline" size="sm" onClick={() => setEditing(false)}>
                        Cancel
                      </Button>
                      <Button size="sm" disabled={update.isPending} onClick={save}>
                        {update.isPending ? "Saving…" : "Save card"}
                      </Button>
                    </div>
                  </>
                ) : (
                  OVERLAY_FIELDS.map((f) => (
                    <div key={f.key}>
                      <h4 className="text-xs text-muted-foreground">{f.label}</h4>
                      <p className={overlay[f.key] ? "" : "text-muted-foreground"}>
                        {overlay[f.key] || "Not documented yet."}
                      </p>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}
