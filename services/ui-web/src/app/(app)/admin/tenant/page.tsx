"use client";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Card, CardContent, CardHeader, CardTitle, Badge, Textarea, Label, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/session/SessionContext";
import { useTenant, useSetEmbedConfig, useTenantIdp, useSetTenantIdp, useDeleteTenantIdp } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { formatLocal } from "@/lib/utils";

export default function AdminTenantPage() {
  const { tenantId } = useSession();
  const query = useTenant(tenantId);
  const tenant = query.data;

  return (
    <div>
      <PageHeader
        title="Tenant settings"
        description="Tenant profile, isolation tier, and compute quotas (identity-service)."
      />
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!tenant}
        emptyTitle="Tenant not found."
        onRetry={() => query.refetch()}
      >
        {tenant && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="text-sm">Profile</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Row label="Display name" value={tenant.displayName || tenant.name} />
                <Row label="Name" value={tenant.name} mono />
                <Row label="Owner" value={tenant.ownerEmail || "—"} />
                <Row label="Subdomain" value={tenant.subdomain || "—"} />
                <div className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Status</span>
                  <StatusChip status={(tenant.status ?? "").toUpperCase() === "ACTIVE" ? "SUCCEEDED" : (tenant.status ?? "—").toUpperCase()} />
                </div>
                <Row label="Created" value={formatLocal(tenant.createdAt)} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle className="text-sm">Isolation &amp; platform</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Isolation tier</span>
                  <Badge variant="secondary">{tenant.tier || "—"}</Badge>
                </div>
                <Row label="Cloud" value={tenant.cloud || "—"} />
                <Row label="Platform version" value={tenant.platformVersion || "—"} />
                <div className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Auto-upgrade</span>
                  <Badge variant={tenant.autoUpgrade ? "success" : "secondary"}>{tenant.autoUpgrade ? "on" : "off"}</Badge>
                </div>
                <div>
                  <p className="mb-1 text-muted-foreground">Modules</p>
                  <span className="flex flex-wrap gap-1">
                    {tenant.modules.length === 0 ? "—" : tenant.modules.map((m) => <Badge key={m} variant="secondary">{m}</Badge>)}
                  </span>
                </div>
              </CardContent>
            </Card>

            {tenant.quotas && (
              <Card className="lg:col-span-2">
                <CardHeader><CardTitle className="text-sm">Compute quotas</CardTitle></CardHeader>
                <CardContent className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <Row label="CPU" value={String(tenant.quotas.cpu ?? "—")} />
                  <Row label="Memory" value={tenant.quotas.memory || "—"} />
                  <Row label="Processing CPU" value={String(tenant.quotas.processingCpu ?? "—")} />
                  <Row label="Processing memory" value={tenant.quotas.processingMemory || "—"} />
                </CardContent>
              </Card>
            )}

            <EmbedConfigCard tenantId={tenant.id} configured={tenant.embedConfig?.configured ?? false}
              allowedOrigins={tenant.embedConfig?.allowedOrigins ?? []} updatedAt={tenant.embedConfig?.updatedAt ?? null} />

            <IdentityProviderCard />
          </div>
        )}
      </AsyncBoundary>

      <p className="mt-4 text-xs text-muted-foreground">
        Editing tenant settings (display name, quotas, auto-upgrade) is a super-admin operation in
        identity-service (PATCH /tenants/&#123;id&#125;) and is intentionally read-only here for a tenant admin.
      </p>
    </div>
  );
}

/**
 * Embedded-UI (iframe) configuration: allowed origins become the CSP
 * frame-ancestors of every embed of this tenant, and every request into
 * POST /token/embed must present the matching secret. The secret is shown
 * exactly once, right after (re)generation — identity-service stores only its
 * hash, so it can never be displayed again after this render.
 */
