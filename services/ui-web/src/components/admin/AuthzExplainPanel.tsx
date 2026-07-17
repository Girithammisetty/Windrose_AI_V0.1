"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { Card, CardTitle, CardContent, Badge, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useExplainAuthz } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";

/**
 * A small "why was I denied" debug tool (rbac-service POST /authz/explain).
 * Collapsed by default — this is a debug aid for admins/support, not a
 * flagship page. Gated on audit.log.read (the real permission the route
 * requires); tenant is always the caller's own verified token tenant, so
 * this only ever explains decisions within the admin's own tenant.
 */
export function AuthzExplainPanel() {
  const [open, setOpen] = useState(false);
  const [userId, setUserId] = useState("");
  const [action, setAction] = useState("");
  const [resourceUrn, setResourceUrn] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const explain = useExplainAuthz();
  const error = explain.error instanceof GraphQLRequestError ? explain.error : null;

  return (
    <Can gate={FEATURE_GATES.explainAuthz}>
      <Card>
        <button
          type="button"
          className="flex w-full items-center justify-between gap-2 p-4 text-left"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <CardTitle className="flex items-center gap-2 text-sm">
            <Search className="size-4" aria-hidden />
            Authz explain (why was I denied)
          </CardTitle>
          {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
        </button>
        {open && (
          <CardContent className="space-y-3 pt-0 text-sm">
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (!userId.trim() || !action.trim()) return;
                explain.mutate({
                  userId: userId.trim(), action: action.trim(),
                  resourceUrn: resourceUrn.trim() || undefined,
                  workspaceId: workspaceId.trim() || undefined,
                });
              }}
            >
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Subject user id</span>
                <Input value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="Subject user id" className="h-8 w-48 text-xs" />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Action</span>
                <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="case.case.read" aria-label="Action" className="h-8 w-48 text-xs" />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Resource URN (optional)</span>
                <Input value={resourceUrn} onChange={(e) => setResourceUrn(e.target.value)} aria-label="Resource URN" className="h-8 w-56 text-xs" />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Workspace id (optional)</span>
                <Input value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)} aria-label="Workspace id" className="h-8 w-40 text-xs" />
              </label>
              <Button type="submit" size="sm" disabled={!userId.trim() || !action.trim() || explain.isPending}>
                Explain
              </Button>
            </form>

            {error && <p role="alert" className="text-xs text-destructive">{error.message}</p>}

            {explain.data && (
              <div className="space-y-2 rounded-md border p-3">
                <p className="flex items-center gap-2">
                  <Badge variant={explain.data.allowed ? "success" : "destructive"}>
                    {explain.data.allowed ? "ALLOWED" : "DENIED"}
                  </Badge>
                  <span className="text-xs text-muted-foreground">{explain.data.reason}</span>
                </p>
                {explain.data.chain.length > 0 && (
                  <ol className="space-y-1 text-xs">
                    {explain.data.chain.map((step, i) => (
                      <li key={i} className="rounded border bg-muted/30 p-2 font-mono">
                        <span className="font-semibold">{step.type}</span>
                        {step.role && ` role=${step.role}`}
                        {step.group && ` group=${step.group}`}
                        {step.action && ` action=${step.action}`}
                        {step.workspace && ` workspace=${step.workspace}`}
                        {step.level && ` level=${step.level}`}
                        {step.admin != null && ` admin=${step.admin}`}
                        {step.detail && ` — ${step.detail}`}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}
          </CardContent>
        )}
      </Card>
    </Can>
  );
}
