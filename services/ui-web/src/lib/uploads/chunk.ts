/**
 * Pure chunking + chunk-PUT helpers for the resumable upload flow (ING-FR-040..042).
 * The PUT goes to a same-origin ui-web API route
 * (/api/uploads/{uploadId}/parts/{n}), never through GraphQL — see that route's
 * doc comment for why (raw binary body, GraphQL is JSON-only).
 */

export interface UploadedPart {
  n: number;
  etag: string;
  size: number;
}

/** Split a File into partSize-byte blobs. Parts are 1-indexed (ingestion-service's
 * PartManifestEntry requires n >= 1). A zero-byte file still yields one empty part
 * so the upload session has something to complete against. */
export function chunkFile(file: File, partSize: number): Blob[] {
  const size = Math.max(1, Math.floor(partSize));
  const chunks: Blob[] = [];
  for (let offset = 0; offset < file.size; offset += size) {
    chunks.push(file.slice(offset, offset + size));
  }
  if (chunks.length === 0) chunks.push(file.slice(0, 0));
  return chunks;
}

/** PUT one chunk to the ui-web proxy route and return the confirmed part. Re-PUTting
 * the same part with identical bytes is idempotent server-side (safe to retry). */
export async function putUploadPart(uploadId: string, n: number, blob: Blob): Promise<UploadedPart> {
  const res = await fetch(`/api/uploads/${encodeURIComponent(uploadId)}/parts/${n}`, {
    method: "PUT",
    body: blob,
  });
  const json: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(extractErrorMessage(json) ?? `chunk ${n} failed with status ${res.status}`);
  }
  const data = unwrapData(json);
  return {
    n: typeof data?.n === "number" ? data.n : n,
    etag: typeof data?.etag === "string" ? data.etag : "",
    size: typeof data?.size === "number" ? data.size : blob.size,
  };
}

function unwrapData(json: unknown): { n?: unknown; etag?: unknown; size?: unknown } | null {
  if (json && typeof json === "object") {
    if ("data" in json && (json as { data?: unknown }).data && typeof (json as { data?: unknown }).data === "object") {
      return (json as { data: Record<string, unknown> }).data;
    }
    return json as Record<string, unknown>;
  }
  return null;
}

function extractErrorMessage(json: unknown): string | null {
  if (!json || typeof json !== "object") return null;
  const err = (json as { error?: unknown }).error;
  if (typeof err === "string") return err;
  if (err && typeof err === "object" && typeof (err as { message?: unknown }).message === "string") {
    return (err as { message: string }).message;
  }
  return null;
}

/** Best-effort file format hint from the extension (ingestion-service accepts a
 * free-form string here; the ingestion pipeline validates it against the actual
 * bytes during profiling). */
export function guessFileFormat(fileName: string): string | undefined {
  const ext = fileName.split(".").pop()?.toLowerCase();
  return ext || undefined;
}

/** Strip the extension for a default dataset name suggestion. */
export function baseName(fileName: string): string {
  const idx = fileName.lastIndexOf(".");
  return idx > 0 ? fileName.slice(0, idx) : fileName;
}
