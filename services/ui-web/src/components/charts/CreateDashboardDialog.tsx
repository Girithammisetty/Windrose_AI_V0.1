"use client";
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateDashboard } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { t } from "@/lib/i18n/messages";

/**
 * Create-dashboard flow. Workspace is sourced from the JWT server-side, so the
 * form only collects a name (+ optional module). On success it invokes onCreated
 * with the new dashboard id so the caller can navigate to it.
 */
export function CreateDashboardDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (id: string) => void;
}) {
  // chart-service requires module ∈ these three (DB CHECK constraint); default to
  // the general "insights" board so the form is valid without guessing.
  const MODULES = ["insights", "case_management", "inspector"] as const;
  const [name, setName] = useState("");
  const [module, setModule] = useState<string>("insights");
  const [banner, setBanner] = useState<string | null>(null);
  const createMutation = useCreateDashboard();

  useEffect(() => {
    if (open) {
      setName("");
      setModule("insights");
      setBanner(null);
    }
  }, [open]);

  const submit = () => {
    setBanner(null);
    if (!name.trim()) {
      setBanner(t("charts.nameRequired"));
      return;
    }
    createMutation.mutate(
      { name: name.trim(), module },
      { onSuccess: (r) => onCreated(r.createDashboard.id) },
    );
  };

  const error = createMutation.error instanceof GraphQLRequestError ? createMutation.error : null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">{t("dashboards.createTitle")}</Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="dashboard-name">{t("dashboards.name")}</Label>
              <Input
                id="dashboard-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("dashboards.namePlaceholder")}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dashboard-module">{t("dashboards.module")}</Label>
              <select
                id="dashboard-module"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={module}
                onChange={(e) => setModule(e.target.value)}
              >
                {MODULES.map((m) => (
                  <option key={m} value={m}>
                    {m.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
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
              <Button type="submit" disabled={!name.trim() || createMutation.isPending}>
                {createMutation.isPending ? t("dashboards.creating") : t("dashboards.create")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
