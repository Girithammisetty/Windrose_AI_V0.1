"use client";
import { useState } from "react";
import { FileCheck2, ShieldCheck, Download } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent, Badge, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useGenerateSoc2Pack, useGenerateAiDecisionLog, useComplianceOperation,
  useVerifyChainIntegrity,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";

const todayIso = () => new Date().toISOString().slice(0, 10);
const monthAgoIso = () => new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);

/**
 * Compliance pack generation (SOC2 / AI decision log) + real chain-integrity
 * verification (audit-service). Both are real backend calls: packs are async
 * jobs polled via complianceOperation, and verify replays the real hash chain
 * — a 409 (day not sealed yet) is surfaced verbatim, never faked as pass/fail.
 */
export function AuditComplianceCard() {
  return (
    <div className="mb-4 grid gap-4 lg:grid-cols-2">
      <CompliancePackPanel />
      <ChainVerifyPanel />
    </div>
  );
}

function CompliancePackPanel() {
  const [from, setFrom] = useState(monthAgoIso());
  const [to, setTo] = useState(todayIso());
  const [agentId, setAgentId] = useState("");
  const [operationId, setOperationId] = useState<string | null>(null);
  const soc2 = useGenerateSoc2Pack();
  const aiLog = useGenerateAiDecisionLog();
  const status = useComplianceOperation(operationId, {
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  const error = (soc2.error ?? aiLog.error) instanceof GraphQLRequestError ? (soc2.error ?? aiLog.error) as GraphQLRequestError : null;
  const pending = soc2.isPending || aiLog.isPending;

  const rfc3339 = (d: string, end = false) => `${d}T${end ? "23:59:59" : "00:00:00"}Z`;

  return (
    <Can gate={FEATURE_GATES.generateCompliancePack} fallback={null}>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm"><FileCheck2 className="size-4" aria-hidden />Compliance packs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">From</span>
              <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From date" className="h-8 text-xs" />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">To</span>
              <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="To date" className="h-8 text-xs" />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Agent id (AI log only, optional)</span>
              <Input value={agentId} onChange={(e) => setAgentId(e.target.value)} aria-label="Agent id" className="h-8 w-40 text-xs" />
            </label>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm" disabled={pending}
              onClick={() =>
                soc2.mutate(
                  { from: rfc3339(from), to: rfc3339(to, true) },
                  { onSuccess: (r) => setOperationId(r.operationId) },
                )
              }
            >
              Generate SOC2 pack
            </Button>
            <Button
              size="sm" variant="outline" disabled={pending}
              onClick={() =>
                aiLog.mutate(
                  { from: rfc3339(from), to: rfc3339(to, true), agentId: agentId.trim() || undefined },
                  { onSuccess: (r) => setOperationId(r.operationId) },
                )
              }
            >
              Generate AI decision log
            </Button>
          </div>
          {error && <p className="text-xs text-destructive">{error.message}</p>}

          {operationId && (
            <div className="rounded-md border p-2 text-xs">
              <p className="flex items-center gap-2">
                operation <span className="font-mono">{operationId}</span>{" "}
                <Badge variant={status.data?.status === "succeeded" ? "success" : status.data?.status === "failed" ? "destructive" : "warning"}>
                  {status.data?.status ?? "…"}
                </Badge>
              </p>
              {status.data?.error && <p className="mt-1 text-destructive">{status.data.error}</p>}
              {status.data?.resultUrl && (
                <a
                  href={status.data.resultUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 flex items-center gap-1 text-primary hover:underline"
                >
                  <Download className="size-3" aria-hidden />Download pack
                </a>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </Can>
  );
}

function ChainVerifyPanel() {
  const [date, setDate] = useState(() => {
    const d = new Date(Date.now() - 86_400_000);
    return d.toISOString().slice(0, 10);
  });
  const [tenantId, setTenantId] = useState("");
  const verify = useVerifyChainIntegrity();
  const error = verify.error instanceof GraphQLRequestError ? verify.error : null;

  return (
    <Can gate={FEATURE_GATES.verifyChainIntegrity} fallback={null}>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm"><ShieldCheck className="size-4" aria-hidden />Chain integrity verify</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Day</span>
              <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Verify day" className="h-8 text-xs" />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Tenant id (breakglass only, optional)</span>
              <Input value={tenantId} onChange={(e) => setTenantId(e.target.value)} aria-label="Tenant id" className="h-8 w-56 text-xs" />
            </label>
            <Button
              size="sm" disabled={verify.isPending}
              onClick={() => verify.mutate({ date, tenantId: tenantId.trim() || undefined })}
            >
              Verify
            </Button>
          </div>
          {error && (
            <p className="text-xs text-destructive">
              {error.message}
              {error.code === "CONFLICT" ? " — this day isn't sealed yet; verify after the WORM export seals it." : ""}
            </p>
          )}
          {verify.data && (
            <div className="rounded-md border p-2 text-xs">
              <p className="flex items-center gap-2">
                <Badge variant={verify.data.valid ? "success" : "destructive"}>
                  {verify.data.valid ? "VALID" : "INVALID"}
                </Badge>
                <span className="text-muted-foreground">{verify.data.eventsChecked} events checked</span>
              </p>
              <p className="mt-1 font-mono text-muted-foreground">chain head {verify.data.chainHead}</p>
              <p className="mt-1 text-muted-foreground">
                manifest match {String(verify.data.manifestMatch)}
                {verify.data.firstMismatchSeq != null && ` · first mismatch at seq ${verify.data.firstMismatchSeq}`}
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </Can>
  );
}
