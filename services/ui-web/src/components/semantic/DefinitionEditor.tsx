"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Loader2, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { EntitiesSection } from "./EntitiesSection";
import { DimensionsSection } from "./DimensionsSection";
import { MeasuresSection } from "./MeasuresSection";
import { JoinPathsSection } from "./JoinPathsSection";
import { graphqlRequest, GraphQLRequestError } from "@/lib/graphql/client";
import { qk } from "@/lib/graphql/keys";
import * as ops from "@/lib/graphql/operations";
import { useUpdateSemanticModelDraft, useSubmitSemanticModelVersion } from "@/lib/graphql/hooks";
import {
  normalizeDefinition,
  datasetIdFromUrn,
  parseValidationDetails,
  groupProblemsByObject,
} from "@/lib/semantic/definition";
import type { SemanticDefinitionDoc, SemanticModelVersion, DatasetColumn } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

const SAVE_DEBOUNCE_MS = 900;

/**
 * The draft definition editor: entities/dimensions/measures/join-paths sections
 * bound to real dataset columns, with a debounced live save (structural/expr
 * validation happens on every save — SEM-FR-006) and a Submit-for-review action
 * that runs the REAL full validation (bindings against real dataset columns).
 * A failed submit does NOT transition state server-side, so it doubles safely
 * as a "validate the whole definition" action — no fake endpoint.
 */
