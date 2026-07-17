"use client";
import { useState } from "react";
import { Loader2, Table2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useConnectionPreview } from "@/lib/graphql/hooks";
import { t } from "@/lib/i18n/messages";

/**
 * Live sample-rows preview from a SAVED connection (ingestion-service POST
 * /connections/{id}/preview, ING-FR-005): ≤100 real rows, never persisted.
 * Exactly one of table/path/query is sent — whichever the user filled last.
 */
export function ConnectionPreviewPanel({ connectionId }: { connectionId: string }) {
  const [targetKind, setTargetKind] = useState<"table" | "path" | "query">("query");
  const [target, setTarget] = useState("");
  const preview = useConnectionPreview();

  const run = () => {
    if (!target.trim()) return;
    preview.mutate({ id: connectionId, input: { [targetKind]: target.trim(), limit: 100 } });
  };

  const data = preview.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t("connections.previewTitle")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">{t("connections.previewHint")}</p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1.5">
            <Label htmlFor="preview-kind">{t("connections.previewTargetHint")}</Label>
            <select
              id="preview-kind"
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={targetKind}
              onChange={(e) => setTargetKind(e.target.value as "table" | "path" | "query")}
            >
              <option value="query">{t("connections.previewQuery")}</option>
              <option value="table">{t("connections.previewTable")}</option>
              <option value="path">{t("connections.previewPath")}</option>
            </select>
          </div>
          <Input
            aria-label="Preview target"
            className="min-w-64 flex-1 font-mono text-xs"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder={targetKind === "query" ? "SELECT * FROM claims LIMIT 10" : targetKind}
          />
          <Button onClick={run} disabled={preview.isPending || !target.trim()}>
            {preview.isPending ? (
              <>
                <Loader2 className="animate-spin" /> {t("connections.previewLoading")}
              </>
            ) : (
              t("connections.previewRun")
            )}
          </Button>
        </div>

        {preview.isError && (
          <p role="alert" className="text-sm text-destructive" data-testid="preview-error">
            {(preview.error as Error).message}
          </p>
        )}

        {data && data.rows.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("connections.previewEmpty")}</p>
        )}

        {data && data.rows.length > 0 && (
          <div className="overflow-x-auto rounded-md border" data-testid="preview-rows">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b bg-muted/40 text-left">
                  {data.columns.map((c) => (
                    <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data.rows as Record<string, unknown>[]).map((row, i) => (
                  <tr key={i} className="border-b last:border-0">
                    {data.columns.map((c) => (
                      <td key={c} className="whitespace-nowrap px-3 py-1.5 font-mono text-xs">
                        {row?.[c] == null ? "∅" : typeof row[c] === "object" ? JSON.stringify(row[c]) : String(row[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!data && !preview.isError && !preview.isPending && (
          <div className="flex items-center gap-2 rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            <Table2 className="size-4" /> {t("connections.previewTargetHint")}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
