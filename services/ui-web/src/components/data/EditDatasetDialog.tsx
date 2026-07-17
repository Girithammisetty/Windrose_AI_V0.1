"use client";
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useUpdateDataset } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { t } from "@/lib/i18n/messages";

/**
 * Edit a dataset's catalog metadata (name + description). Datasets are created
 * via ingestion — there is no create dialog — so this is the tenant's only path
 * to correct a name or description after the fact (dataset-service PATCH
 * /datasets/{id}). Only fields that actually changed are sent; the backend
 * rejects a rename that collides with another dataset in the workspace (409),
 * surfaced inline. On success the caller's onSaved runs (e.g. show a banner);
 * the hook already invalidates the detail + list queries so the new name shows.
 */
export function EditDatasetDialog({
  open,
  onOpenChange,
  dataset,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  dataset: { id: string; name: string; description?: string | null };
  onSaved?: () => void;
}) {
  const [name, setName] = useState(dataset.name);
  const [description, setDescription] = useState(dataset.description ?? "");
  const [banner, setBanner] = useState<string | null>(null);
  const update = useUpdateDataset();

  // Reset the form to the dataset's current values every time it (re)opens.
  useEffect(() => {
    if (open) {
      setName(dataset.name);
      setDescription(dataset.description ?? "");
      setBanner(null);
    }
  }, [open, dataset.name, dataset.description]);

  const submit = () => {
    setBanner(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setBanner(t("datasets.editNameRequired"));
      return;
    }
    // Send only what changed (both fields optional on the backend).
    const nextDescription = description.trim();
    const currentDescription = (dataset.description ?? "").trim();
    const input: { id: string; name?: string; description?: string } = { id: dataset.id };
    if (trimmedName !== dataset.name) input.name = trimmedName;
    if (nextDescription !== currentDescription) input.description = nextDescription;
    if (input.name === undefined && input.description === undefined) {
      onOpenChange(false);
      return;
    }
    update.mutate(input, {
      onSuccess: () => {
        onOpenChange(false);
        onSaved?.();
      },
    });
  };

  const error = update.error instanceof GraphQLRequestError ? update.error : null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">{t("datasets.editTitle")}</Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="dataset-edit-name">{t("datasets.editName")}</Label>
              <Input
                id="dataset-edit-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("datasets.editNamePlaceholder")}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dataset-edit-description">{t("datasets.editDescription")}</Label>
              <Textarea
                id="dataset-edit-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("datasets.editDescriptionPlaceholder")}
                rows={3}
              />
            </div>
            {banner && <p className="text-xs text-muted-foreground">{banner}</p>}
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("action.cancel")}
              </Button>
              <Button type="submit" disabled={!name.trim() || update.isPending}>
                {update.isPending ? t("datasets.editSaving") : t("action.save")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
