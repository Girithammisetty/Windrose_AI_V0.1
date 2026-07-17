/**
 * Canvas state + pure DAG helpers for the no-code pipeline builder.
 *
 * The visual builder keeps its node/edge state in a zustand store (no react-flow
 * dependency). The pure helpers below — node construction from a catalog step,
 * edge-connection legality, and serialization to the bff `definition` shape — are
 * framework-free so they are unit-testable in isolation.
 *
 * Serialized shape (POSTed as `definition`):
 *   { nodes: [{ alias, component, parameters, outputs:[{name,type}] }],
 *     edges: [{ from: "alias.port", to: "alias.port", type }] }
 */
import { create } from "zustand";
import type {
  PipelineDefinition,
  PipelinePort,
  PipelineStepParam,
  PipelineStepType,
} from "@/lib/graphql/types";
import { collect, defaultValues, type ParamValues } from "./form";

/** Wildcard input-port type: accepts any upstream output type. */
export const ANY_PORT = "*";
/**
 * The backend's default consumer input-port type (dag.py). Catalog components do
 * not declare typed inputs, so every input slot accepts a `dataframe`; this lets
 * the canvas reject type-incompatible wires (e.g. a `model` output → a `dataframe`
 * input) live, before the user hits Validate.
 */
export const DEFAULT_INPUT_TYPE = "dataframe";
/** Hard cap on how many input slots we render for a step (min/max still apply). */
const MAX_INPUT_SLOTS = 8;

export interface CanvasNode {
  id: string;
  component: string; // real catalog component name (e.g. "xgboost-train")
  displayName: string;
  category: string;
  x: number;
  y: number;
  inputs: PipelinePort[]; // positional input slots (in0, in1, …)
  outputs: PipelinePort[]; // named + typed output ports from the catalog
  minInputs: number;
  maxInputs: number;
  maxOutputs: number;
  params: PipelineStepParam[]; // parameter schema
  values: ParamValues; // current form values (raw)
}

export interface CanvasEdge {
  id: string;
  from: { nodeId: string; port: string; type: string };
  to: { nodeId: string; port: string };
}

/** An in-progress connection: the output port the user clicked first. */
export interface PendingConnection {
  nodeId: string;
  port: string;
  type: string;
}

function uid(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
}

function inputSlots(maxInputs: number): PipelinePort[] {
  const n = Math.max(0, Math.min(maxInputs, MAX_INPUT_SLOTS));
  return Array.from({ length: n }, (_, i) => ({ name: `in${i}`, type: DEFAULT_INPUT_TYPE }));
}

/** Build a canvas node from a catalog step type. */
export function nodeFromStep(step: PipelineStepType, at: { x: number; y: number }): CanvasNode {
  return {
    id: uid(),
    component: step.name,
    displayName: step.displayName,
    category: step.category,
    x: at.x,
    y: at.y,
    inputs: inputSlots(step.maxInputs),
    // Use the catalog's declared outputs verbatim. Sink components (write /
    // batch-write / comment) legitimately declare zero outputs + max_outputs:0;
    // injecting a phantom "out" here makes the backend reject the node with
    // ARITY_VIOLATION (declared_out 1 > max_outputs 0).
    outputs: step.outputs,
    minInputs: step.minInputs,
    maxInputs: step.maxInputs,
    maxOutputs: step.maxOutputs,
    params: step.parameters,
    values: defaultValues(step.parameters),
  };
}

/** Two port types are compatible if the input accepts any, or they match. */
export function typesCompatible(fromType: string, toType: string): boolean {
  return toType === ANY_PORT || fromType === toType;
}

export interface ConnectCheck {
  ok: boolean;
  reason?: string;
}

/**
 * Whether a pending output→input connection is legal: not a self-loop, port
 * types compatible, target input slot free, no duplicate, and within the
 * source's maxOutputs / target's maxInputs fan limits.
 */
