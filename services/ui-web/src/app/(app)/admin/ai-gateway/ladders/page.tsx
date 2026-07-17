"use client";
import { useEffect, useState } from "react";
import { Layers } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, Textarea, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useAiLadder, usePutAiLadder } from "@/lib/graphql/hooks";

const REQUEST_CLASSES = ["chat", "sql-gen", "judge", "embed"];

export default function AiLaddersPage() {
  const [requestClass, setRequestClass] = useState("chat");
  const ladder = useAiLadder(requestClass);
  const put = usePutAiLadder();
  const [rungsText, setRungsText] = useState("");
  const [maxRung, setMaxRung] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);

  useEffect(() => {
    if (ladder.data) {
      setRungsText(JSON.stringify(ladder.data.rungs, null, 2));
      setMaxRung(ladder.data.maxRung != null ? String(ladder.data.maxRung) : "");
    }
  }, [ladder.data]);

  const save = () => {
    setJsonError(null);
    let rungs: unknown;
    try {
      rungs = JSON.parse(rungsText);
    } catch (e) {
      setJsonError((e as Error).message);
      return;
    }
    if (!Array.isArray(rungs)) {
      setJsonError("rungs must be a JSON array");
      return;
    }
    put.mutate({ requestClass, rungs, maxRung: maxRung.trim() ? Number(maxRung) : undefined, scope: "platform" });
  };

  return (
    <div>
      <PageHeader title="Routing ladders" description="Ordered model rungs per request class (chat, sql-gen, judge, embed)." />

      <div className="mb-4 flex gap-2">
        {REQUEST_CLASSES.map((rc) => (
          <Button key={rc} variant={rc === requestClass ? "default" : "outline"} size="sm" onClick={() => setRequestClass(rc)}>
            {rc}
          </Button>
        ))}
      </div>

      <AsyncBoundary isLoading={ladder.isLoading} isError={ladder.isError} error={ladder.error} onRetry={() => ladder.refetch()}>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{requestClass} ladder{ladder.data ? ` — v${ladder.data.version}` : ""}</CardTitle>
            <CardDescription>Rungs are tried in order; each has a model_alias, max_tokens, temperature_default, cost_tier.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="ladder-rungs">Rungs (JSON array)</Label>
              <Textarea id="ladder-rungs" value={rungsText} onChange={(e) => setRungsText(e.target.value)} rows={12} className="font-mono text-xs" />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="ladder-max-rung">Max rung (tenant cap, optional)</Label>
              <input
                id="ladder-max-rung" type="number" min="0" value={maxRung} onChange={(e) => setMaxRung(e.target.value)}
                className="h-9 w-32 rounded-md border border-input bg-background px-2 text-sm"
              />
            </div>
            {jsonError && <p className="text-xs text-destructive">{jsonError}</p>}
            {put.error && <p className="text-xs text-destructive">{put.error.message}</p>}
            <Can gate={FEATURE_GATES.manageAiLadders}>
              <Button disabled={put.isPending} onClick={save}>Save ladder</Button>
            </Can>
          </CardContent>
        </Card>
      </AsyncBoundary>

      {!ladder.data && !ladder.isLoading && (
        <div className="mt-8 flex flex-col items-center gap-2 text-muted-foreground">
          <Layers className="size-8" />
          <p>No ladder configured for this request class.</p>
        </div>
      )}
    </div>
  );
}
