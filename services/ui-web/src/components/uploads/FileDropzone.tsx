"use client";
import { useCallback, useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";
import { ACCEPT_ATTR, formatBytes } from "@/lib/uploads/formats";
import { t } from "@/lib/i18n/messages";

/** Drag-and-drop (or click-to-browse) single-file picker for the upload wizard. */
export function FileDropzone({
  file,
  onFile,
  disabled,
}: {
  file: File | null;
  onFile: (f: File | null) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const pick = () => inputRef.current?.click();
  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (disabled) return;
      const f = e.dataTransfer.files?.[0];
      if (f) onFile(f);
    },
    [onFile, disabled],
  );

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={t("upload.dropzone.aria")}
      data-testid="file-dropzone"
      onClick={pick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          pick();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center text-sm transition-colors",
        dragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-primary/50",
        disabled && "pointer-events-none opacity-60",
      )}
    >
      <UploadCloud className="size-8 text-muted-foreground" aria-hidden />
      {file ? (
        <div>
          <p className="font-medium">{file.name}</p>
          <p className="text-xs text-muted-foreground">{formatBytes(file.size)}</p>
        </div>
      ) : (
        <div>
          <p className="font-medium">{t("upload.dropzone.title")}</p>
          <p className="text-xs text-muted-foreground">{t("upload.dropzone.hint")}</p>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        aria-label={t("upload.fileInputAria")}
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}
