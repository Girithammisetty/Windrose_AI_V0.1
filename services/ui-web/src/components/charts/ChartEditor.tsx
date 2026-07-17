"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2 } from "lucide-react";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { ChartView } from "./ChartView";
import {
  useChartTypes,
  useSemanticModels,
  useSemanticModel,
  useChartPreview,
  useCreateChart,
  useUpdateChart,
  useSavedQueries,
  useDatasets,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { useSession } from "@/lib/session/SessionContext";
import {
  FRIENDLY_CHART_TYPES,
  ALLOWED_AGG_FNS,
  requiredEncodings,
  validateEncodings,
  buildChartConfig,
  buildDisplayMeta,
  buildSources,
  defaultAgg,
  validateHeatmapEncodings,
  buildHeatmapConfig,
  validateNetworkEncodings,
  buildNetworkConfig,
  buildSavedQuerySource,
  validateMetricSource,
  buildDatasetSource,
  type Encodings,
  type MeasureEncoding,
  type EncodingError,
  type HeatmapEncodings,
  type NetworkEncodings,
} from "@/lib/charts/spec";
import type {
  Chart,
  ChartShapedData,
  ChartType,
  ChartSourceInput,
  CreateChartInput,
  UpdateChartInput,
  JSONValue,
} from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

/* Narrowing helpers for reading a saved chart's persisted spec (JSONValue) back
 * into the editor's typed state — the inverse of spec.ts's serialization. */
type JObj = Record<string, JSONValue>;
const asJObj = (v: JSONValue | undefined): JObj | null =>
  v != null && typeof v === "object" && !Array.isArray(v) ? (v as JObj) : null;
const dimensionOf = (v: JSONValue | undefined): string => {
  const d = asJObj(v)?.dimension;
  return typeof d === "string" ? d : "";
};
const stringOf = (v: JSONValue | undefined): string => (typeof v === "string" ? v : "");

/**
 * The no-code chart editor (mirrors the pipeline builder's flow): pick a chart
 * type → pick a source → once picked, choose encodings → build the
 * config/displayMeta/sources spec → LIVE PREVIEW via chartPreview → Save via
 * createChart. Every option comes from the live bff (no mock data): the type
 * catalog, the models/queries/datasets, and the preview data.
 *
 * The "source" step depends on the picked type's family — chart-service's
 * config shape (and the sources[] it can actually resolve) genuinely differs
 * per family, confirmed against services/chart-service/internal:
 *   axis / y_only / grid  → a semantic MODEL (dimension + measures, unchanged
 *                           from the original 4-type editor; axis additionally
 *                           offers an optional series-split dimension)
 *   heatmap                → a semantic MODEL, but x/y/dataseries are all
 *                           DIMENSIONS (not measures — heatmap has no y-measure)
 *   network                → a SAVED QUERY (network configs have no y-measure,
 *                           so chart-service can only resolve them via
 *                           source_type "saved_query", never a semantic model)
 *   metric (dataClass       → a DATASET (no x/y at all — the artifact IS the
 *     "dataset")               dataset's profile)
 */
export function ChartEditor({
  dashboardId,
  open,
  onOpenChange,
  onSaved,
  editChart,
}: {
  dashboardId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved?: () => void;
  /** When set, the editor opens in EDIT mode: it hydrates from this chart and
   * saves via updateChart instead of createChart. */
  editChart?: Chart | null;
}) {
  const { workspaceId, tenantId } = useSession();

  const chartTypesQuery = useChartTypes();
  const modelsQuery = useSemanticModels(workspaceId);
  const savedQueriesQuery = useSavedQueries();
  const datasetsQuery = useDatasets();
  const savedQueries = useMemo(
    () => savedQueriesQuery.data?.pages.flatMap((p) => p.nodes) ?? [],
    [savedQueriesQuery.data],
  );
  const datasets = useMemo(() => datasetsQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [datasetsQuery.data]);

  // Friendly type catalog: filter the full catalog to our offered subset, in the
  // display order defined by FRIENDLY_CHART_TYPES, grouped by family for the
  // <optgroup>-organized picker.
  const friendly = useMemo(() => {
    const byName = new Map((chartTypesQuery.data ?? []).map((c) => [c.name, c]));
    return FRIENDLY_CHART_TYPES.map((f) => ({ ...f, catalog: byName.get(f.chartType) })).filter(
      (f): f is typeof f & { catalog: ChartType } => !!f.catalog,
    );
  }, [chartTypesQuery.data]);

  const groupedFriendly = useMemo(() => {
    const groups = new Map<string, typeof friendly>();
    for (const f of friendly) {
      const arr = groups.get(f.group) ?? [];
      arr.push(f);
      groups.set(f.group, arr);
    }
    return Array.from(groups.entries());
  }, [friendly]);

  const [name, setName] = useState("");
  const [chartType, setChartType] = useState<string>("");
  // model-based families (axis / y_only / grid / heatmap)
  const [modelName, setModelName] = useState<string>("");
  const [x, setX] = useState<string>(""); // axis/y_only/grid x-dimension, or heatmap x-dimension
  const [measures, setMeasures] = useState<Record<string, string>>({});
  const [dataseries, setDataseries] = useState<string>(""); // axis-only optional series-split dimension
  const [heatY, setHeatY] = useState<string>(""); // heatmap y-dimension
  const [heatSeries, setHeatSeries] = useState<string>(""); // heatmap dataseries-dimension
  // network family
  const [savedQueryId, setSavedQueryId] = useState<string>("");
  const [nodesCol, setNodesCol] = useState<string>("");
  const [childrenCol, setChildrenCol] = useState<string>("");
  const [nodeValuesCol, setNodeValuesCol] = useState<string>("");
  // metric family
  const [datasetId, setDatasetId] = useState<string>("");
  const [banner, setBanner] = useState<string | null>(null);

  const family = friendly.find((f) => f.chartType === chartType)?.catalog.family ?? "axis";
  const usesModel = family === "axis" || family === "y_only" || family === "grid" || family === "heatmap";

  const modelQuery = useSemanticModel(usesModel ? modelName : "");
  const model = usesModel && modelName ? modelQuery.data : null;

  const previewMutation = useChartPreview();
  const createMutation = useCreateChart(dashboardId);
  const updateMutation = useUpdateChart(dashboardId);
  const [preview, setPreview] = useState<ChartShapedData | null>(null);

  // Suppress the family/model "reset encodings" effects while hydrating an
  // existing chart (they'd otherwise wipe the values we just loaded); cleared
  // the moment the user changes the type or model select themselves.
  const hydratingRef = useRef(false);
  // Guards the open/hydrate effect so it applies exactly once per open (it
  // re-runs when the chart-type catalog arrives — see below).
  const hydratedRef = useRef(false);
  // The source urn parsed from a network/metric chart, held so we can resolve
  // it to a saved-query / dataset id once those lists finish loading.
  const hydrateSourceUrnRef = useRef("");

  // On open: hydrate from the edited chart (edit mode) or reset to blank
  // (create mode). Every field is set explicitly so no stale value from a
  // prior session leaks across families. Runs once per open — but for edit it
  // waits for the chart-type catalog (needed to classify the chart's family),
  // re-running when it arrives; hydratedRef makes that idempotent.
  useEffect(() => {
    if (!open) {
      hydratedRef.current = false;
      return;
    }
    if (hydratedRef.current) return;
    if (editChart) {
      if (!chartTypesQuery.data) return; // wait for the catalog to classify family
      hydratedRef.current = true;
      hydratingRef.current = true;
      setBanner(null);
      setPreview(null);
      const cfg = asJObj(editChart.config);
      const dm = asJObj(editChart.displayMeta);
      const srcs = Array.isArray(editChart.sources) ? (editChart.sources as JSONValue[]) : [];
      const srcUrn = stringOf(asJObj(srcs[0])?.source_urn);
      hydrateSourceUrnRef.current = srcUrn;
      const ct = editChart.chartType ?? "";
      const fam = friendly.find((f) => f.chartType === ct)?.catalog.family ?? "axis";

      // axis / y_only / grid measures: config.y = [{ measure, agg_fn }]
      const yRec: Record<string, string> = {};
      const yArr = Array.isArray(cfg?.y) ? (cfg?.y as JSONValue[]) : [];
      for (const m of yArr) {
        const measure = asJObj(m)?.measure;
        if (typeof measure === "string") {
          const agg = asJObj(m)?.agg_fn;
          yRec[measure] = typeof agg === "string" ? agg : "count";
        }
      }

      setName(editChart.name ?? "");
      setChartType(ct);
      setModelName(fam === "network" || fam === "metric" ? "" : stringOf(dm?.semantic_model));
      setX(fam === "network" || fam === "metric" ? "" : dimensionOf(cfg?.x));
      setMeasures(fam === "axis" || fam === "y_only" || fam === "grid" ? yRec : {});
      setDataseries(fam === "axis" ? dimensionOf(cfg?.dataseries) : "");
      setHeatY(fam === "heatmap" ? dimensionOf(cfg?.y) : "");
      setHeatSeries(fam === "heatmap" ? dimensionOf(cfg?.dataseries) : "");
      setSavedQueryId(fam === "network" ? savedQueries.find((q) => q.urn === srcUrn)?.id ?? "" : "");
      setNodesCol(fam === "network" ? stringOf(cfg?.nodes) : "");
      setChildrenCol(fam === "network" ? stringOf(cfg?.children) : "");
      setNodeValuesCol(fam === "network" ? stringOf(cfg?.node_values) : "");
      setDatasetId(fam === "metric" ? datasets.find((d) => d.urn === srcUrn)?.id ?? "" : "");
      return;
    }
    // create mode
    hydratedRef.current = true;
    hydratingRef.current = false;
    hydrateSourceUrnRef.current = "";
    setBanner(null);
    setPreview(null);
    setName("");
    setChartType(friendly[0]?.chartType ?? "");
    setModelName("");
    setX("");
    setMeasures({});
    setDataseries("");
    setHeatY("");
    setHeatSeries("");
    setSavedQueryId("");
    setNodesCol("");
    setChildrenCol("");
    setNodeValuesCol("");
    setDatasetId("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editChart, chartTypesQuery.data]);

  // Network/metric hydration: the saved-query / dataset lists may still be
  // loading when we hydrate, so resolve the persisted source urn to its id
  // once they arrive (only while still hydrating — i.e. untouched by the user).
  useEffect(() => {
    if (!hydratingRef.current) return;
    const urn = hydrateSourceUrnRef.current;
    if (!urn) return;
    if (family === "network" && !savedQueryId) {
      const q = savedQueries.find((sq) => sq.urn === urn);
      if (q) setSavedQueryId(q.id);
    }
    if (family === "metric" && !datasetId) {
      const d = datasets.find((ds) => ds.urn === urn);
      if (d) setDatasetId(d.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedQueries, datasets, family, savedQueryId, datasetId]);

  // Default the type once the catalog arrives.
  useEffect(() => {
    if (!chartType && friendly.length > 0) setChartType(friendly[0].chartType);
  }, [friendly, chartType]);

  // Switching family invalidates every encoding (the shapes are unrelated).
  useEffect(() => {
    if (hydratingRef.current) return;
    setX("");
    setMeasures({});
    setDataseries("");
    setHeatY("");
    setHeatSeries("");
    setSavedQueryId("");
    setNodesCol("");
    setChildrenCol("");
    setNodeValuesCol("");
    setDatasetId("");
    setPreview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [family]);

  // Reset encodings when the model changes.
  useEffect(() => {
    if (hydratingRef.current) return;
    setX("");
    setMeasures({});
    setDataseries("");
    setHeatY("");
    setHeatSeries("");
    setPreview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelName]);

  const encodings: Encodings = useMemo(() => {
    const ordered = (model?.measures ?? [])
      .filter((m) => measures[m.name] != null)
      .map<MeasureEncoding>((m) => ({ measure: m.name, agg: measures[m.name] }));
    return { x: x || undefined, y: ordered, dataseries: family === "axis" ? dataseries || undefined : undefined };
  }, [model, measures, x, dataseries, family]);

  const heatmapEncodings: HeatmapEncodings = useMemo(
    () => ({ x: x || undefined, y: heatY || undefined, dataseries: heatSeries || undefined }),
    [x, heatY, heatSeries],
  );

  const networkEncodings: NetworkEncodings = useMemo(
    () => ({ nodes: nodesCol || undefined, children: childrenCol || undefined, nodeValues: nodeValuesCol || undefined }),
    [nodesCol, childrenCol, nodeValuesCol],
  );

  const selectedQuery = savedQueries.find((q) => q.id === savedQueryId);
  const selectedDataset = datasets.find((d) => d.id === datasetId);

  // Per-family: validation errors + the config/displayMeta/sources to preview.
  const built = useMemo((): {
    errors: EncodingError[];
    canBuild: boolean;
    config: JSONValue | null;
    displayMeta: JSONValue;
    sources: ChartSourceInput[] | null;
  } => {
    if (family === "heatmap") {
      const errors = validateHeatmapEncodings(heatmapEncodings);
      const canBuild = !!model && errors.length === 0;
      return {
        errors,
        canBuild,
        config: canBuild ? buildHeatmapConfig(heatmapEncodings) : null,
        displayMeta: buildDisplayMeta(modelName, workspaceId),
        sources:
          canBuild && model
            ? buildSources(model.measures[0]?.name ?? model.dimensions[0]?.name ?? "dimension", tenantId)
            : null,
      };
    }
    if (family === "network") {
      const errors = [...validateNetworkEncodings(networkEncodings)];
      if (!selectedQuery) errors.push({ field: "source", message: "Pick a saved query." });
      const canBuild = !!selectedQuery && errors.length === 0;
      return {
        errors,
        canBuild,
        config: canBuild ? buildNetworkConfig(networkEncodings) : null,
        // chart-service's preview handler authorizes chart.chart.read against
        // display_meta.workspace_id (there's no persisted resource for an
        // unsaved preview to derive it from) — every family needs this, not
        // just the model-bound ones.
        displayMeta: { workspace_id: workspaceId },
        sources: canBuild && selectedQuery ? buildSavedQuerySource(selectedQuery.urn) : null,
      };
    }
    if (family === "metric") {
      const errors = validateMetricSource(selectedDataset?.urn);
      const canBuild = !!selectedDataset && errors.length === 0;
      return {
        errors,
        canBuild,
        config: canBuild ? {} : null,
        displayMeta: { workspace_id: workspaceId },
        sources: canBuild && selectedDataset ? buildDatasetSource(selectedDataset.urn) : null,
      };
    }
    // axis / y_only / grid
    const errors = validateEncodings(family, encodings);
    const canBuild = !!model && errors.length === 0;
    return {
      errors,
      canBuild,
      config: canBuild ? buildChartConfig(family, encodings) : null,
      displayMeta: buildDisplayMeta(modelName, workspaceId),
      sources: canBuild && model ? buildSources(model.measures[0]?.name ?? encodings.y[0]?.measure ?? "measure", tenantId) : null,
    };
  }, [family, model, modelName, workspaceId, tenantId, encodings, heatmapEncodings, networkEncodings, selectedQuery, selectedDataset]);

  const errors = built.errors;
  const canPreview = !!chartType && built.canBuild;

  const previewInput: CreateChartInput | null =
    canPreview && built.config !== null && built.sources !== null
      ? {
          dashboardId,
          name: name.trim() || "Preview",
          chartType,
          config: built.config,
          displayMeta: built.displayMeta,
          sources: built.sources,
        }
      : null;

  // Live preview: re-resolve whenever the (valid) spec changes.
  const specKey = previewInput ? JSON.stringify(previewInput.config) + chartType + (previewInput.sources?.[0]?.sourceUrn ?? "") : "";
  useEffect(() => {
    if (!previewInput) {
      setPreview(null);
      return;
    }
    previewMutation.mutate(previewInput, { onSuccess: (r) => setPreview(r) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specKey]);

  const toggleMeasure = (measureName: string, agg: string) => {
    setMeasures((prev) => {
      const next = { ...prev };
      if (next[measureName] != null) delete next[measureName];
      else next[measureName] = agg;
      return next;
    });
  };
  const setMeasureAgg = (measureName: string, agg: string) =>
    setMeasures((prev) => ({ ...prev, [measureName]: agg }));

  const runSave = () => {
    setBanner(null);
    if (!name.trim()) {
      setBanner(t("charts.nameRequired"));
      return;
    }
    if (!previewInput) return;
    const onSuccess = () => {
      setBanner(t("charts.saved"));
      onSaved?.();
      onOpenChange(false);
    };
    if (editChart) {
      const input: UpdateChartInput = {
        name: name.trim(),
        chartType,
        config: previewInput.config,
        displayMeta: previewInput.displayMeta,
        sources: previewInput.sources ?? undefined,
      };
      updateMutation.mutate({ id: editChart.id, input }, { onSuccess });
      return;
    }
    const input: CreateChartInput = { ...previewInput, name: name.trim() };
    createMutation.mutate(input, { onSuccess });
  };

  const req = requiredEncodings(family);
  const activeMutation = editChart ? updateMutation : createMutation;
  const previewError =
    previewMutation.error instanceof GraphQLRequestError ? previewMutation.error : null;
  const saveError = activeMutation.error instanceof GraphQLRequestError ? activeMutation.error : null;
  const canSave = !!name.trim() && canPreview && !!previewInput && !activeMutation.isPending;
  const showErrors = errors.length > 0 && (model || family === "network" || family === "metric");

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-full max-w-3xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border bg-card shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <div className="flex items-center justify-between border-b p-4">
            <Dialog.Title className="text-lg font-semibold">
              {editChart ? t("charts.editTitle") : t("charts.editorTitle")}
            </Dialog.Title>
          </div>

          <form
            className="grid gap-4 overflow-y-auto p-4 md:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              runSave();
            }}
            aria-label={editChart ? t("charts.editTitle") : t("charts.editorTitle")}
          >
            {/* Left column: the spec form */}
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="chart-name">{t("charts.name")}</Label>
                <Input
                  id="chart-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("charts.namePlaceholder")}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="chart-type">{t("charts.type")}</Label>
                <select
                  id="chart-type"
                  value={chartType}
                  onChange={(e) => {
                    hydratingRef.current = false;
                    setChartType(e.target.value);
                  }}
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                >
                  {friendly.length === 0 && <option value="">{t("charts.pickType")}</option>}
                  {groupedFriendly.map(([group, items]) => (
                    <optgroup key={group} label={group}>
                      {items.map((f) => (
                        <option key={f.chartType} value={f.chartType}>
                          {f.label}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>

              {/* ---- model-based families: axis / y_only / grid / heatmap ---- */}
              {usesModel && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="chart-model">{t("charts.model")}</Label>
                    <select
                      id="chart-model"
                      value={modelName}
                      onChange={(e) => {
                        hydratingRef.current = false;
                        setModelName(e.target.value);
                      }}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    >
                      <option value="">{t("charts.pickModel")}</option>
                      {(modelsQuery.data ?? []).map((m) => (
                        <option key={m.id} value={m.name}>
                          {m.name}
                        </option>
                      ))}
                    </select>
                    {modelsQuery.data && modelsQuery.data.length === 0 && (
                      <p className="text-xs text-muted-foreground">{t("charts.noModels")}</p>
                    )}
                  </div>

                  {!modelName && <p className="text-xs text-muted-foreground">{t("charts.pickModelFirst")}</p>}

                  {modelName && modelQuery.isLoading && (
                    <p className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="size-3 animate-spin" /> {t("state.loading")}
                    </p>
                  )}

                  {model && family !== "heatmap" && (
                    <>
                      <div className="space-y-1.5">
                        <Label htmlFor="chart-x">
                          {t("charts.xDimension")}
                          {req.x && <span className="ml-0.5 text-destructive">*</span>}
                        </Label>
                        <select
                          id="chart-x"
                          value={x}
                          onChange={(e) => setX(e.target.value)}
                          className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                          aria-invalid={errors.some((er) => er.field === "x")}
                        >
                          <option value="">{t("charts.pickDimension")}</option>
                          {model.dimensions.map((d) => (
                            <option key={d.name} value={d.name}>
                              {d.name}
                              {d.dimType ? ` (${d.dimType})` : ""}
                            </option>
                          ))}
                        </select>
                      </div>

                      <fieldset className="space-y-1.5">
                        <legend className="text-sm font-medium leading-none">
                          {t("charts.yMeasures")}
                          {req.y && <span className="ml-0.5 text-destructive">*</span>}
                        </legend>
                        <div
                          className="space-y-1 rounded-md border p-2"
                          aria-invalid={errors.some((er) => er.field === "y")}
                        >
                          {model.measures.length === 0 && (
                            <p className="text-xs text-muted-foreground">No measures in this model.</p>
                          )}
                          {model.measures.map((m) => {
                            const selected = measures[m.name] != null;
                            const agg = measures[m.name] ?? defaultAgg(m);
                            return (
                              <div key={m.name} className="flex items-center gap-2">
                                <input
                                  id={`measure-${m.name}`}
                                  type="checkbox"
                                  checked={selected}
                                  onChange={() => toggleMeasure(m.name, defaultAgg(m))}
                                  className="size-4 accent-[hsl(var(--primary))]"
                                />
                                <label htmlFor={`measure-${m.name}`} className="flex-1 text-sm">
                                  {m.name}
                                </label>
                                <select
                                  aria-label={`${m.name} ${t("charts.agg")}`}
                                  value={agg}
                                  disabled={!selected}
                                  onChange={(e) => setMeasureAgg(m.name, e.target.value)}
                                  className="h-8 rounded-md border border-input bg-background px-1.5 text-xs disabled:opacity-50"
                                >
                                  {ALLOWED_AGG_FNS.map((a) => (
                                    <option key={a} value={a}>
                                      {a}
                                    </option>
                                  ))}
                                </select>
                              </div>
                            );
                          })}
                        </div>
                      </fieldset>

                      {family === "axis" && (
                        <div className="space-y-1.5">
                          <Label htmlFor="chart-dataseries">{t("charts.seriesDimension")}</Label>
                          <select
                            id="chart-dataseries"
                            value={dataseries}
                            onChange={(e) => setDataseries(e.target.value)}
                            className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                          >
                            <option value="">{t("charts.pickSeriesDimension")}</option>
                            {model.dimensions.map((d) => (
                              <option key={d.name} value={d.name}>
                                {d.name}
                              </option>
                            ))}
                          </select>
                        </div>
                      )}
                    </>
                  )}

                  {/* ---- heatmap: x/y/dataseries are ALL dimensions ---- */}
                  {model && family === "heatmap" && (
                    <>
                      <div className="space-y-1.5">
                        <Label htmlFor="chart-x">
                          {t("charts.xDimension")}
                          <span className="ml-0.5 text-destructive">*</span>
                        </Label>
                        <select
                          id="chart-x"
                          value={x}
                          onChange={(e) => setX(e.target.value)}
                          className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                          aria-invalid={errors.some((er) => er.field === "x")}
                        >
                          <option value="">{t("charts.pickDimension")}</option>
                          {model.dimensions.map((d) => (
                            <option key={d.name} value={d.name}>
                              {d.name}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="chart-heat-y">
                          {t("charts.yDimension")}
                          <span className="ml-0.5 text-destructive">*</span>
                        </Label>
                        <select
                          id="chart-heat-y"
                          value={heatY}
                          onChange={(e) => setHeatY(e.target.value)}
                          className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                          aria-invalid={errors.some((er) => er.field === "y")}
                        >
                          <option value="">{t("charts.pickDimension")}</option>
                          {model.dimensions.map((d) => (
                            <option key={d.name} value={d.name}>
                              {d.name}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="chart-heat-series">
                          {t("charts.dataseriesDimension")}
                          <span className="ml-0.5 text-destructive">*</span>
                        </Label>
                        <select
                          id="chart-heat-series"
                          value={heatSeries}
                          onChange={(e) => setHeatSeries(e.target.value)}
                          className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                          aria-invalid={errors.some((er) => er.field === "dataseries")}
                        >
                          <option value="">{t("charts.pickDimension")}</option>
                          {model.dimensions.map((d) => (
                            <option key={d.name} value={d.name}>
                              {d.name}
                            </option>
                          ))}
                        </select>
                      </div>
                    </>
                  )}
                </>
              )}

              {/* ---- network: a saved query + column-position labels ---- */}
              {family === "network" && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="chart-saved-query">{t("charts.savedQuery")}</Label>
                    <select
                      id="chart-saved-query"
                      value={savedQueryId}
                      onChange={(e) => {
                        hydratingRef.current = false;
                        setSavedQueryId(e.target.value);
                      }}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    >
                      <option value="">{t("charts.pickSavedQuery")}</option>
                      {savedQueries.map((q) => (
                        <option key={q.id} value={q.id}>
                          {q.name}
                        </option>
                      ))}
                    </select>
                    {savedQueriesQuery.data && savedQueries.length === 0 && (
                      <p className="text-xs text-muted-foreground">{t("charts.noSavedQueries")}</p>
                    )}
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="chart-nodes">
                      {t("charts.nodesColumn")}
                      <span className="ml-0.5 text-destructive">*</span>
                    </Label>
                    <Input
                      id="chart-nodes"
                      value={nodesCol}
                      onChange={(e) => setNodesCol(e.target.value)}
                      placeholder={t("charts.nodesColumnPlaceholder")}
                      aria-invalid={errors.some((er) => er.field === "nodes")}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="chart-children">
                      {t("charts.childrenColumn")}
                      <span className="ml-0.5 text-destructive">*</span>
                    </Label>
                    <Input
                      id="chart-children"
                      value={childrenCol}
                      onChange={(e) => setChildrenCol(e.target.value)}
                      placeholder={t("charts.childrenColumnPlaceholder")}
                      aria-invalid={errors.some((er) => er.field === "children")}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="chart-node-values">{t("charts.nodeValuesColumn")}</Label>
                    <Input
                      id="chart-node-values"
                      value={nodeValuesCol}
                      onChange={(e) => setNodeValuesCol(e.target.value)}
                      placeholder={t("charts.nodeValuesColumnPlaceholder")}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">{t("charts.networkHint")}</p>
                </>
              )}

              {/* ---- metric: a dataset only, no encodings ---- */}
              {family === "metric" && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="chart-dataset">
                      {t("charts.dataset")}
                      <span className="ml-0.5 text-destructive">*</span>
                    </Label>
                    <select
                      id="chart-dataset"
                      value={datasetId}
                      onChange={(e) => {
                        hydratingRef.current = false;
                        setDatasetId(e.target.value);
                      }}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                      aria-invalid={errors.some((er) => er.field === "source")}
                    >
                      <option value="">{t("charts.pickDataset")}</option>
                      {datasets.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                    {datasetsQuery.data && datasets.length === 0 && (
                      <p className="text-xs text-muted-foreground">{t("charts.noDatasets")}</p>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{t("charts.metricHint")}</p>
                </>
              )}
            </div>

            {/* Right column: live preview */}
            <div className="space-y-2">
              <Label>{t("charts.preview")}</Label>
              <div className="min-h-[240px] rounded-md border p-3">
                {previewMutation.isPending ? (
                  <p className="flex items-center gap-2 py-8 text-center text-xs text-muted-foreground">
                    <Loader2 className="size-3 animate-spin" /> {t("charts.previewing")}
                  </p>
                ) : previewError ? (
                  <p role="alert" className="py-8 text-center text-xs text-destructive" data-testid="preview-error">
                    {previewError.message}
                  </p>
                ) : preview && Array.isArray(preview.rows) && preview.rows.length > 0 ? (
                  <>
                    <ChartView
                      chartType={chartType}
                      family={family}
                      columns={preview.columns}
                      rows={preview.rows}
                      title={name.trim() || t("charts.preview")}
                    />
                    <p className="mt-1 text-center text-xs text-muted-foreground">
                      {t("charts.rows", { count: preview.rowCount ?? (preview.rows as unknown[]).length })}
                    </p>
                  </>
                ) : preview && (family === "network" || family === "metric") ? (
                  <ChartView chartType={chartType} family={family} columns={preview.columns} rows={preview.rows} artifact={preview.artifact} />
                ) : preview ? (
                  <p className="py-8 text-center text-xs text-muted-foreground">{t("charts.previewEmpty")}</p>
                ) : (
                  <p className="py-8 text-center text-xs text-muted-foreground">{t("charts.previewHint")}</p>
                )}
              </div>
            </div>
          </form>

          {/* Footer */}
          <div className="flex flex-wrap items-center gap-2 border-t p-4">
            {showErrors && (
              <ul className="text-xs text-muted-foreground">
                {errors.map((er, i) => (
                  <li key={i}>{er.message}</li>
                ))}
              </ul>
            )}
            {banner && (
              <span role="status" className="text-xs text-muted-foreground">
                {banner}
              </span>
            )}
            {saveError && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {saveError.message}
              </p>
            )}
            <div className="ml-auto flex items-center gap-2">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("action.cancel")}
              </Button>
              <Button type="button" onClick={runSave} disabled={!canSave}>
                {activeMutation.isPending ? t("charts.saving") : t("charts.save")}
              </Button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