export function canConnect(
  pending: PendingConnection,
  target: { nodeId: string; port: string },
  nodes: CanvasNode[],
  edges: CanvasEdge[],
): ConnectCheck {
  if (pending.nodeId === target.nodeId) return { ok: false, reason: "Cannot connect a node to itself" };

  const src = nodes.find((n) => n.id === pending.nodeId);
  const dst = nodes.find((n) => n.id === target.nodeId);
  if (!src || !dst) return { ok: false, reason: "Unknown node" };

  const inPort = dst.inputs.find((p) => p.name === target.port);
  if (!inPort) return { ok: false, reason: "Unknown input port" };
  if (!typesCompatible(pending.type, inPort.type))
    return { ok: false, reason: `Type mismatch: ${pending.type} → ${inPort.type}` };

  if (edges.some((e) => e.to.nodeId === target.nodeId && e.to.port === target.port))
    return { ok: false, reason: "Input already connected" };
  if (
    edges.some(
      (e) =>
        e.from.nodeId === pending.nodeId &&
        e.from.port === pending.port &&
        e.to.nodeId === target.nodeId &&
        e.to.port === target.port,
    )
  )
    return { ok: false, reason: "Edge already exists" };

  const outCount = edges.filter((e) => e.from.nodeId === pending.nodeId).length;
  if (outCount >= src.maxOutputs) return { ok: false, reason: "Source output limit reached" };
  const inCount = edges.filter((e) => e.to.nodeId === target.nodeId).length;
  if (inCount >= dst.maxInputs) return { ok: false, reason: "Target input limit reached" };

  return { ok: true };
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "step";

/** Stable, unique alias per node: slug(component) + running index. */
export function aliasMap(nodes: CanvasNode[]): Map<string, string> {
  const counts = new Map<string, number>();
  const out = new Map<string, string>();
  for (const n of nodes) {
    const base = slugify(n.component);
    const i = counts.get(base) ?? 0;
    counts.set(base, i + 1);
    out.set(n.id, `${base}_${i}`);
  }
  return out;
}

export interface SerializeResult {
  definition: PipelineDefinition;
  /** Per-node parameter errors keyed by node id (empty when all valid). */
  paramErrors: Record<string, Record<string, string>>;
  ok: boolean;
}

/** Serialize the canvas to the bff `{nodes,edges}` definition + collect param errors. */
export function serializeDefinition(nodes: CanvasNode[], edges: CanvasEdge[]): SerializeResult {
  const aliases = aliasMap(nodes);
  const paramErrors: Record<string, Record<string, string>> = {};

  const defNodes = nodes.map((n) => {
    const { parameters, errors } = collect(n.params, n.values);
    if (Object.keys(errors).length) paramErrors[n.id] = errors;
    return {
      alias: aliases.get(n.id)!,
      component: n.component,
      parameters,
      outputs: n.outputs,
    };
  });

  const defEdges = edges.map((e) => ({
    from: `${aliases.get(e.from.nodeId)!}.${e.from.port}`,
    to: `${aliases.get(e.to.nodeId)!}.${e.to.port}`,
    type: e.from.type,
  }));

  return {
    definition: { nodes: defNodes, edges: defEdges },
    paramErrors,
    ok: Object.keys(paramErrors).length === 0,
  };
}

/**
 * Reverse of {@link collect}: turn a saved node's typed `parameters` back into the
 * raw string/boolean form-values a widget edits. Starts from the schema defaults so
 * params omitted at save time (empty optionals) come back as their default/empty
 * state, then overrides with the stored values (JSON re-stringified for the
 * array/object/columns/key_value widgets that store text).
 */
export function valuesFromParameters(
  params: PipelineStepParam[],
  parameters: Record<string, unknown>,
): ParamValues {
  const values = defaultValues(params);
  for (const p of params) {
    if (!(p.name in parameters)) continue;
    const raw = parameters[p.name];
    if (p.type === "boolean") {
      values[p.name] = raw === true || raw === "true";
      continue;
    }
    if (raw === null || raw === undefined) continue;
    const fmt = p.format ?? "";
    if (p.type === "object" || p.type === "array" || fmt === "columns" || fmt === "key_value") {
      values[p.name] = typeof raw === "string" ? raw : JSON.stringify(raw);
    } else {
      values[p.name] = String(raw);
    }
  }
  return values;
}

/** Split an `"alias.port"` edge reference. Aliases never contain a dot (slug + "_" +
 * index) and ports are dot-free, so the last dot is the separator. */
function splitRef(ref: string): [string, string] {
  const i = ref.lastIndexOf(".");
  return i < 0 ? [ref, ""] : [ref.slice(0, i), ref.slice(i + 1)];
}

/**
 * Inverse of {@link serializeDefinition}: rebuild canvas nodes + edges from a saved
 * `{nodes,edges}` definition (the builder's edit mode). Each node is reconstructed
 * from its catalog step (so it carries the live parameter schema + input slots) with
 * its saved parameter values applied; a component missing from the current catalog
 * is preserved structurally so the DAG survives the round-trip. Positions are not
 * stored in `definition`, so nodes are laid out in a simple grid.
 */
export function hydrateFromDefinition(
  definition: PipelineDefinition,
  steps: PipelineStepType[],
): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  const stepByName = new Map(steps.map((s) => [s.name, s]));
  const aliasToId = new Map<string, string>();

  const nodes: CanvasNode[] = (definition.nodes ?? []).map((dn, i) => {
    const at = { x: 60 + (i % 5) * 220, y: 60 + (i % 6) * 120 };
    const step = stepByName.get(dn.component);
    let node: CanvasNode;
    if (step) {
      node = nodeFromStep(step, at);
      node.values = valuesFromParameters(step.parameters, dn.parameters ?? {});
    } else {
      // Component not in the current catalog: keep the node so the graph is intact,
      // using its saved outputs (params are non-editable without a schema).
      const outputs = dn.outputs ?? [];
      node = {
        id: uid(), component: dn.component, displayName: dn.component,
        category: "utility", x: at.x, y: at.y,
        inputs: inputSlots(MAX_INPUT_SLOTS), outputs,
        minInputs: 0, maxInputs: MAX_INPUT_SLOTS, maxOutputs: outputs.length,
        params: [], values: {},
      };
    }
    aliasToId.set(dn.alias, node.id);
    return node;
  });

  const edges: CanvasEdge[] = [];
  for (const de of definition.edges ?? []) {
    const [fromAlias, fromPort] = splitRef(de.from);
    const [toAlias, toPort] = splitRef(de.to);
    const fromId = aliasToId.get(fromAlias);
    const toId = aliasToId.get(toAlias);
    if (!fromId || !toId) continue; // dangling ref (unknown/skipped node)
    edges.push({
      id: uid(),
      from: { nodeId: fromId, port: fromPort, type: String(de.type) },
      to: { nodeId: toId, port: toPort },
    });
  }
  return { nodes, edges };
}