export function DefinitionEditor({
  modelId,
  version,
  onSubmitted,
}: {
  modelId: string;
  version: SemanticModelVersion;
  onSubmitted: () => void;
}) {
  const readOnly = version.status !== "DRAFT" && version.status !== "REJECTED";
  const [doc, setDoc] = useState<SemanticDefinitionDoc>(
    () => normalizeDefinition(version.definitionJson),
  );
  // Re-seed local state whenever we switch to a different version (e.g. after
  // opening a new draft, or after a submit rolls back to draft with fresh data).
  const loadedVersionKey = useRef(`${version.modelId}:${version.versionNo}`);
  useEffect(() => {
    const key = `${version.modelId}:${version.versionNo}`;
    if (key !== loadedVersionKey.current) {
      loadedVersionKey.current = key;
      setDoc(normalizeDefinition(version.definitionJson));
    }
  }, [version]);

  const saveMutation = useUpdateSemanticModelDraft(modelId);
  const submitMutation = useSubmitSemanticModelVersion(modelId);

  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [submitProblems, setSubmitProblems] = useState<ReturnType<typeof parseValidationDetails>>([]);
  const [submitBanner, setSubmitBanner] = useState<string | null>(null);

  // Debounced autosave: fires SAVE_DEBOUNCE_MS after the last edit. A save-time
  // 422 (structural/expr) is real and shown immediately; it does not clear the
  // author's in-progress edits.
  const skipNextSave = useRef(true);
  useEffect(() => {
    if (readOnly) return;
    if (skipNextSave.current) {
      // Don't save on initial mount / version switch — only on real edits.
      skipNextSave.current = false;
      return;
    }
    const timer = setTimeout(() => {
      setSaveError(null);
      saveMutation.mutate(
        { versionNo: version.versionNo, definition: doc },
        {
          onSuccess: () => setSavedAt(Date.now()),
          onError: (e) => setSaveError(e instanceof GraphQLRequestError ? e.message : "Save failed"),
        },
      );
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, readOnly]);
  // Reset the "skip" guard whenever the loaded version changes.
  useEffect(() => {
    skipNextSave.current = true;
  }, [version.modelId, version.versionNo]);

  // Real columns for every referenced dataset, keyed by entity name — the
  // dimension/measure column pickers bind to these instead of free text.
  const datasetIds = useMemo(
    () => Array.from(new Set(doc.entities.map((e) => datasetIdFromUrn(e.dataset_urn)).filter(Boolean))) as string[],
    [doc.entities],
  );
  const schemaQueries = useQueries({
    queries: datasetIds.map((id) => ({
      queryKey: qk.datasetSchema(id),
      queryFn: () => graphqlRequest<ops.DatasetSchemaResult>(ops.DATASET_SCHEMA, { datasetId: id }).then((r) => r.datasetSchema),
      staleTime: 60_000,
    })),
  });
  const columnsByEntity = useMemo(() => {
    const byDatasetId: Record<string, DatasetColumn[]> = {};
    datasetIds.forEach((id, idx) => {
      byDatasetId[id] = schemaQueries[idx]?.data ?? [];
    });
    const out: Record<string, DatasetColumn[]> = {};
    for (const entity of doc.entities) {
      const id = datasetIdFromUrn(entity.dataset_urn);
      out[entity.name] = id ? (byDatasetId[id] ?? []) : [];
    }
    return out;
  }, [doc.entities, datasetIds, schemaQueries]);

  const errorsByObject = useMemo(() => groupProblemsByObject(submitProblems), [submitProblems]);

  const onSubmit = () => {
    setSubmitBanner(null);
    setSubmitProblems([]);
    submitMutation.mutate(version.versionNo, {
      onSuccess: () => {
        setSubmitBanner(t("semantic.submitted"));
        onSubmitted();
      },
      onError: (e) => {
        if (e instanceof GraphQLRequestError) {
          const details = e.raw[0]?.extensions?.details;
          const problems = parseValidationDetails(details);
          if (problems.length > 0) {
            setSubmitProblems(problems);
            setSubmitBanner(t("semantic.submitBlockedHint"));
          } else {
            setSubmitBanner(e.message);
          }
        } else {
          setSubmitBanner("Submit failed");
        }
      },
    });
  };

  return (
    <div className="space-y-6">
      {readOnly && (
        <div role="status" className="rounded-md border bg-muted/40 px-3 py-2 text-sm">
          {t("semantic.readOnlyHint", { status: version.status })}
        </div>
      )}

      <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
        {saveMutation.isPending ? (
          <>
            <Loader2 className="size-3 animate-spin" /> {t("semantic.saving")}
          </>
        ) : saveError ? (
          <span className="text-destructive" role="alert" data-testid="save-error">
            {saveError}
          </span>
        ) : savedAt ? (
          <>
            <CheckCircle2 className="size-3" /> {t("semantic.saved")}
          </>
        ) : null}
      </div>

      <EntitiesSection doc={doc} onChange={setDoc} errors={errorsByObject} readOnly={readOnly} />
      <DimensionsSection doc={doc} onChange={setDoc} columnsByEntity={columnsByEntity} errors={errorsByObject} readOnly={readOnly} />
      <MeasuresSection doc={doc} onChange={setDoc} columnsByEntity={columnsByEntity} errors={errorsByObject} readOnly={readOnly} />
      <JoinPathsSection doc={doc} onChange={setDoc} errors={errorsByObject} readOnly={readOnly} />

      {submitProblems.length > 0 && (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3">
          <p className="mb-1 text-sm font-medium text-destructive">{t("semantic.errors.title", { count: submitProblems.length })}</p>
          <ul className="space-y-0.5 text-xs text-destructive">
            {submitProblems.map((p, i) => (
              <li key={i}>
                {p.kind}
                {p.name ? `/${p.name}` : ""}: {p.problem}
              </li>
            ))}
          </ul>
        </div>
      )}
      {submitBanner && submitProblems.length === 0 && (
        <p role="status" className="text-sm text-muted-foreground" data-testid="submit-banner">
          {submitBanner}
        </p>
      )}

      {!readOnly && (
        <div className="flex justify-end">
          <Can gate={FEATURE_GATES.submitSemanticModelVersion}>
            <Button onClick={onSubmit} disabled={submitMutation.isPending}>
              {submitMutation.isPending ? t("semantic.submitting") : t("semantic.submit")}
            </Button>
          </Can>
        </div>
      )}
    </div>
  );
}
