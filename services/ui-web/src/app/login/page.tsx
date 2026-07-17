"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label } from "@/components/ui/primitives";
import { t } from "@/lib/i18n/messages";
import { WindroseLogo } from "@/components/brand/WindroseLogo";
import Link from "next/link";

/**
 * Dev login (AUTH_MODE=dev). Posts to /api/auth/login which mints a real RS256
 * user JWT into an httpOnly cookie. In production this screen initiates the OIDC
 * code+PKCE redirect to Keycloak instead.
 */
export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

/** The seeded demo personas (deploy/local/seed_platform.py). Offered as
 * quick-pick chips on the dev login so testers don't have to remember the exact
 * emails — the default is a real, provisioned persona (was a dead @acme.com
 * address that failed sign-in). */
const DEMO_PERSONAS = [
  { label: "Adjuster", email: "adjuster@demo.windrose" },
  { label: "Manager", email: "manager@demo.windrose" },
  { label: "Data scientist", email: "datascientist@demo.windrose" },
  { label: "Admin", email: "admin@demo.windrose" },
];

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState(DEMO_PERSONAS[0].email);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
    });
    setPending(false);
    if (!res.ok) {
      // Surface the server's reason (e.g. 403 "unknown user" when the email is
      // not a provisioned persona) instead of a silent generic failure.
      const body = (await res.json().catch(() => null)) as { error?: string } | null;
      setError(body?.error ? `Sign-in failed: ${body.error}` : "Sign-in failed.");
      return;
    }
    router.replace(params.get("next") || "/");
    router.refresh();
  }

  return (
    <main id="main" className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="mb-1 flex items-center gap-2.5">
            <WindroseLogo className="size-9" />
            <CardTitle className="text-2xl">{t("app.name")}</CardTitle>
          </div>
          <CardDescription>{t("app.tagline")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {DEMO_PERSONAS.map((p) => (
                <button
                  key={p.email}
                  type="button"
                  onClick={() => setEmail(p.email)}
                  aria-pressed={email === p.email}
                  className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent aria-pressed:border-primary aria-pressed:bg-primary/10 aria-pressed:text-primary"
                >
                  {p.label}
                </button>
              ))}
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? t("state.loading") : t("action.signIn")}
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              New here?{" "}
              <Link href="/welcome" className="underline underline-offset-2 hover:text-foreground">
                See what Windrose AI does
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