/**
 * Flat run-parameters map for `runPipeline`: the URN value of every `dataset_ref`
 * param across the canvas, keyed by param name (e.g. `{ dataset: "wr:…/123" }`).
 * The orchestrator merges these over the template version's run_parameters, so
 * the run materializes the dataset(s) the user picked in the builder.
 */
export function collectRunParameters(nodes: CanvasNode[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const n of nodes) {
    const { parameters } = collect(n.params, n.values);
    for (const p of n.params) {
      if (p.type === "dataset_ref" && parameters[p.name] !== undefined) {
        out[p.name] = parameters[p.name];
      }
    }
  }
  return out;
}

/**
 * IO components that read a dataset into the pipeline. The dataset they point at
 * is the source of truth for data-aware column binding (column / columns params).
 */
const READ_COMPONENTS = new Set(["read-from-warehouse", "batch-read-from-warehouse"]);

/** The chosen dataset URN of the first node that has a set `dataset_ref` param. */
function firstDatasetUrn(nodes: CanvasNode[]): string {
  for (const n of nodes) {
    const p = n.params.find((pp) => pp.type === "dataset_ref" || pp.format === "dataset_ref");
    if (!p) continue;
    const v = n.values[p.name];
    if (typeof v === "string" && v.trim() !== "") return v;
  }
  return "";
}

