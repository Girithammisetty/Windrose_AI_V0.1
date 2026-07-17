"use client";
import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import { t } from "@/lib/i18n/messages";

/**
 * Destructive-action confirmation (UI-FR-019). For irreversible ops a
 * typed-name gate is required (`confirmPhrase`).
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  confirmPhrase,
  destructive,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  confirmPhrase?: string;
  destructive?: boolean;
  onConfirm: () => void;
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const gateOk = !confirmPhrase || typed === confirmPhrase;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none">
          <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
          {description && (
            <Dialog.Description asChild>
              <div className="mt-2 text-sm text-muted-foreground">{description}</div>
            </Dialog.Description>
          )}
          {children}
          {confirmPhrase && (
            <div className="mt-3">
              <label className="text-xs text-muted-foreground">
                Type <span className="font-mono font-semibold">{confirmPhrase}</span> to confirm
              </label>
              <Input value={typed} onChange={(e) => setTyped(e.target.value)} className="mt-1" />
            </div>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t("action.cancel")}
            </Button>
            <Button
              variant={destructive ? "destructive" : "default"}
              disabled={!gateOk}
              onClick={() => {
                onConfirm();
                setTyped("");
              }}
            >
              {confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
