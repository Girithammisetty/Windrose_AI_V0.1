"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardContent, Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateSemanticModel } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { t } from "@/lib/i18n/messages";

export default function NewSemanticModelPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const createMutation = useCreateSemanticModel();

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError(t("semantic.nameRequired"));
      return;
    }
    createMutation.mutate(
      { name: name.trim(), description: description.trim() || undefined },
      {
        onSuccess: (m) => router.push(`/data/semantic-models/${m.id}`),
        onError: (e) => setError(e instanceof GraphQLRequestError ? e.message : "Failed to create model"),
      },
    );
  };

  return (
    <div>
      <PageHeader
        title={t("semantic.new")}
        actions={
          <Button variant="ghost" size="sm" onClick={() => router.push("/data/semantic-models")}>
            <ArrowLeft /> {t("semantic.back")}
          </Button>
        }
      />

      <Card className="max-w-lg">
        <CardContent className="pt-4">
          <form className="space-y-4" onSubmit={onSubmit} aria-label={t("semantic.new")}>
            <div className="space-y-1.5">
              <Label htmlFor="model-name">{t("semantic.name")}</Label>
              <Input
                id="model-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("semantic.namePlaceholder")}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="model-description">{t("semantic.description")}</Label>
              <Textarea
                id="model-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive" data-testid="create-error">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => router.push("/data/semantic-models")}>
                {t("action.cancel")}
              </Button>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? t("semantic.creating") : t("semantic.new")}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
