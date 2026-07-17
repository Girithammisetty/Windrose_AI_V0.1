"use client";
import { useMemo } from "react";
import { useCaseSearch } from "@/lib/graphql/hooks";
import { StatusChip } from "@/components/primitives/StatusChip";
import { useEmbedFrame } from "@/lib/embed/useEmbedFrame";
import { formatLocal } from "@/lib/utils";

/**
 * Headless embedded case queue (embed surface "cases"). Read-only list of the
 * workspace's cases, scoped by the embed token. No AppShell, no nav — drops
 * into a tenant's iframe. Data flows through the normal governed /api/graphql
 * path using the embed session token.
 */
export default function EmbeddedCasesPage() {
  useEmbedFrame();
  const query = useCaseSearch();
  const cases = useMemo(
    () => query.data?.pages.flatMap((p) => p.nodes) ?? [],
    [query.data],
  );

  return (
    <main id="main" className="min-h-screen bg-background p-4">
      <h1 className="mb-3 text-lg font-semibold tracking-tight">Cases</h1>
      {query.isLoading ? (
        <p className="p-8 text-center text-sm text-muted-foreground">Loading…</p>
      ) : query.isError ? (
        <p className="p-8 text-center text-sm text-destructive">
          Cases are unavailable.
        </p>
      ) : cases.length === 0 ? (
        <p className="p-8 text-center text-sm text-muted-foreground">No cases.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Case</th>
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Severity</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Due</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="px-3 py-2 font-mono text-xs">#{c.caseNumber ?? "—"}</td>
                  <td className="px-3 py-2">{c.title ?? c.urn}</td>
                  <td className="px-3 py-2">
                    {c.severity ? <StatusChip status={c.severity} /> : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {c.status ? <StatusChip status={c.status} /> : "—"}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {c.dueDate ? formatLocal(c.dueDate) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
