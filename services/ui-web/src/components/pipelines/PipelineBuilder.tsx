"use client";
import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, XCircle, Loader2, Play } from "lucide-react";
import type { PipelineDefinition, PipelineStepType, PipelineTemplate } from "@/lib/graphql/types";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { StepPalette } from "./StepPalette";
import { PipelineCanvas } from "./PipelineCanvas";
import { NodeConfigPanel } from "./NodeConfigPanel";
import { useCanvasStore, nodeFromStep, serializeDefinition, aliasMap, collectRunParameters, hydrateFromDefinition } from "@/lib/pipelines/canvas";
import { PIPELINE_TYPES } from "@/lib/pipelines/form";
import { useCreatePipeline, useUpdatePipeline, useValidatePipeline, useRunPipeline } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { t } from "@/lib/i18n/messages";

/**
 * The no-code DAG builder: palette + canvas + per-step config, with a toolbar to
 * name/type the pipeline, live-validate it against the bff, save it, and run it.
 */
export function PipelineBuilder({
  steps,
  editTemplate,
  onSaved,
}: {
  steps: PipelineStepType[];
  /** When set, the builder opens in edit mode: the canvas is rehydrated from this
   * template's saved definition and Save becomes an Update (new version). */
  editTemplate?: PipelineTemplate | null;
  onSaved?: (tpl: PipelineTemplate) => void;
}) {
  const nodes = useCanvasStore((s) => s.nodes);
  const edges = useCanvasStore((s) => s.edges);
  const addNode = useCanvasStore((s) => s.addNode);
  const setIssues = useCanvasStore((s) => s.setIssues);
  const load = useCanvasStore((s) => s.load);
  const reset = useCanvasStore((s) => s.reset);

  const isEdit = !!editTemplate;
  const [name, setName] = useState(editTemplate?.name ?? "");
  const [pipelineType, setPipelineType] = useState<string>(editTemplate?.pipelineType ?? PIPELINE_TYPES[0]);
  const [banner, setBanner] = useState<string | null>(null);
  const [summary, setSummary] = useState<string[]>([]);
  // In edit mode the template is already saved, so Run is available immediately; any
  // edit clears it (the definition may have changed) until the next Update.
  const [saved, setSaved] = useState<PipelineTemplate | null>(editTemplate ?? null);

  const validateMutation = useValidatePipeline();
  const createMutation = useCreatePipeline();
  const updateMutation = useUpdatePipeline();
  const runMutation = useRunPipeline();

  // Fresh canvas per builder mount — rehydrated from the saved definition in edit
  // mode, empty otherwise.
  useEffect(() => {
    const def = editTemplate?.definition;
    if (def && typeof def === "object") {
      const { nodes: hydNodes, edges: hydEdges } = hydrateFromDefinition(def as PipelineDefinition, steps);
      load(hydNodes, hydEdges);
    } else {
      reset();
    }
    return () => reset();
  }, [editTemplate, steps, load, reset]);

  const stepByName = useMemo(() => new Map(steps.map((s) => [s.name, s])), [steps]);

  // Space newly-added nodes so consecutive cards never overlap: the x step must
  // exceed the node width (NODE_W = 190) or a node's drag-handle header covers
  // the previous node's output port (blocking clicks + drag-to-connect).
  const placeAt = () => ({ x: 60 + (nodes.length % 5) * 220, y: 60 + (nodes.length % 6) * 120 });

  const addStep = (step: PipelineStepType, at?: { x: number; y: number }) => {
    addNode(nodeFromStep(step, at ?? placeAt()));
    setSaved(null); // definition changed → previous save is stale
  };

  const onDropEntry = (token: string, at: { x: number; y: number }) => {
    const step = stepByName.get(token);
    if (step) addStep(step, at);
  };

  /** Map bff/param issues (keyed by alias) back onto node ids for badges. */
  const applyIssues = (
    paramErrors: Record<string, Record<string, string>>,
    bffIssues: { code: string; message: string; node?: string | null }[],
  ) => {
    const aliases = aliasMap(nodes); // nodeId -> alias
    const aliasToId = new Map([...aliases.entries()].map(([id, a]) => [a, id]));
    const byNode: Record<string, string[]> = {};
    const push = (id: string, msg: string) => {
      (byNode[id] ??= []).push(msg);
    };
    for (const [id, errs] of Object.entries(paramErrors)) {
      for (const [pname, msg] of Object.entries(errs)) push(id, `${pname}: ${msg}`);
    }
    const general: string[] = [];
    for (const iss of bffIssues) {
      const id = iss.node ? aliasToId.get(iss.node) : undefined;
      if (id) push(id, iss.message);
      else general.push(iss.message);
    }
    setIssues(byNode);
    return general;
  };

  const runValidate = () => {
    setBanner(null);
    const { definition, paramErrors, ok } = serializeDefinition(nodes, edges);
    if (!ok) {
      const general = applyIssues(paramErrors, []);
      setSummary([t("pipelines.fixParams"), ...general]);
      setBanner(t("pipelines.invalid"));
      return;
    }
    validateMutation.mutate(
      { definition, pipelineType },
      {
        onSuccess: (res) => {
          applyIssues({}, res.issues);
          setSummary(res.issues.map((i) => (i.node ? `${i.node}: ${i.message}` : i.message)));
          setBanner(res.valid ? t("pipelines.valid") : t("pipelines.invalid"));
        },
      },
    );
  };

  const runSave = () => {
    setBanner(null);
    if (!name.trim()) {
      setBanner(t("pipelines.nameRequired"));
      return;
    }
    if (nodes.length === 0) {
      setBanner(t("pipelines.addStepFirst"));
      return;
    }
    const { definition, paramErrors, ok } = serializeDefinition(nodes, edges);
    if (!ok) {
      const general = applyIssues(paramErrors, []);
      setSummary([t("pipelines.fixParams"), ...general]);
      setBanner(t("pipelines.invalid"));
      return;
    }
    if (isEdit && editTemplate) {
      updateMutation.mutate(
        { id: editTemplate.id, input: { name: name.trim(), definition } },
        {
          onSuccess: (r) => {
            setSaved(r.updatePipeline);
            setIssues({});
            setSummary([]);
            setBanner(t("pipelines.updated"));
            onSaved?.(r.updatePipeline);
          },
        },
      );
      return;
    }
    createMutation.mutate(
      { name: name.trim(), pipelineType, definition },
      {
        onSuccess: (r) => {
          setSaved(r.createPipeline);
          setIssues({});
          setSummary([]);
          setBanner(t("pipelines.saved"));
          onSaved?.(r.createPipeline);
        },
      },
    );
  };

  const runRun = () => {
    if (!saved) return;
    setBanner(null);
    // Thread the picked dataset_ref values through as run parameters; omit when
    // none so the run falls back to the template version's baked-in parameters.
    const parameters = collectRunParameters(nodes);
    runMutation.mutate(
      { id: saved.id, parameters: Object.keys(parameters).length > 0 ? parameters : undefined },
      { onSuccess: (r) => setBanner(t("pipelines.runStarted", { status: String(r.runPipeline.status ?? "QUEUED") })) },
    );
  };

  const saveMutationError = isEdit ? updateMutation.error : createMutation.error;
  const saveError = saveMutationError instanceof GraphQLRequestError ? saveMutationError : null;
  const savePending = isEdit ? updateMutation.isPending : createMutation.isPending;
  const validateError = validateMutation.error instanceof GraphQLRequestError ? validateMutation.error : null;
  const runError = runMutation.error instanceof GraphQLRequestError ? runMutation.error : null;
  const canSave = !!name.trim() && nodes.length > 0;

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-end gap-3 rounded-lg border p-3">
        <div className="space-y-1.5">
          <Label htmlFor="pipeline-name">{t("pipelines.name")}</Label>
          <Input
            id="pipeline-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setSaved(null);
            }}
            placeholder={t("pipelines.namePlaceholder")}
            className="w-56"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pipeline-type">{t("pipelines.type")}</Label>
          <select
            id="pipeline-type"
            value={pipelineType}
            onChange={(e) => {
              setPipelineType(e.target.value);
              setSaved(null);
            }}
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            {PIPELINE_TYPES.map((pt) => (
              <option key={pt} value={pt}>
                {pt}
              </option>
            ))}
          </select>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" onClick={runValidate} disabled={nodes.length === 0 || validateMutation.isPending}>
            {validateMutation.isPending ? (
              <>
                <Loader2 className="animate-spin" /> {t("pipelines.validating")}
              </>
            ) : (
              t("pipelines.validate")
            )}
          </Button>
          <Button onClick={runSave} disabled={!canSave || savePending}>
            {isEdit
              ? savePending
                ? t("pipelines.updating")
                : t("pipelines.update")
              : savePending
                ? t("pipelines.saving")
                : t("pipelines.save")}
          </Button>
          {saved && (
            <Button variant="secondary" onClick={runRun} disabled={runMutation.isPending}>
              {runMutation.isPending ? (
                <>
                  <Loader2 className="animate-spin" /> {t("pipelines.running")}
                </>
              ) : (
                <>
                  <Play /> {t("pipelines.run")}
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Status banner + issue summary */}
      {banner && (
        <div
          role="status"
          data-testid="builder-banner"
          className="flex items-center gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm"
        >
          {banner === t("pipelines.valid") || banner === t("pipelines.saved") || banner === t("pipelines.updated") ? (
            <CheckCircle2 className="size-4 text-[hsl(var(--success))]" />
          ) : banner === t("pipelines.invalid") ? (
            <XCircle className="size-4 text-destructive" />
          ) : null}
          {banner}
        </div>
      )}
      {summary.length > 0 && (
        <ul className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {summary.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      )}
      {(saveError || validateError || runError) && (
        <p role="alert" className="text-sm text-destructive" data-testid="mutation-error">
          {(saveError ?? validateError ?? runError)!.message}
        </p>
      )}

      {/* Palette | Canvas | Config */}
      <div className="flex h-[calc(100vh-19rem)] min-h-[420px] overflow-hidden rounded-lg border">
        <StepPalette steps={steps} onAdd={(s) => addStep(s)} />
        <PipelineCanvas onDropEntry={onDropEntry} />
        <NodeConfigPanel />
      </div>
    </div>
  );
}
