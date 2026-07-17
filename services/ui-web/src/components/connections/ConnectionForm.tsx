"use client";
import { useState } from "react";
import { CheckCircle2, XCircle, Loader2 } from "lucide-react";
import type { ConnectorType, ConnectorField, DataConnection, ConnectionTestResult } from "@/lib/graphql/types";
import { Input, Textarea, Label, Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateConnection, useTestConnection, useUpdateConnection } from "@/lib/graphql/hooks";
import { collect, defaultValues, type FormValues } from "@/lib/connections/form";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { t } from "@/lib/i18n/messages";
import { cn } from "@/lib/utils";

/** Seed edit-mode values: schema defaults overlaid with the SAVED config.
 * Secrets stay blank — they are write-only (masked on read); blank = keep. */
function editValues(fields: ConnectorField[], config: Record<string, unknown>): FormValues {
  const v = defaultValues(fields);
  for (const f of fields) {
    if (f.secret) continue;
    const cur = config[f.name];
    if (cur === undefined || cur === null) continue;
    if (f.type === "boolean") v[f.name] = cur === true;
    else if (f.type === "object" || f.type === "array") v[f.name] = JSON.stringify(cur);
    else v[f.name] = String(cur);
  }
  return v;
}

/**
 * Connection config form generated from the connector type's field schema —
 * used by BOTH the New Connection flow (create) and the edit page (`editing`
 * set). Right widget per field type, required/validation derived from the
 * schema, secret fields as Vault-backed password inputs (write-only: on edit,
 * blank keeps the stored value; entered keys merge over Vault). Create mode
 * offers a live adhoc Test button; edit saves re-probe server-side (PATCH
 * probes on config/secret change and 424s on failure).
 */
export function ConnectionForm({
  type,
  editing,
  onSaved,
  onCancel,
}: {
  type: ConnectorType;
  /** When set the form PATCHes this saved connection instead of creating one. */
  editing?: DataConnection | null;
  onSaved: (conn: DataConnection) => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(editing?.name ?? "");
  // Decision write-back (INS-FR-061) delivers over an `outgoing` connection;
  // only postgres (db_upsert) and http_api (http_post) have a real executor
  // server-side, so the picker only appears for those types — offering it
  // elsewhere would let someone configure a target that can never deliver.
  const supportsWriteback = type.connectorType === "postgres" || type.connectorType === "http_api";
  const [trafficDirection, setTrafficDirection] = useState(editing?.trafficDirection ?? "incoming");
  const [values, setValues] = useState<FormValues>(() =>
    editing
      ? editValues(type.fields, (editing.config ?? {}) as Record<string, unknown>)
      : defaultValues(type.fields),
  );
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [nameError, setNameError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);

  const testMutation = useTestConnection();
  const createMutation = useCreateConnection();
  const updateMutation = useUpdateConnection();

  const setField = (n: string, v: string | boolean) => {
    setValues((prev) => ({ ...prev, [n]: v }));
    setTestResult(null); // config changed → previous probe result is stale
  };

  const runTest = () => {
    const { ok, errors, config, secrets } = collect(type.fields, values);
    setFieldErrors(errors);
    if (!ok) return;
    setTestResult(null);
    testMutation.mutate(
      { type: type.connectorType, config, secrets },
      { onSuccess: (r) => setTestResult(r) },
    );
  };

  const runSave = () => {
    const { ok, errors, config, secrets } = collect(type.fields, values);
    const nErr = name.trim() ? null : t("connections.nameRequired");
    setFieldErrors(errors);
    setNameError(nErr);
    if (!ok || nErr) return;
    if (editing) {
      updateMutation.mutate(
        {
          id: editing.id,
          input: {
            name: name.trim(),
            config,
            // Only the secrets the user actually re-entered — the service
            // merges them over Vault; omitted keys are preserved (US-6).
            secrets: Object.keys(secrets).length ? secrets : undefined,
          },
        },
        { onSuccess: (r) => onSaved(r) },
      );
      return;
    }
    createMutation.mutate(
      {
        name: name.trim(),
        type: type.connectorType,
        config,
        secrets: Object.keys(secrets).length ? secrets : undefined,
        trafficDirection: supportsWriteback ? trafficDirection : undefined,
      },
      { onSuccess: (r) => onSaved(r.createConnection) },
    );
  };

  const rawSaveError = editing ? updateMutation.error : createMutation.error;
  const saveError = rawSaveError instanceof GraphQLRequestError ? rawSaveError : null;
  const savePending = editing ? updateMutation.isPending : createMutation.isPending;

  return (
    <form
      className="max-w-2xl space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        runSave();
      }}
      aria-label={`${type.displayName} connection`}
    >
      <div className="space-y-1.5">
        <Label htmlFor="conn-name">{t("connections.name")}</Label>
        <Input
          id="conn-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Prod Warehouse"
          aria-invalid={!!nameError}
          aria-describedby={nameError ? "conn-name-error" : undefined}
        />
        {nameError && (
          <p id="conn-name-error" className="text-xs text-destructive">
            {nameError}
          </p>
        )}
      </div>

      {editing && (
        <p className="text-xs text-muted-foreground" data-testid="secret-keep-hint">
          {t("connections.secretKeep")}
        </p>
      )}

      {!editing && supportsWriteback && (
        <div className="space-y-1.5">
          <Label htmlFor="conn-direction">Traffic direction</Label>
          <select
            id="conn-direction"
            className="h-9 w-full max-w-xs rounded-md border bg-background px-3 text-sm"
            value={trafficDirection}
            onChange={(e) => setTrafficDirection(e.target.value)}
          >
            <option value="incoming">Incoming — reads data in</option>
            <option value="outgoing">Outgoing — a decision write-back target</option>
            <option value="both">Both</option>
          </select>
          <p className="text-xs text-muted-foreground">
            Outgoing/both makes this connection selectable as a system-of-record target for decision write-backs.
          </p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {type.fields.map((f) => (
          <Field
            key={f.name}
            field={f}
            value={values[f.name]}
            error={fieldErrors[f.name]}
            onChange={(v) => setField(f.name, v)}
          />
        ))}
      </div>

      {testResult && <TestResultBanner result={testResult} />}
      {testMutation.error instanceof GraphQLRequestError && (
        <p role="alert" className="text-sm text-destructive">
          {testMutation.error.message}
        </p>
      )}
      {saveError && (
        <p role="alert" className="text-sm text-destructive" data-testid="save-error">
          {saveError.code === "CONNECTION_TEST_FAILED"
            ? `${t("connections.testFailed")}: ${(saveError.raw[0]?.extensions?.details as { error_category?: string } | undefined)?.error_category ?? saveError.message}`
            : saveError.message}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-2">
        {/* Edit mode has no adhoc test: stored secrets never reach the browser,
            so an adhoc probe couldn't include them — the PATCH itself live-probes
            on config/secret change and fails the save honestly (424). */}
        {!editing && (
          <Button type="button" variant="outline" onClick={runTest} disabled={testMutation.isPending}>
            {testMutation.isPending ? (
              <>
                <Loader2 className="animate-spin" /> {t("connections.testing")}
              </>
            ) : (
              t("connections.test")
            )}
          </Button>
        )}
        <Button type="submit" disabled={savePending}>
          {savePending
            ? editing
              ? t("connections.updating")
              : t("connections.saving")
            : editing
              ? t("connections.update")
              : t("connections.save")}
        </Button>
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t("action.cancel")}
          </Button>
        )}
      </div>
    </form>
  );
}

