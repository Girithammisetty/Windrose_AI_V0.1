"use client";
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateSavedQuery, useUpdateSavedQuery } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { SavedQuery, SavedQueryInput, VariableDeclInput } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

const splitCsv = (s: string): string[] =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/**
 * Save/edit a governed saved query (query-service POST/PATCH /queries).
 * Editing always opens a NEW immutable version server-side. Variables are
 * authored as the raw JSON declaration array (typed per QRY-FR-002) — the
 * service's 422 per-variable problems surface verbatim in the error box.
 */
export function SavedQueryDialog({
  open,
  onOpenChange,
  initialSql,
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  /** SQL prefill for create mode (the editor's current text). */
  initialSql: string;
  /** When set, the dialog PATCHes this query instead of creating one. */
  editing?: SavedQuery | null;
  onSaved: (q: SavedQuery) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [modules, setModules] = useState("");
  const [tags, setTags] = useState("");
  const [sql, setSql] = useState("");
  const [variablesText, setVariablesText] = useState("");
  const [banner, setBanner] = useState<string | null>(null);

  const createMutation = useCreateSavedQuery();
  const updateMutation = useUpdateSavedQuery();
  const pending = createMutation.isPending || updateMutation.isPending;

  useEffect(() => {
    if (!open) return;
    setBanner(null);
    createMutation.reset();
    updateMutation.reset();
    if (editing) {
      setName(editing.name);
      setDescription(editing.description ?? "");
      setModules((editing.moduleNames ?? []).join(", "));
      setTags((editing.tags ?? []).join(", "));
      setSql(editing.sqlText ?? "");
      const vars = editing.variables;
      setVariablesText(Array.isArray(vars) && vars.length > 0 ? JSON.stringify(vars, null, 2) : "");
    } else {
      setName("");
      setDescription("");
      setModules("");
      setTags("");
      setSql(initialSql);
      setVariablesText("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open/editing change
  }, [open, editing]);

  const submit = () => {
    setBanner(null);
    if (!name.trim()) {
      setBanner(t("queries.nameRequired"));
      return;
    }
    const moduleNames = splitCsv(modules);
    if (moduleNames.length === 0) {
      setBanner(t("queries.modulesRequired"));
      return;
    }
    let variables: VariableDeclInput[] | undefined;
    if (variablesText.trim()) {
      try {
        const parsed: unknown = JSON.parse(variablesText);
        if (!Array.isArray(parsed)) throw new Error("not an array");
        variables = parsed as VariableDeclInput[];
      } catch {
        setBanner(t("queries.variablesInvalid"));
        return;
      }
    } else {
      variables = [];
    }
    const input: SavedQueryInput = {
      name: name.trim(),
      description: description.trim() || undefined,
      moduleNames,
      tags: splitCsv(tags),
      sqlText: sql,
      variables,
    };
    if (editing) {
      updateMutation.mutate({ id: editing.id, input }, { onSuccess: (q) => onSaved(q) });
    } else {
      createMutation.mutate(input, { onSuccess: (q) => onSaved(q) });
    }
  };

  const error =
    (createMutation.error instanceof GraphQLRequestError ? createMutation.error : null) ??
    (updateMutation.error instanceof GraphQLRequestError ? updateMutation.error : null) ??
    ((createMutation.error ?? updateMutation.error) as Error | null);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">
            {editing
              ? t("queries.editTitle", { version: (editing.versionNo ?? 0) + 1 })
              : t("queries.saveTitle")}
          </Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="sq-name">{t("queries.name")}</Label>
              <Input
                id="sq-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("queries.namePlaceholder")}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sq-description">{t("queries.description")}</Label>
              <Input id="sq-description" value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sq-modules">{t("queries.modules")}</Label>
              <Input
                id="sq-modules"
                value={modules}
                onChange={(e) => setModules(e.target.value)}
                placeholder={t("queries.modulesPlaceholder")}
              />
              <p className="text-xs text-muted-foreground">{t("queries.modulesHint")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sq-tags">{t("queries.tags")}</Label>
              <Input
                id="sq-tags"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder={t("queries.tagsPlaceholder")}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sq-sql">SQL</Label>
              <Textarea
                id="sq-sql"
                rows={5}
                className="font-mono text-xs"
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                spellCheck={false}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sq-variables">{t("queries.variables")}</Label>
              <Textarea
                id="sq-variables"
                rows={3}
                className="font-mono text-xs"
                value={variablesText}
                onChange={(e) => setVariablesText(e.target.value)}
                spellCheck={false}
              />
              <p className="text-xs text-muted-foreground">{t("queries.variablesHint")}</p>
            </div>
            {banner && <p className="text-xs text-destructive">{banner}</p>}
            {error && (
              <p role="alert" className="whitespace-pre-wrap text-xs text-destructive" data-testid="mutation-error">
                {error.message}
                {error instanceof GraphQLRequestError && error.raw?.[0]?.extensions?.details
                  ? `\n${JSON.stringify(error.raw[0].extensions.details)}`
                  : ""}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("action.cancel")}
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? t("connections.saving") : t("queries.save")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
