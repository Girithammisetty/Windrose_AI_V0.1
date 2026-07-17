"use client";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/authz/Can";
import { Textarea, Label } from "@/components/ui/primitives";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useSession } from "@/lib/session/SessionContext";
import { useApproveSemanticModelVersion, useRejectSemanticModelVersion } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { SemanticModelVersion } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

/**
 * The governance review action bar for an IN_REVIEW version: Approve
 * (publishes) / Reject (note required). Gated on semantic.model.approve AND
 * client-side hidden when the viewer authored this version — the server
 * enforces the four-eyes rule regardless (SEM-FR-007); this only avoids a
 * guaranteed-403 click.
 */
export function ReviewActions({
  modelId,
  version,
  onDecided,
}: {
  modelId: string;
  version: SemanticModelVersion;
  onDecided: () => void;
}) {
  const { userId } = useSession();
  const { can } = useCapabilities();
  const isAuthor = !!version.submittedBy && version.submittedBy === userId;

  const approveMutation = useApproveSemanticModelVersion(modelId);
  const rejectMutation = useRejectSemanticModelVersion(modelId);

  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  if (version.status !== "IN_REVIEW") return null;

  const onApprove = () => {
    setError(null);
    approveMutation.mutate(
      { versionNo: version.versionNo },
      {
        onSuccess: () => {
          setBanner(t("semantic.approved"));
          onDecided();
        },
        onError: (e) => setError(e instanceof GraphQLRequestError ? e.message : "Approve failed"),
      },
    );
  };

  const onReject = () => {
    setError(null);
    if (!note.trim()) {
      setError(t("semantic.rejectReasonRequired"));
      return;
    }
    rejectMutation.mutate(
      { versionNo: version.versionNo, note: note.trim() },
      {
        onSuccess: () => {
          setBanner(t("semantic.rejected"));
          setRejecting(false);
          onDecided();
        },
        onError: (e) => setError(e instanceof GraphQLRequestError ? e.message : "Reject failed"),
      },
    );
  };

  return (
    <div className="space-y-3 rounded-md border p-3" data-testid="review-actions">
      <p className="text-sm font-medium">{t("semantic.reviewVersion", { version: version.versionNo })}</p>
      {version.submittedBy && <p className="text-xs text-muted-foreground">{t("semantic.submittedBy", { who: version.submittedBy })}</p>}

      {isAuthor && can(FEATURE_GATES.approveSemanticModelVersion) && (
        <p className="text-xs text-muted-foreground" role="status">
          {t("semantic.cannotApproveOwn")}
        </p>
      )}

      {!isAuthor && (
        <Can gate={FEATURE_GATES.approveSemanticModelVersion}>
          {!rejecting ? (
            <div className="flex gap-2">
              <Button onClick={onApprove} disabled={approveMutation.isPending}>
                {approveMutation.isPending ? <Loader2 className="animate-spin" /> : null} {t("semantic.approve")}
              </Button>
              <Button variant="outline" onClick={() => setRejecting(true)}>
                {t("semantic.reject")}
              </Button>
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="reject-note">{t("semantic.rejectReasonPlaceholder")}</Label>
              <Textarea id="reject-note" value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
              <div className="flex gap-2">
                <Button variant="destructive" onClick={onReject} disabled={rejectMutation.isPending}>
                  {rejectMutation.isPending ? t("semantic.rejecting") : t("semantic.reject")}
                </Button>
                <Button variant="ghost" onClick={() => setRejecting(false)}>
                  {t("action.cancel")}
                </Button>
              </div>
            </div>
          )}
        </Can>
      )}

      {error && (
        <p role="alert" className="text-sm text-destructive" data-testid="review-error">
          {error}
        </p>
      )}
      {banner && (
        <p role="status" className="text-sm text-muted-foreground">
          {banner}
        </p>
      )}
    </div>
  );
}