function TestResultBanner({ result }: { result: ConnectionTestResult }) {
  const ok = result.status === "OK";
  return (
    <div
      role="status"
      data-testid="test-result"
      data-status={result.status}
      className={cn(
        "flex items-center gap-2 rounded-md border p-3 text-sm",
        ok ? "border-[hsl(var(--success))] text-[hsl(var(--success))]" : "border-destructive text-destructive",
      )}
    >
      {ok ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
      {ok ? (
        <span>{t("connections.testOk", { ms: result.latencyMs ?? 0 })}</span>
      ) : (
        <span>
          {t("connections.testFailed")}
          {result.errorCategory ? ` — ${result.errorCategory}` : ""}
          {result.errorDetail ? `: ${result.errorDetail}` : ""}
        </span>
      )}
    </div>
  );
}

function Field({
  field,
  value,
  error,
  onChange,
}: {
  field: ConnectorField;
  value: string | boolean | undefined;
  error?: string;
  onChange: (v: string | boolean) => void;
}) {
  const id = `field-${field.name}`;
  const describedBy = error ? `${id}-error` : field.help ? `${id}-help` : undefined;
  const common = {
    id,
    "aria-invalid": !!error,
    "aria-describedby": describedBy,
  } as const;

  let widget: React.ReactNode;
  if (field.type === "boolean") {
    widget = (
      <input
        {...common}
        type="checkbox"
        checked={value === true}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 accent-[hsl(var(--primary))]"
      />
    );
  } else if (field.type === "enum" && field.enum) {
    widget = (
      <select
        {...common}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
      >
        {!field.required && <option value="">—</option>}
        {field.enum.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  } else if (field.type === "object" || field.type === "array") {
    widget = (
      <Textarea
        {...common}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder="{ }"
        className="font-mono text-xs"
      />
    );
  } else {
    widget = (
      <Input
        {...common}
        type={field.secret ? "password" : field.type === "integer" || field.type === "number" ? "number" : "text"}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={field.secret ? "new-password" : "off"}
      />
    );
  }

  const isCheckbox = field.type === "boolean";
  return (
    <div className={cn("space-y-1.5", isCheckbox && "flex items-center gap-2 space-y-0")}>
      <Label htmlFor={id} className={cn(isCheckbox && "order-2")}>
        {field.name}
        {field.required && <span className="ml-0.5 text-destructive">*</span>}
        {field.secret && (
          <Badge variant="secondary" className="ml-2 align-middle text-[10px]">
            secret
          </Badge>
        )}
      </Label>
      {widget}
      {field.help && !error && (
        <p id={`${id}-help`} className="text-xs text-muted-foreground">
          {field.help}
          {field.secret ? ` ${t("connections.secretHint")}` : ""}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
