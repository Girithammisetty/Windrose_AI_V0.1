"use client";
import type { PipelineStepParam } from "@/lib/graphql/types";
import { Input, Textarea, Label } from "@/components/ui/primitives";
import { useDatasets } from "@/lib/graphql/hooks";
import { t } from "@/lib/i18n/messages";
import { cn } from "@/lib/utils";

/**
 * Generic schema-driven field renderer, extracted from ConnectionForm's `Field`
 * and generalized for `PipelineStepParam`. Widget selection is a `(type, format)`
 * dispatch: `type` is the storage shape, `format` (a JSON-Schema semantic hint)
 * drives the widget + data-binding when recognized. Data-aware formats
 * (`column` / `columns`) bind to `availableColumns` — the real columns of the
 * dataset flowing into the pipeline — and degrade to free-text / array inputs
 * when no columns are resolvable yet. An UNKNOWN/absent format falls through to
 * the base-type widget (forward-compatible) — never crashes.
 */
export function SchemaField({
  param,
  value,
  error,
  onChange,
  availableColumns,
}: {
  param: PipelineStepParam;
  value: string | boolean | undefined;
  error?: string;
  onChange: (v: string | boolean) => void;
  /** Real column names of the dataset feeding the pipeline (data-binding). */
  availableColumns?: string[];
}) {
  const id = `param-${param.name}`;
  const describedBy = error ? `${id}-error` : param.help ? `${id}-help` : undefined;
  const common = {
    id,
    "aria-invalid": !!error,
    "aria-describedby": describedBy,
  } as const;

  const format = param.format ?? "";
  const cols = availableColumns ?? [];
  const hasCols = cols.length > 0;
  const strValue = typeof value === "string" ? value : "";

  let widget: React.ReactNode;

  if (param.type === "boolean") {
    // Boolean stays first: its label/checkbox layout differs from every other widget.
    widget = (
      <input
        {...common}
        type="checkbox"
        checked={value === true}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 accent-[hsl(var(--primary))]"
      />
    );
  } else if (format === "dataset_ref" || param.type === "dataset_ref") {
    widget = <DatasetRefField param={param} common={common} value={value} onChange={onChange} />;
  } else if (format === "columns") {
    // Data-aware multi-select over the pipeline's columns; degrade to the raw
    // array textarea when no dataset/columns are resolvable yet (never blocks).
    widget = hasCols ? (
      <ColumnsMultiSelect
        {...common}
        aria-label={param.name}
        columns={cols}
        value={strValue}
        onChange={onChange}
      />
    ) : (
      <Textarea
        {...common}
        aria-label={param.name}
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        placeholder="[ ]"
        className="font-mono text-xs"
      />
    );
  } else if (format === "column") {
    // Single-select over the pipeline's columns; degrade to free text when none.
    widget = hasCols ? (
      <ColumnSelect
        {...common}
        aria-label={param.name}
        columns={cols}
        required={param.required}
        value={strValue}
        onChange={onChange}
      />
    ) : (
      <Input
        {...common}
        aria-label={param.name}
        type="text"
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
    );
  } else if (format === "expression") {
    widget = (
      <Textarea
        {...common}
        aria-label={param.name}
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        rows={4}
        className="font-mono text-xs"
      />
    );
  } else if (format === "key_value") {
    // Minimal, robust key/value editor: a labeled JSON object textarea (parsed
    // by form.collect's object branch into a real JSON object).
    widget = (
      <Textarea
        {...common}
        aria-label={param.name}
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        placeholder="{ }"
        className="font-mono text-xs"
      />
    );
  } else if ((param.type === "enum" || format === "enum") && param.enumValues) {
    widget = (
      <select
        {...common}
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        className={SELECT_CLS}
      >
        {!param.required && <option value="">—</option>}
        {param.enumValues.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  } else if (param.type === "object" || param.type === "array") {
    widget = (
      <Textarea
        {...common}
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        placeholder={param.type === "array" ? "[ ]" : "{ }"}
        className="font-mono text-xs"
      />
    );
  } else {
    const numeric = param.type === "integer" || param.type === "number";
    widget = (
      <Input
        {...common}
        type={numeric ? "number" : "text"}
        min={numeric && param.min != null ? param.min : undefined}
        max={numeric && param.max != null ? param.max : undefined}
        step={param.type === "integer" ? 1 : undefined}
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
    );
  }

  const isCheckbox = param.type === "boolean";
  return (
    <div className={cn("space-y-1.5", isCheckbox && "flex items-center gap-2 space-y-0")}>
      <Label htmlFor={id} className={cn(isCheckbox && "order-2")}>
        {param.name}
        {param.required && <span className="ml-0.5 text-destructive">*</span>}
      </Label>
      {widget}
      {param.help && !error && (
        <p id={`${id}-help`} className="text-xs text-muted-foreground">
          {param.help}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

const SELECT_CLS = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";

/** Parse a stored JSON-array string into a `string[]` (tolerant of empty/invalid). */
function parseArrayValue(value: string): string[] {
  if (value.trim() === "") return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map((v) => String(v)) : [];
  } catch {
    return [];
  }
}

/**
 * Data-aware multi-select over the pipeline's columns. Stored as a JSON-array
 * string (matching `form.collect`'s `array` branch → a real JSON array of
 * column names). Preserves already-selected columns that are no longer offered
 * (e.g. the upstream dataset changed) so a save never silently drops them.
 */
function ColumnsMultiSelect({
  columns,
  value,
  onChange,
  ...rest
}: {
  columns: string[];
  value: string;
  onChange: (v: string) => void;
  id: string;
  "aria-invalid": boolean;
  "aria-describedby": string | undefined;
  "aria-label": string;
}) {
  const selected = parseArrayValue(value);
  const options = [...columns, ...selected.filter((c) => !columns.includes(c))];
  return (
    <select
      {...rest}
      multiple
      value={selected}
      size={Math.min(Math.max(options.length, 2), 6)}
      onChange={(e) => {
        const next = Array.from(e.target.selectedOptions, (o) => o.value);
        onChange(JSON.stringify(next));
      }}
      className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
    >
      {options.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );
}

/** Data-aware single-select over the pipeline's columns (stored as a plain string). */
function ColumnSelect({
  columns,
  required,
  value,
  onChange,
  ...rest
}: {
  columns: string[];
  required: boolean;
  value: string;
  onChange: (v: string) => void;
  id: string;
  "aria-invalid": boolean;
  "aria-describedby": string | undefined;
  "aria-label": string;
}) {
  const known = columns.includes(value);
  return (
    <select
      {...rest}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={SELECT_CLS}
    >
      <option value="">{required ? t("pipelines.columnSelect") : "—"}</option>
      {/* Keep a preset column selectable even if it's not in the current list. */}
      {value && !known && <option value={value}>{value}</option>}
      {columns.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );
}

/**
 * Dataset picker for a `dataset_ref` param: a `<select>` over the workspace's
 * datasets whose option value is the dataset URN. Preserves a preset URN even if
 * it isn't in the first page of results, and degrades to a free-text URN input
 * while loading is done but no datasets exist yet.
 */
function DatasetRefField({
  param,
  common,
  value,
  onChange,
}: {
  param: PipelineStepParam;
  common: { id: string; "aria-invalid": boolean; "aria-describedby": string | undefined };
  value: string | boolean | undefined;
  onChange: (v: string) => void;
}) {
  const { data, isLoading } = useDatasets();
  const current = typeof value === "string" ? value : "";
  const datasets = data?.pages.flatMap((p) => p.nodes) ?? [];

  if (isLoading) {
    return (
      <select {...common} aria-label={param.name} disabled className={SELECT_CLS}>
        <option>{t("pipelines.datasetLoading")}</option>
      </select>
    );
  }

  // No datasets in the workspace yet: fall back to a free-text URN input so a
  // known URN can still be entered and any preset value stays editable.
  if (datasets.length === 0) {
    return (
      <>
        <Input
          {...common}
          aria-label={param.name}
          type="text"
          value={current}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t("pipelines.datasetUrnPlaceholder")}
          autoComplete="off"
        />
        <p className="text-xs text-muted-foreground">{t("pipelines.noDatasets")}</p>
      </>
    );
  }

  const known = new Set(datasets.map((d) => d.urn));
  return (
    <select
      {...common}
      aria-label={param.name}
      value={current}
      onChange={(e) => onChange(e.target.value)}
      className={SELECT_CLS}
    >
      <option value="">{param.required ? t("pipelines.datasetSelect") : "—"}</option>
      {/* Keep a preset URN selectable even if it's not in the loaded page. */}
      {current && !known.has(current) && <option value={current}>{current}</option>}
      {datasets.map((d) => (
        <option key={d.id} value={d.urn}>
          {d.name}
        </option>
      ))}
    </select>
  );
}