/**
 * Resolve the dataset id (last URN path-segment) feeding the pipeline, for the
 * data-aware `column`/`columns` widgets. Prefers a read-from-warehouse node;
 * falls back to any node with a chosen `dataset_ref`. Returns "" when no dataset
 * is picked yet, so the widgets degrade to free-text/array inputs.
 */
export function resolveInputDatasetId(nodes: CanvasNode[]): string {
  const urn = firstDatasetUrn(nodes.filter((n) => READ_COMPONENTS.has(n.component))) || firstDatasetUrn(nodes);
  return urn ? urn.split("/").pop() ?? "" : "";
}

/* ------------------------------ zustand store ------------------------------ */

interface CanvasStore {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  selectedId: string | null;
  pending: PendingConnection | null;
  /** Validation issue messages keyed by node id (from validatePipeline). */
  issues: Record<string, string[]>;

  addNode: (node: CanvasNode) => void;
  moveNode: (id: string, x: number, y: number) => void;
  removeNode: (id: string) => void;
  selectNode: (id: string | null) => void;
  setValue: (id: string, name: string, value: string | boolean) => void;
  /** Click an output port: begin (or cancel) a pending connection. */
  beginConnect: (nodeId: string, port: string, type: string) => void;
  /** Click an input port: complete a pending connection if legal. */
  endConnect: (nodeId: string, port: string) => ConnectCheck;
  cancelPending: () => void;
  removeEdge: (id: string) => void;
  setIssues: (issues: Record<string, string[]>) => void;
  clearIssues: () => void;
  /** Replace the whole canvas (edit mode: rehydrate from a saved definition). */
  load: (nodes: CanvasNode[], edges: CanvasEdge[]) => void;
  reset: () => void;
}

export const useCanvasStore = create<CanvasStore>((set, get) => ({
  nodes: [],
  edges: [],
  selectedId: null,
  pending: null,
  issues: {},

  addNode: (node) => set((s) => ({ nodes: [...s.nodes, node], selectedId: node.id })),
  moveNode: (id, x, y) => set((s) => ({ nodes: s.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)) })),
  removeNode: (id) =>
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== id),
      edges: s.edges.filter((e) => e.from.nodeId !== id && e.to.nodeId !== id),
      selectedId: s.selectedId === id ? null : s.selectedId,
    })),
  selectNode: (id) => set({ selectedId: id }),
  setValue: (id, name, value) =>
    set((s) => ({
      nodes: s.nodes.map((n) => (n.id === id ? { ...n, values: { ...n.values, [name]: value } } : n)),
    })),
  beginConnect: (nodeId, port, type) =>
    set((s) =>
      s.pending && s.pending.nodeId === nodeId && s.pending.port === port
        ? { pending: null } // clicking the same output again cancels
        : { pending: { nodeId, port, type } },
    ),
  endConnect: (nodeId, port) => {
    const { pending, nodes, edges } = get();
    if (!pending) return { ok: false, reason: "No source port selected" };
    const check = canConnect(pending, { nodeId, port }, nodes, edges);
    if (check.ok) {
      set({
        edges: [...edges, { id: uid(), from: { ...pending }, to: { nodeId, port } }],
        pending: null,
      });
    }
    return check;
  },
  cancelPending: () => set({ pending: null }),
  removeEdge: (id) => set((s) => ({ edges: s.edges.filter((e) => e.id !== id) })),
  setIssues: (issues) => set({ issues }),
  clearIssues: () => set({ issues: {} }),
  load: (nodes, edges) => set({ nodes, edges, selectedId: null, pending: null, issues: {} }),
  reset: () => set({ nodes: [], edges: [], selectedId: null, pending: null, issues: {} }),
}));
