/**
 * Dynamic connection-form logic (UI-FR-011). Pure, framework-free helpers that
 * turn a connector-type field schema (from bff `connectorTypes`) into default
 * form values and, on submit, into the `{config, secrets}` the createConnection
 * mutation expects — with per-field validation derived from the schema (types,
 * required, enum). Kept separate from the React component so it is unit-testable.
 */
import { z } from "zod";
import type { ConnectorField } from "@/lib/graphql/types";

export type FormValues = Record<string, string | boolean>;

/** Seed form state from the field defaults (secrets always start blank). */
export function defaultValues(fields: ConnectorField[]): FormValues {
  const v: FormValues = {};
  for (const f of fields) {
    if (f.secret) {
      v[f.name] = "";
    } else if (f.type === "boolean") {
      v[f.name] = f.default === true;
    } else if (f.default !== undefined && f.default !== null) {
      v[f.name] = String(f.default);
    } else {
      v[f.name] = "";
    }
  }
  return v;
}

export interface CollectResult {
  ok: boolean;
  errors: Record<string, string>;
  /** Non-secret config (typed, empty optionals omitted). */
  config: Record<string, unknown>;
  /** Write-only secrets (only the ones the user actually entered). */
  secrets: Record<string, string>;
}

const intSchema = z.number().int();

/**
 * Validate + coerce the form values against the connector's field schema.
 * Empty optional fields are omitted; empty required fields error. Secrets are
 * collected only when non-blank (so an edit can "keep the stored value").
 */
export function collect(fields: ConnectorField[], values: FormValues): CollectResult {
  const errors: Record<string, string> = {};
  const config: Record<string, unknown> = {};
  const secrets: Record<string, string> = {};

  for (const f of fields) {
    const raw = values[f.name];

    if (f.secret) {
      const s = typeof raw === "string" ? raw : "";
      if (s !== "") secrets[f.name] = s;
      continue;
    }

    if (f.type === "boolean") {
      config[f.name] = raw === true || raw === "true";
      continue;
    }

    const s = typeof raw === "string" ? raw.trim() : "";
    if (s === "") {
      if (f.required) errors[f.name] = "Required";
      continue; // optional + empty → omit
    }

    if (f.type === "integer" || f.type === "number") {
      const n = Number(s);
      if (!Number.isFinite(n)) {
        errors[f.name] = "Must be a number";
      } else if (f.type === "integer" && !intSchema.safeParse(n).success) {
        errors[f.name] = "Must be a whole number";
      } else {
        config[f.name] = n;
      }
    } else if (f.type === "enum") {
      if (f.enum && f.enum.length > 0 && !f.enum.includes(s)) {
        errors[f.name] = "Invalid value";
      } else {
        config[f.name] = s;
      }
    } else if (f.type === "object" || f.type === "array") {
      try {
        config[f.name] = JSON.parse(s);
      } catch {
        errors[f.name] = "Must be valid JSON";
      }
    } else {
      config[f.name] = s;
    }
  }

  return { ok: Object.keys(errors).length === 0, errors, config, secrets };
}

/** Grouping order for the connector-type picker. "file-upload" leads: it is the
 * synthetic entry that routes to the upload wizard rather than the connection form. */
export const CATEGORY_ORDER = ["file-upload", "database", "warehouse", "object-store", "file", "saas"] as const;

export const CATEGORY_LABELS: Record<string, string> = {
  "file-upload": "File upload",
  database: "Databases",
  warehouse: "Warehouses",
  "object-store": "Object stores",
  file: "File servers",
  saas: "SaaS",
};

/** Synthetic connector-type for the "upload a file" data source. It carries no
 * config/secrets — selecting it navigates to the upload wizard instead of the
 * connection form (see the New Data Source page). */
export const FILE_UPLOAD_CONNECTOR = "file_upload";
