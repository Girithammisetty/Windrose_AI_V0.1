"use client";
import { PlugZap } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";

/**
 * Honest "not yet wired" panel (BR-15). Rendered for screens whose backing
 * bff-graphql operation is not in the current schema. We never fabricate rows —
 * we name the exact operation the screen needs and describe its intended UX so
 * the gap is auditable rather than hidden behind fake data.
 */
export function NotWiredPanel({
  title,
  operation,
  description,
}: {
  title: string;
  operation: string;
  description: string;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-start gap-3 space-y-0">
        <div className="mt-0.5 rounded-md bg-muted p-2 text-muted-foreground">
          <PlugZap className="size-5" aria-hidden />
        </div>
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-sm">{title}</CardTitle>
            <Badge variant="warning">Not enabled</Badge>
          </div>
          <CardDescription>{description}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border border-dashed bg-muted/40 p-3 text-sm">
          <p className="text-muted-foreground">
            Requires bff-graphql operation:{" "}
            <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs text-foreground">
              {operation}
            </code>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            This view activates automatically once the operation is present in the schema
            (services/bff-graphql/src/schema/typeDefs.ts).
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
