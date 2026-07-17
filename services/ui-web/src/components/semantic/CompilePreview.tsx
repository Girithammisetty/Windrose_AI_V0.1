"use client";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { NoAccess } from "@/components/authz/NoAccess";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useCompileSemanticModel } from "@/lib/graphql/hooks";
import { useSession } from "@/lib/session/SessionContext";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { SemanticDefinitionDoc, SemanticCompileResult } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

/**
 * Compile the model's real definition into real SQL via semantic-service's
 * compiler (POST /compile) and, optionally, a real query-service dry-run cost
 * estimate. `draftVersionNo`, when set, previews the OPEN draft instead of the
 * published definition (BR-2).
 */
export function CompilePreview({
  modelId,
  doc,
  draftVersionNo,
}: {
  modelId: string;
  doc: SemanticDefinitionDoc;
  draftVersionNo?: number;
}) {
  const { can } = useCapabilities();
  const { workspaceId } = useSession();
  const [metrics, setMetrics] = useState<string[]>([]);
  const [dimensions, setDimensions] = useState<string[]>([]);
  const [withValidate, setWithValidate] = useState(false);
  const [result, setResult] = useState<SemanticCompileResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const compileMutation = useCompileSemanticModel();

  if (!can(FEATURE_GATES.compileSemanticModel)) {
    return <NoAccess />;
  }

  const toggle = (list: string[], set: (v: string[]) => void, name: string) => {
    set(list.includes(name) ? list.filter((n) => n !== name) : [...list, name]);
  };

  const run = () => {
    setError(null);
    setResult(null);
    compileMutation.mutate(
      {
        model: modelId,
        workspaceId,
        metrics,
        dimensions: dimensions.map((name) => ({ name })),
        draftVersionNo,
        validate: withValidate,
      },
      {
        onSuccess: setResult,
        onError: (e) => setError(e instanceof GraphQLRequestError ? e.message : "Compile failed"),
      },
    );
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <p className="mb-1 text-sm font-medium">{t("semantic.measures")}</p>
          <div className="space-y-1 rounded-md border p-2">
            {doc.measures.length === 0 && <p className="text-xs text-muted-foreground">{t("semantic.measure.none")}</p>}
            {doc.measures.map((m) => (
              <label key={m.name} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={metrics.includes(m.name)}
                  onChange={() => toggle(metrics, setMetrics, m.name)}
                />
                {m.name}
              </label>
            ))}
          </div>
        </div>
        <div>
          <p className="mb-1 text-sm font-medium">{t("semantic.dimensions")}</p>
          <div className="space-y-1 rounded-md border p-2">
            {doc.dimensions.length === 0 && <p className="text-xs text-muted-foreground">{t("semantic.dimension.none")}</p>}
            {doc.dimensions.map((d) => (
              <label key={d.name} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={dimensions.includes(d.name)}
                  onChange={() => toggle(dimensions, setDimensions, d.name)}
                />
                {d.name}
              </label>
            ))}
          </div>
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={withValidate} onChange={(e) => setWithValidate(e.target.checked)} />
        {t("semantic.preview.withCostEstimate")}
      </label>

      {metrics.length === 0 && <p className="text-xs text-muted-foreground">{t("semantic.preview.pickMetric")}</p>}

      <Button onClick={run} disabled={metrics.length === 0 || compileMutation.isPending}>
        {compileMutation.isPending ? <Loader2 className="animate-spin" /> : null} {t("semantic.preview.run")}
      </Button>

      {error && (
        <p role="alert" className="text-sm text-destructive" data-testid="compile-error">
          {error}
        </p>
      )}

      {result && (
        <div className="space-y-3">
          <div>
            <p className="mb-1 text-sm font-medium">{t("semantic.preview.sql")}</p>
            <pre className="overflow-x-auto rounded-md border bg-muted/40 p-3 text-xs" data-testid="compiled-sql">
              {result.sql}
            </pre>
          </div>
          <div>
            <p className="mb-1 text-sm font-medium">{t("semantic.preview.schema")}</p>
            <table className="w-full text-xs">
              <tbody>
                {result.outputSchema.map((c) => (
                  <tr key={c.name} className="border-b last:border-0">
                    <td className="py-1 pr-2 font-medium">{c.name}</td>
                    <td className="py-1 pr-2 text-muted-foreground">{c.type}</td>
                    <td className="py-1 text-muted-foreground">{c.role}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {withValidate && (
            <p role="status" className="text-xs" data-testid="validation-status">
              {result.validationAvailable
                ? result.validationValid
                  ? t("semantic.preview.validationOk")
                  : t("semantic.preview.validationFailed", { reason: result.validationMessage ?? "" })
                : t("semantic.preview.validationUnavailable", { reason: result.validationMessage ?? "unknown" })}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
