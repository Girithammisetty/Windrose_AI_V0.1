/**
 * Dynamic per-step parameter-form logic for the no-code pipeline builder. Pure,
 * framework-free helpers that turn a step type's `parameters` schema (from bff
 * `pipelineStepTypes` / `algorithmTemplates`) into default form values and, on
 * change, into the typed `parameters` object stored on a canvas node — with
 * per-field validation derived from the schema (types, required, enum, min/max).
 *
 * Mirrors src/lib/connections/form.ts but for PipelineStepParam (enumValues +
 * numeric min/max instead of enum + secret). Kept separate from React so it is
 * unit-testable.
 */
import { z } from "zod";
import type { PipelineStepParam, PipelineStepCategory } from "@/lib/graphql/types";

export type ParamValues = Record<string, string | boolean>;

/** Seed form state from the parameter defaults. */
export function defaultValues(params: PipelineStepParam[]): ParamValues {
  const v: ParamValues = {};
  for (const p of params) {
    if (p.type === "boolean") {
      v[p.name] = p.default === true;
    } else if (p.default !== undefined && p.default !== null) {
      v[p.name] =
        p.type === "object" || p.type === "array"
          ? JSON.stringify(p.default)
          : String(p.default);
    } else {
      v[p.name] = "";
    }
  }
  return v;
}

export interface CollectResult {
  ok: boolean;
  errors: Record<string, string>;
  /** Typed parameters (empty optionals omitted). */
  parameters: Record<string, unknown>;
}

const intSchema = z.number().int();

/**
 * Validate + coerce the form values against the step's parameter schema. Empty
 * optional fields are omitted; empty required fields error. Numeric min/max are
 * enforced; enum membership is checked against `enumValues`.
 */
export function collect(params: PipelineStepParam[], values: ParamValues): CollectResult {
  const errors: Record<string, string> = {};
  const parameters: Record<string, unknown> = {};

  for (const p of params) {
    const raw = values[p.name];

    if (p.type === "boolean") {
      parameters[p.name] = raw === true || raw === "true";
      continue;
    }

    const s = typeof raw === "string" ? raw.trim() : "";
    if (s === "") {
      if (p.required) errors[p.name] = "Required";
      continue; // optional + empty → omit
    }

    // Semantic `format` drives the storage shape independent of `type` (the
    // widget layer stores columns/key_value as JSON, column/expression as text).
    // Unknown formats fall through to the type-based coercion below.
    const fmt = p.format ?? "";
    if (fmt === "columns" || fmt === "key_value") {
      try {
        parameters[p.name] = JSON.parse(s); // → real JSON array / object
      } catch {
        errors[p.name] = "Must be valid JSON";
      }
      continue;
    }
    if (fmt === "column" || fmt === "expression" || fmt === "dataset_ref") {
      parameters[p.name] = s;
      continue;
    }

    if (p.type === "integer" || p.type === "number") {
      const n = Number(s);
      if (!Number.isFinite(n)) {
        errors[p.name] = "Must be a number";
      } else if (p.type === "integer" && !intSchema.safeParse(n).success) {
        errors[p.name] = "Must be a whole number";
      } else if (p.min != null && n < p.min) {
        errors[p.name] = `Must be ≥ ${p.min}`;
      } else if (p.max != null && n > p.max) {
        errors[p.name] = `Must be ≤ ${p.max}`;
      } else {
        parameters[p.name] = n;
      }
    } else if (p.type === "enum") {
      if (p.enumValues && p.enumValues.length > 0 && !p.enumValues.includes(s)) {
        errors[p.name] = "Invalid value";
      } else {
        parameters[p.name] = s;
      }
    } else if (p.type === "object" || p.type === "array") {
      try {
        parameters[p.name] = JSON.parse(s);
      } catch {
        errors[p.name] = "Must be valid JSON";
      }
    } else {
      parameters[p.name] = s;
    }
  }

  return { ok: Object.keys(errors).length === 0, errors, parameters };
}

/** Grouping order for the step palette. */
export const CATEGORY_ORDER: PipelineStepCategory[] = ["io", "data_prep", "algorithm", "utility"];

export const CATEGORY_LABELS: Record<string, string> = {
  io: "Input / Output",
  data_prep: "Data prep",
  algorithm: "Algorithms",
  utility: "Utilities",
};

/** Pipeline-type options for the builder toolbar. */
export const PIPELINE_TYPES = ["data_prep", "training", "inference"] as const;
export type PipelineType = (typeof PIPELINE_TYPES)[number];