function EmbedConfigCard({
  tenantId, configured, allowedOrigins, updatedAt,
}: { tenantId: string; configured: boolean; allowedOrigins: string[]; updatedAt: string | null }) {
  const [originsText, setOriginsText] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);
  const mutation = useSetEmbedConfig(tenantId);
  const error = mutation.error instanceof GraphQLRequestError ? mutation.error : null;

  // Seed the editable textarea from the loaded config exactly once — after
  // that, refetches (e.g. after rotation) must not clobber an in-progress edit.
  useEffect(() => {
    if (originsText === null) setOriginsText(allowedOrigins.join("\n"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const parsedOrigins = (originsText ?? "").split("\n").map((s) => s.trim()).filter(Boolean);

  const rotate = () => {
    mutation.mutate(parsedOrigins, {
      onSuccess: (result) => {
        setRevealedSecret(result.embedSecret);
        setConfirmOpen(false);
      },
    });
  };

  return (
    <Card className="lg:col-span-2">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm">Embedding</CardTitle>
        <Badge variant={configured ? "success" : "secondary"}>{configured ? "configured" : "not configured"}</Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          Origins allowed to iframe this tenant&apos;s Windrose surfaces (dashboards, cases, copilot).
          Also bound into every embed session as its CSP frame-ancestors — a host not listed here
          cannot frame the surface even with a valid secret.
        </p>
        <div className="space-y-1.5">
          <Label htmlFor="embed-origins">Allowed origins (one per line)</Label>
          <Textarea
            id="embed-origins"
            rows={4}
            placeholder="https://portal.acme.example.com"
            value={originsText ?? ""}
            onChange={(e) => setOriginsText(e.target.value)}
          />
        </div>
        {updatedAt && <Row label="Last updated" value={formatLocal(updatedAt)} />}

        {revealedSecret ? (
          <div className="space-y-2 rounded-md border border-[hsl(var(--warning))] bg-[hsl(var(--warning)/0.08)] p-3">
            <p className="text-xs font-medium text-[hsl(var(--warning))]">
              Copy this secret now — it will not be shown again. Store it in the embedding host&apos;s
              own secret store; it authenticates every call to POST /token/embed.
            </p>
            <code className="block break-all rounded bg-background p-2 font-mono text-xs">{revealedSecret}</code>
            <Button size="sm" variant="outline" onClick={() => setRevealedSecret(null)}>Done</Button>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3">
            {error && <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">{error.message}</p>}
            <Button
              size="sm"
              variant={configured ? "outline" : "default"}
              disabled={mutation.isPending}
              onClick={() => (configured ? setConfirmOpen(true) : rotate())}
            >
              {mutation.isPending ? "Saving…" : configured ? "Rotate secret" : "Generate secret"}
            </Button>
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Rotate embed secret?"
        description="The current secret stops working immediately. Any embedding host still presenting it will get 401s from /token/embed until it's updated with the new one."
        confirmLabel="Rotate"
        onConfirm={rotate}
      />
    </Card>
  );
}

/**
 * Bring-your-own OIDC identity provider (BYO-P4). A tenant registers their own
 * Okta/Auth0/Entra/Keycloak here; an inbound ID token whose issuer matches then
 * routes to THIS tenant at login. The issuer is globally unique — SSO is off
 * for the tenant until configured, and turning it off is one click.
 */
function IdentityProviderCard() {
  const query = useTenantIdp();
  const cfg = query.data;
  const save = useSetTenantIdp();
  const remove = useDeleteTenantIdp();
  const error = (save.error ?? remove.error) instanceof GraphQLRequestError
    ? (save.error ?? remove.error) as GraphQLRequestError : null;

  const [issuer, setIssuer] = useState<string | null>(null);
  const [clientId, setClientId] = useState<string | null>(null);
  const [discoveryUrl, setDiscoveryUrl] = useState<string | null>(null);

  useEffect(() => {
    if (issuer === null && cfg) {
      setIssuer(cfg.issuer ?? "");
      setClientId(cfg.clientId ?? "");
      setDiscoveryUrl(cfg.discoveryUrl ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg]);

  const configured = cfg?.configured ?? false;
  const submit = () => {
    if (!issuer?.trim()) return;
    save.mutate({
      issuer: issuer.trim(),
      clientId: (clientId ?? "").trim() || undefined,
      discoveryUrl: (discoveryUrl ?? "").trim() || undefined,
      enabled: true,
    });
  };

  return (
    <Card className="lg:col-span-2">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm">Single sign-on (SSO)</CardTitle>
        <Badge variant={configured && cfg?.enabled ? "success" : "secondary"}>
          {configured ? (cfg?.enabled ? "enabled" : "disabled") : "not configured"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          Bring your own identity provider (Okta, Auth0, Entra ID, Keycloak — any OIDC IdP).
          Users sign in against your IdP; a token whose issuer matches yours routes to this tenant.
          The issuer must be unique across the platform.
        </p>
        <div className="space-y-1.5">
          <Label htmlFor="idp-issuer">Issuer URL</Label>
          <Input id="idp-issuer" placeholder="https://your-org.okta.com"
            value={issuer ?? ""} onChange={(e) => setIssuer(e.target.value)} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="idp-client">Client ID (optional)</Label>
            <Input id="idp-client" placeholder="windrose-web"
              value={clientId ?? ""} onChange={(e) => setClientId(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="idp-discovery">Discovery URL (optional)</Label>
            <Input id="idp-discovery" placeholder="defaults to issuer + /.well-known/openid-configuration"
              value={discoveryUrl ?? ""} onChange={(e) => setDiscoveryUrl(e.target.value)} />
          </div>
        </div>
        {cfg?.updatedAt && <Row label="Last updated" value={formatLocal(cfg.updatedAt)} />}
        {error && <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">{error.message}</p>}
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!issuer?.trim() || save.isPending} onClick={submit}>
            {save.isPending ? "Saving…" : configured ? "Update SSO" : "Enable SSO"}
          </Button>
          {configured && (
            <Button size="sm" variant="outline" disabled={remove.isPending}
              onClick={() => remove.mutate(undefined, { onSuccess: () => { setIssuer(""); setClientId(""); setDiscoveryUrl(""); } })}>
              {remove.isPending ? "Removing…" : "Turn off SSO"}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? "truncate font-mono text-xs" : "font-medium"}>{value}</span>
    </div>
  );
}
