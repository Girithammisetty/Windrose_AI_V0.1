/** Master cursor-pagination envelope (MASTER-FR-022). */
export interface Page<T> {
  data: T[];
  page: { next_cursor?: string | null; has_more: boolean };
}

/** Single-resource envelope used by some services (e.g. dataset-service). */
export interface Envelope<T> {
  data: T;
}

/** Coerce either `{data:{...}}` or a bare object into the resource. */
export function unwrap<T>(v: Envelope<T> | T): T {
  if (v && typeof v === "object" && "data" in (v as any) && !Array.isArray((v as any).data)) {
    return (v as Envelope<T>).data;
  }
  return v as T;
}
