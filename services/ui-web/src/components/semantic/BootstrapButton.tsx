"use client";
import { useEffect, useState } from "react";
import { Loader2, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useBootstrapSemanticModel, useSemanticOperation } from "@/lib/graphql/hooks";
import { t } from "@/lib/i18n/messages";

/**
 * Auto-draft the model's definition from its entities' dataset schemas
 * (semantic-service POST /models/{id}/bootstrap → 202 + GET /operations/{id}
 * polling, SEM-FR-020). On completion the open draft's definition has been
 * rewritten server-side — `onCompleted` refetches it into the editor.
 */
export function BootstrapButton({
  modelId,
  onCompleted,
  onNotice,
}: {
  modelId: string;
  onCompleted: () => void;
  onNotice: (msg: string) => void;
}) {
  const bootstrap = useBootstrapSemanticModel();
  const [operationId, setOperationId] = useState<string | null>(null);
  const operation = useSemanticOperation(operationId);

  const status = operation.data?.status;
  useEffect(() => {
    if (!operationId || !status) return;
    if (status === "completed") {
      setOperationId(null);
      onNotice(t("semantic.bootstrapDone"));
      onCompleted();
    } else if (status === "failed") {
      setOperationId(null);
      onNotice(t("semantic.bootstrapFailed"));
    }
  }, [operationId, status, onCompleted, onNotice]);

  const start = () =>
    bootstrap.mutate(
      { modelId },
      {
        onSuccess: (op) => {
          if (op.status === "completed") {
            // The deployment ran the bootstrap synchronously inside the 202.
            onNotice(t("semantic.bootstrapDone"));
            onCompleted();
          } else if (op.status === "failed") {
            onNotice(t("semantic.bootstrapFailed"));
          } else {
            setOperationId(op.operationId);
            onNotice(t("semantic.bootstrapStarted"));
          }
        },
        onError: (e) => onNotice((e as Error).message),
      },
    );

  const busy = bootstrap.isPending || !!operationId;

  return (
    <Can gate={FEATURE_GATES.bootstrapSemanticModel}>
      <Button variant="outline" size="sm" onClick={start} disabled={busy} title={t("semantic.bootstrapHint")}>
        {busy ? <Loader2 className="animate-spin" /> : <Wand2 />}
        {busy ? t("semantic.bootstrapping") : t("semantic.bootstrap")}
      </Button>
    </Can>
  );
}
