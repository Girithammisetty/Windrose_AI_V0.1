/**
 * Pure logic for the semantic-model definition editor: stub builders, the
 * server-side vocabulary (mirrors semantic-service app/domain/definition.py +
 * expr.py exactly so the UI never offers a choice the backend would reject),
 * and parsing of the real `[{object, problem}]` validation list the backend
 * returns (submit-time full validation) or single-message structural errors
 * (save-time) into a per-row lookup the editor can render inline.
 */
import type { SemanticDefinitionDoc } from "@/lib/graphql/types";
import { urnParts } from "@/lib/utils";

export const DIM_TYPES = ["categorical", "time", "numeric", "boolean", "geo"] as const;
export type DimType = (typeof DIM_TYPES)[number];

export const TIME_GRAINS = ["hour", "day", "week", "month", "quarter", "year"] as const;

export const AGG_FNS = ["sum", "avg", "min", "max", "count", "count_distinct", "first"] as const;

export const JOIN_TYPES = ["left", "inner"] as const;
export const CARDINALITIES = ["many_to_one", "one_to_one"] as const;

/** Whitelisted expression functions (app/domain/expr.py FUNC_WHITELIST). Any
 * other function call is rejected by the backend at save time. */
export const EXPR_FUNCS = [
  "coalesce", "nullif", "cast", "date_trunc", "extract",
  "lower", "upper", "trim", "concat", "abs", "round",
] as const;

const NAME_RE = /^[a-z][a-z0-9_]{0,62}$/;

/** Mirrors semantic-service's name-field regex (entity/dimension/measure/
 * join_path names). Used for inline client-side hinting only — the server is
 * still the authority. */
export function isValidName(name: string): boolean {
  return NAME_RE.test(name);
}

export function emptyDefinition(): SemanticDefinitionDoc {
  return { entities: [], dimensions: [], measures: [], join_paths: [] };
}

/**
 * Coerce a definition loaded from the server into a fully-populated doc: every
 * collection defaults to `[]`. The stored `definitionJson` can omit empty
 * collections (a model authored without joins has no `join_paths` key), and the
 * editor sections index these arrays directly — without this the JoinPaths/
 * Entities sections crash with "Cannot read properties of undefined".
 */
export function normalizeDefinition(raw: unknown): SemanticDefinitionDoc {
  const d = (raw ?? {}) as Partial<SemanticDefinitionDoc>;
  return {
    entities: d.entities ?? [],
    dimensions: d.dimensions ?? [],
    measures: d.measures ?? [],
    join_paths: d.join_paths ?? [],
  };
}

/** True when a definition has nothing authored yet (a fresh draft). */
export function isDefinitionEmpty(doc: SemanticDefinitionDoc | null | undefined): boolean {
  if (!doc) return true;
  return (
    (doc.entities?.length ?? 0) === 0 &&
    (doc.dimensions?.length ?? 0) === 0 &&
    (doc.measures?.length ?? 0) === 0 &&
    (doc.join_paths?.length ?? 0) === 0
  );
}

export function newEntity(): SemanticDefinitionDoc["entities"][number] {
  return {
    name: "",
    dataset_urn: "",
    table: "",
    primary_key: [],
    dataset_version_policy: { policy: "latest" },
  };
}

export function newDimension(entityName?: string): SemanticDefinitionDoc["dimensions"][number] {
  return {
    name: "",
    entity: entityName ?? "",
    column: "",
    type: "categorical",
    time_grains: [],
    synonyms: [],
    deprecated: false,
  };
}

export function newMeasure(entityName?: string): SemanticDefinitionDoc["measures"][number] {
  return {
    name: "",
    entity: entityName ?? "",
    agg: "count",
    synonyms: [],
    deprecated: false,
  };
}

export function newJoinPath(): SemanticDefinitionDoc["join_paths"][number] {
  return {
    name: "",
    from_entity: "",
    to_entity: "",
    join_type: "left",
    on: [{ from_column: "", to_column: "" }],
    cardinality: "many_to_one",
  };
}

/** One real validation problem, as returned by the backend (full-validation
 * `[{object, problem}]` list at submit, or a single structural message at
 * save) — reshaped to a stable {kind, name, problem} triple the editor rows
 * key off of. */
export interface DefinitionProblem {
  /** "entity" | "dimension" | "measure" | "join_path" | "join_paths" | "unknown". */
  kind: string;
  /** The object's `name` field (empty for collection-level problems like "join_paths"). */
  name: string;
  problem: string;
}

/** Parse the raw `details` array from a GraphQL error's `extensions.details`
 * (submit-time full validation: `[{object: "dimension/foo", problem: "..."}]`)
 * into DefinitionProblem[]. Defensive against a single-message save-time error
 * (no details array) — the caller passes the error message as a fallback. */
export function parseValidationDetails(details: unknown): DefinitionProblem[] {
  if (!Array.isArray(details)) return [];
  const out: DefinitionProblem[] = [];
  for (const item of details) {
    if (!item || typeof item !== "object") continue;
    const object = (item as { object?: unknown }).object;
    const problem = (item as { problem?: unknown }).problem;
    if (typeof problem !== "string") continue;
    if (typeof object === "string") {
      const slash = object.indexOf("/");
      if (slash >= 0) {
        out.push({ kind: object.slice(0, slash), name: object.slice(slash + 1), problem });
      } else {
        out.push({ kind: object, name: "", problem });
      }
    } else {
      out.push({ kind: "unknown", name: "", problem });
    }
  }
  return out;
}

/** Group parsed problems by "kind/name" for O(1) per-row lookup, e.g.
 * "dimension/claim_type" -> ["column 'x' not in dataset schema of entity 'y'"]. */
export function groupProblemsByObject(problems: DefinitionProblem[]): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const p of problems) {
    const key = p.name ? `${p.kind}/${p.name}` : p.kind;
    const list = m.get(key) ?? [];
    list.push(p.problem);
    m.set(key, list);
  }
  return m;
}

/** Extract the dataset id from a `wr:<tenant>:dataset:dataset/<id>` URN
 * (mirrors semantic-service's HttpDatasetClient._dataset_id regex), for
 * looking up an entity's real columns via datasetSchema(datasetId). */
export function datasetIdFromUrn(urn: string | undefined | null): string | undefined {
  if (!urn) return undefined;
  const { path } = urnParts(urn);
  if (!path) return undefined;
  const slash = path.lastIndexOf("/");
  return slash >= 0 ? path.slice(slash + 1) : undefined;
}
