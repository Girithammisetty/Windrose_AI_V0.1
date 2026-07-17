"use client";
import { UPLOAD_FORMATS } from "@/lib/uploads/formats";
import { cn } from "@/lib/utils";

/** Explicit CSV / JSON / Parquet / Avro / XML selector for the upload wizard. */
export function FormatPicker({
  value,
  onChange,
  disabled,
}: {
  value: string | null;
  onChange: (key: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5" role="radiogroup" aria-label="File format">
      {UPLOAD_FORMATS.map((f) => (
        <button
          key={f.key}
          type="button"
          role="radio"
          aria-checked={value === f.key}
          data-format={f.key}
          disabled={disabled}
          onClick={() => onChange(f.key)}
          className={cn(
            "rounded-lg border p-3 text-center text-sm transition-colors hover:border-primary disabled:pointer-events-none disabled:opacity-60",
            value === f.key ? "border-primary bg-primary/10 font-medium" : "border-muted-foreground/25",
          )}
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}
