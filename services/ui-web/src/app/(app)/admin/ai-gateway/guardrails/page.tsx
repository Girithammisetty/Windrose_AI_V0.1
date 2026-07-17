"use client";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, Textarea, Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useAiGuardrailPolicy, usePutAiGuardrailPolicy } from "@/lib/graphql/hooks";

export default function AiGuardrailsPage() {
  const query = useAiGuardrailPolicy();
  const put = usePutAiGuardrailPolicy();
  const [text, setText] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);

  useEffect(() => {
    if (query.data) setText(JSON.stringify(query.data.policy, null, 2));
  }, [query.data]);

  const save = () => {
    setJsonError(null);
    let policy: unknown;
    try {
      policy = JSON.parse(text);
    } catch (e) {
      setJsonError((e as Error).message);
      return;
    }
    put.mutate(policy);
  };

  return (
    <div>
      <PageHeader title="Guardrail policy" description="PII redaction, prompt-injection classification, and output-schema validation rules." />

      <AsyncBoundary isLoading={query.isLoading} isError={query.isError} error={query.error} onRetry={() => query.refetch()}>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-sm">Current policy {query.data ? <Badge variant="secondary" className="ml-2">v{query.data.version}</Badge> : null}</CardTitle>
              <CardDescription>
                Fields: pii.mode (redact|block|off), injection.mode (block|flag|off), schema_validation (on|off).
                Setting pii.mode to &quot;off&quot; requires the platform-operator scope server-side.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={16} className="font-mono text-xs" />
            {jsonError && <p className="text-xs text-destructive">{jsonError}</p>}
            {put.error && <p className="text-xs text-destructive">{put.error.message}</p>}
            <Can gate={FEATURE_GATES.manageAiGuardrails}>
              <Button disabled={put.isPending} onClick={save}>Save policy</Button>
            </Can>
          </CardContent>
        </Card>
      </AsyncBoundary>
    </div>
  );
}
