/**
 * File-upload data-source formats. The `key` is the exact `fileFormat` string
 * ingestion-service validates against decode.FILE_FORMATS (csv/json/parquet/
 * avro/xml). Detection maps a file extension to a format so the wizard can
 * pre-select; the user can always override via the picker.
 */

export interface UploadFormat {
  key: string;
  label: string;
  extensions: string[];
  hint: string;
}

export const UPLOAD_FORMATS: UploadFormat[] = [
  { key: "csv", label: "CSV", extensions: ["csv"], hint: "Comma-separated values with a header row." },
  { key: "json", label: "JSON", extensions: ["json"], hint: "A top-level JSON array of objects." },
  { key: "parquet", label: "Parquet", extensions: ["parquet", "pq"], hint: "Columnar Apache Parquet file." },
  { key: "avro", label: "Avro", extensions: ["avro"], hint: "Apache Avro object-container file." },
  { key: "xml", label: "XML", extensions: ["xml"], hint: "One row per repeated element under the document root." },
];

/** Best-effort format from the file extension, or null when unrecognised. */
export function detectUploadFormat(fileName: string): string | null {
  const ext = fileName.split(".").pop()?.toLowerCase();
  if (!ext) return null;
  return UPLOAD_FORMATS.find((f) => f.extensions.includes(ext))?.key ?? null;
}

export function uploadFormat(key: string | null): UploadFormat | undefined {
  return key ? UPLOAD_FORMATS.find((f) => f.key === key) : undefined;
}

export const ACCEPT_ATTR = UPLOAD_FORMATS.flatMap((f) => f.extensions.map((e) => `.${e}`)).join(",");

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n;
  let i = -1;
  do {
    v /= 1024;
    i++;
  } while (v >= 1024 && i < units.length - 1);
  return `${v.toFixed(1)} ${units[i]}`;
}
