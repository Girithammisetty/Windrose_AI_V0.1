"use client";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/primitives";

/**
 * Returns a validation error message for a JSON textarea, or null when valid.
 * Empty text is an error only when `required`; otherwise an empty optional
 * field is treated as "not provided" and passes.
 */
export function jsonError(text: string, opts: { required?: boolean } = {}): string | null {
  if (!text.trim()) return opts.required ? "This field is required." : null;
  try {
    JSON.parse(text);
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

/** Pretty-prints the JSON in `text`. Returns the formatted text, or an error. */
export function formatJsonText(text: string): { text: string } | { error: string } {
  try {
    return { text: JSON.stringify(JSON.parse(text), null, 2) };
  } catch (e) {
    return { error: (e as Error).message };
  }
}

/**
 * Monospace JSON editor field (UX: parse-on-blur validation with a red error
 * line, and a "Format" button that re-indents valid JSON). The parent owns the
 * raw text and the error state; submit-blocking is the parent's job (re-parse
 * on submit) — this component just surfaces the per-field error and the Format
 * affordance.
 */
export function JsonField({
  id,
  label,
  value,
  onChange,
  onBlur,
  onFormat,
  error,
  placeholder,
  rows = 8,
  required,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  onBlur: () => void;
  onFormat: () => void;
  error: string | null;
  placeholder: string;
  rows?: number;
  required?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>
          {label}
          {required && <span className="text-destructive"> *</span>}
        </Label>
        <Button type="button" size="sm" variant="ghost" onClick={onFormat}>
          Format
        </Button>
      </div>
      <Textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        rows={rows}
        placeholder={placeholder}
        spellCheck={false}
        aria-invalid={!!error}
        className={`font-mono text-xs${error ? " border-destructive" : ""}`}
      />
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
